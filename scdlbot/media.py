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
    short: bool = False  # a YouTube Short (yt-dlp's media_type), for metrics
    silent: bool = False  # a video that came without sound from every route (the site muted it)

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


async def has_audio(path: Path) -> bool:
    """True if the file has a sound track, or ffprobe can't tell."""
    streams = (await probe(path)).get("streams")
    return streams is None or any(stream.get("codec_type") == "audio" for stream in streams)


def display_size(stream: dict) -> tuple[int | None, int | None]:
    """Width and height as players show the video: phones record sideways and store the rotation next to it."""
    rotation = next((data["rotation"] for data in stream.get("side_data_list", []) if "rotation" in data), 0)
    width, height = stream.get("width"), stream.get("height")
    return (height, width) if abs(int(rotation)) % 180 == 90 else (width, height)


async def fill_video_info(item: Item) -> Item:
    """Some formats come without codec, size or duration (Instagram's progressive mp4s), so get them from the file.
    Telegram takes the size from the request, and iOS squeezes a video without one into a square."""
    if item.vcodec and item.width and item.height and item.duration:
        return item
    info = await probe(item.path)
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video:
        return item
    width, height = display_size(video)
    duration = item.duration or round(float(info.get("format", {}).get("duration") or 0)) or None
    return replace(item, vcodec=item.vcodec or video.get("codec_name"), width=item.width or width, height=item.height or height, duration=duration)


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
    """Telegram iOS can't play VP9 and chokes on HEVC, so re-encode anything that isn't h264.
    One CPU core re-encodes 480p about 8 times faster than it plays (hetbot, 2026-11): 30 minutes take ~4 minutes.
    Videos longer than MAX_REENCODE_DURATION are sent as they are: they play everywhere but on iOS."""
    if item.vcodec and item.vcodec.startswith(("avc1", "h264")):
        return item
    if (item.duration or 0) > cfg.max_reencode_duration:
        log.info("Not re-encoding %s (%s): longer than %d s", item.path.name, item.vcodec, cfg.max_reencode_duration)
        return item
    info = await probe(item.path)
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video or video.get("codec_name") == "h264":
        return item
    codec = str(video.get("codec_name"))
    log.info("Re-encoding %s (%s %sx%s) to h264", item.path.name, codec, video.get("width"), video.get("height"))
    output = item.path.with_name(item.path.stem + ".h264.mp4")
    try:
        code, _ = await run(
            "ffmpeg", "-v", "error", "-y", "-i", str(item.path),
            "-c:v", "libx264", "-crf", "24", "-preset", "veryfast", "-c:a", "copy", "-movflags", "+faststart", "-threads", str(cfg.ffmpeg_threads),
            str(output), timeout=max(cfg.dl_timeout, item.duration or 0),  # up to the video's duration: plenty, even for 720p
        )  # fmt: skip
        result = "failed" if code else "ok"
    except TimeoutError:
        result = "timeout"
    observe.REENCODES.labels(codec, result).inc()
    if result != "ok":
        return item  # send it as is, better than nothing
    item.path.unlink(missing_ok=True)
    width, height = display_size(video)  # ffmpeg applies the rotation while re-encoding
    return replace(item, path=output, vcodec="h264", width=width, height=height)


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
