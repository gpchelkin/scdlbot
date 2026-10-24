"""Run ffmpeg/ffprobe: probe files, re-encode video to h264, split files too big for Telegram."""

import asyncio
import contextlib
import json
import logging
import math
import os
import signal
from dataclasses import dataclass, replace
from pathlib import Path

from scdlbot import observe
from scdlbot.config import Config

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Item:
    """One downloaded file, ready to send."""

    path: Path
    kind: str  # audio | video
    title: str | None = None
    performer: str | None = None
    duration: int | None = None
    width: int | None = None
    height: int | None = None
    vcodec: str | None = None
    author_name: str | None = None  # for videos and photos from social networks
    author_handle: str | None = None
    author_url: str | None = None
    source_url: str | None = None  # the post or track, cleaned up (links.clean_url)
    playlist_url: str | None = None  # the playlist, album or carousel it's from
    description: str | None = None
    playlist_index: int | None = None
    playlist_count: int | None = None
    part: tuple[int, int] | None = None  # (number, total) when split

    @property
    def size(self) -> int:
        return self.path.stat().st_size


def oom_first(process: asyncio.subprocess.Process) -> None:
    """Out of memory, the kernel should kill this download rather than the bot. Its children (ffmpeg, deno) inherit it."""
    with contextlib.suppress(OSError):
        Path(f"/proc/{process.pid}/oom_score_adj").write_text("500")


def kill(process: asyncio.subprocess.Process) -> None:
    """Kill a process started with start_new_session=True, together with its children (e.g. ffmpeg under yt-dlp)."""
    if process.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)


async def run(*cmd: str, timeout: float) -> tuple[int, str]:
    """Run a command, return (exit code, stdout). Kills it on timeout or cancellation."""
    process = await asyncio.create_subprocess_exec(*cmd, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True)
    oom_first(process)
    try:
        async with asyncio.timeout(timeout):
            stdout, stderr = await process.communicate()
    finally:
        kill(process)
    if process.returncode:
        log.warning("%s failed (%s): %s", cmd[0], process.returncode, stderr.decode(errors="replace")[-1000:])
    return process.returncode or 0, stdout.decode(errors="replace")


async def probe(path: Path) -> dict:
    code, out = await run("ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path), timeout=60)
    return json.loads(out) if code == 0 and out else {}


async def ensure_jpeg(item: Item, cfg: Config) -> Item:
    """Telegram takes JPEG and PNG photos; Instagram also serves WebP and HEIC."""
    if item.path.suffix.lower() in (".jpg", ".jpeg", ".png"):
        return item
    output = item.path.with_suffix(".jpg")
    code, _ = await run("ffmpeg", "-v", "error", "-y", "-i", str(item.path), "-q:v", "2", "-threads", str(cfg.ffmpeg_threads), str(output), timeout=60)
    if code or not output.exists():
        return item  # try sending it as it is
    item.path.unlink(missing_ok=True)
    return replace(item, path=output)


async def ensure_h264(item: Item, cfg: Config) -> Item:
    """Telegram iOS can't play VP9 and chokes on HEVC, so re-encode anything that isn't h264."""
    if item.vcodec and item.vcodec.startswith(("avc1", "h264")):
        return item
    info = await probe(item.path)
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video or video.get("codec_name") == "h264":
        return item
    codec = str(video.get("codec_name"))
    log.info("Re-encoding %s (%s %sx%s) to h264", item.path.name, codec, video.get("width"), video.get("height"))
    output = item.path.with_name(item.path.stem + ".h264.mp4")
    code, _ = await run(
        "ffmpeg", "-v", "error", "-y", "-i", str(item.path),
        "-c:v", "libx264", "-crf", "24", "-preset", "veryfast", "-c:a", "copy", "-movflags", "+faststart", "-threads", str(cfg.ffmpeg_threads),
        str(output), timeout=cfg.dl_timeout,
    )  # fmt: skip
    observe.REENCODES.labels(codec, "failed" if code else "ok").inc()
    if code:
        return item  # send it as is, better than nothing
    item.path.unlink(missing_ok=True)
    return replace(item, path=output, vcodec="h264", width=video.get("width"), height=video.get("height"))


async def split(item: Item, cfg: Config) -> list[Item]:
    """Split a file into parts below MAX_TG_FILE_SIZE by duration, without re-encoding."""
    size = item.size
    if size <= cfg.max_tg_file_size:
        return [item]
    duration = item.duration or float((await probe(item.path)).get("format", {}).get("duration", 0))
    if not duration:
        return []
    count = math.ceil(size / (cfg.max_tg_file_size * 0.95))  # bitrate varies, keep some headroom
    length = duration / count
    parts = []
    for number in range(count):
        output = item.path.with_name(f"{item.path.stem}.part{number + 1}{item.path.suffix}")
        code, _ = await run(
            "ffmpeg", "-v", "error", "-y", "-ss", f"{number * length:.3f}", "-t", f"{length:.3f}", "-i", str(item.path),
            "-map", "0", "-c", "copy", "-map_metadata", "0", "-threads", str(cfg.ffmpeg_threads), str(output),
            timeout=cfg.dl_timeout,
        )  # fmt: skip
        if code or output.stat().st_size > cfg.max_tg_file_size:
            break
        parts.append(replace(item, path=output, duration=round(length), part=(number + 1, count)))
    item.path.unlink(missing_ok=True)
    return parts if len(parts) == count else []
