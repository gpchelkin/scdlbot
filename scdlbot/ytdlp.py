"""Run yt-dlp as a subprocess and stream downloaded files as soon as each one is ready.

The exact command is logged, so any failure can be reproduced by hand in a shell.
"""

import asyncio
import json
import logging
import os
import shlex
import shutil
import sys
from collections import deque
from collections.abc import AsyncIterator
from pathlib import Path

import httpx

from scdlbot.config import Config
from scdlbot.links import PHOTO, VIDEO, clean_url, site_by_name
from scdlbot.media import Item, kill, oom_first

log = logging.getLogger(__name__)

# Keep m4a (AAC) and mp3 as they are, convert anything else (opus, vorbis...) to mp3:
AUDIO_ARGS = ["-f", "ba[ext=m4a]/ba[ext=mp3]/ba/b", "-x", "--audio-format", "m4a>m4a/mp3>mp3/mp3", "--audio-quality", "0", "--embed-metadata", "--embed-thumbnail"]
# "Always MP3" setting, for players that don't know m4a: the best audio, converted to VBR V2 mp3 (~200 kbps on average).
# LAME picks the bitrate per frame; the sources are 128-160 kbps AAC/Opus, so CBR 320 would only make files ~1.6x bigger.
MP3_ARGS = ["-f", "ba/b", "-x", "--audio-format", "mp3", "--audio-quality", "2", "--embed-metadata", "--embed-thumbnail"]
# Prefer h264 + AAC in mp4, which plays everywhere in Telegram:
VIDEO_ARGS = ["-S", "vcodec:h264,res:720,acodec:aac,ext:mp4:m4a", "--merge-output-format", "mp4"]
# Info printed as one JSON line per finished file:
FIELDS = (
    "filepath,title,track,artist,uploader,uploader_id,channel,duration,width,height,vcodec,description,playlist_index,playlist_count,webpage_url,playlist_webpage_url,media_type"
)
# Posts with photos (Instagram): yt-dlp skips entries without video, but with these options it still saves
# their image as the entry's thumbnail, and prints where (before giving up on the entry with "No video formats found").
# No --convert-thumbnails: yt-dlp would convert webp/heic photos only after printing their path, and delete that file;
# media.ensure_jpeg converts them instead.
PHOTO_ARGS = ["--ignore-no-formats-error", "--write-thumbnail", "-O", f"before_dl:%(.{{format_id,__files_to_move,{FIELDS}}})j"]

# Error codes by yt-dlp output patterns (lowercase), first match wins. Each code has a text in texts/error_<code>.html.
ERRORS = (
    ("too_large", ("larger than max-filesize",)),
    ("drm", ("drm protected",)),
    ("no_media", ("no video could be found in this tweet", "is not a video")),  # if Twitter's embed endpoint couldn't give photos or text either
    ("live", ("live event", "is live", "premieres in", "is_live", "live stream")),
    ("region", ("not available in your country", "not made this video available in your country", "geo restrict", "blocked it in your country", "from your location")),
    # Age or audience limits (YouTube "confirm your age", Instagram "can't be seen by certain audiences"), before "login":
    ("restricted", ("age-restricted", "inappropriate for some users", "confirm your age", "certain audiences", "isn't available to everyone")),
    # The site limits or blocks our IP: bot checks, rate limits ("This content isn't available, try again later.
    # Your account has been rate-limited by YouTube for up to an hour"), HTTP 403/429. Before "login" and "unavailable":
    (
        "blocked",
        ("not a bot", "rate-limited", "rate limit", "try again later", "too many requests", "http error 429", "http error 403", "ip address is blocked"),
    ),
    # Can't reach the site through this route: no IPv6 address, proxy down, connection refused or reset:
    (
        "network",
        (
            "failed to resolve",
            "name or service not known",
            "temporary failure in name resolution",
            "network is unreachable",
            "connection refused",
            "connection reset",
            "unable to connect to proxy",
            "proxyerror",
            "could not bind",
            "couldn't connect",
        ),
    ),
    ("login", ("sign in to", "login required", "--cookies", "requires authentication", "private video")),
    (
        "unavailable",
        ("unavailable", "isn't available", "has been removed", "http error 404", "does not exist", "not found", "no video formats", "requested format is not available"),
    ),
    ("unsupported", ("unsupported url",)),
)


class DownloadError(Exception):
    def __init__(self, code: str, details: str = ""):
        super().__init__(f"{code}: {details}" if details else code)
        self.code = code


def classify_error(lines: list[str]) -> str:
    text = "\n".join(line for line in lines if line.startswith("ERROR") or "max-filesize" in line).lower()
    for code, patterns in ERRORS:
        if any(p in text for p in patterns):
            return code
    return "failed"


class Cookies:
    """A local cookies.txt, a URL to download it from (to download_to), or firefox:PROFILE[:URL_OF_COOKIES_SQLITE].

    PROFILE is the path of a Firefox profile directory, or a profile name in ~/.mozilla/firefox.
    """

    def __init__(self, spec: str, download_to: Path):
        self.url = None
        self.file: Path | None = None
        self.browser = None
        if spec.startswith("firefox:"):
            _, profile, *url = spec.split(":", 2)
            self.browser = f"firefox:{profile}"
            directory = Path(profile) if Path(profile).is_absolute() else Path.home() / ".mozilla" / "firefox" / profile
            self.file = directory / "cookies.sqlite"
            self.url = url[0] if url else None
        elif spec.startswith(("http://", "https://")):
            self.url = spec
            self.file = download_to
        elif spec:
            self.file = Path(spec).expanduser()

    async def refresh(self, client: httpx.AsyncClient) -> None:
        if not (self.url and self.file):
            return
        try:
            response = await client.get(self.url, follow_redirects=True, timeout=30)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("Could not refresh cookies: %s", exc)
            return
        self.file.parent.mkdir(parents=True, exist_ok=True)
        temp = self.file.with_suffix(".tmp")
        temp.write_bytes(response.content)
        temp.replace(self.file)
        log.info("Cookies refreshed: %s", self.file)

    def args(self, job_dir: Path) -> list[str]:
        if not (self.file and self.file.exists()):
            return []  # not downloaded (yet): go without cookies rather than fail every download
        if self.browser:
            return ["--cookies-from-browser", self.browser]
        # yt-dlp writes cookies back on exit, so each run gets its own copy:
        shutil.copyfile(self.file, job_dir / "cookies.txt")
        return ["--cookies", str(job_dir / "cookies.txt")]


def build_command(url: str, kind: str, job_dir: Path, cfg: Config, extra: list[str], photos: bool = False, mp3: bool = False) -> list[str]:
    return [
        sys.executable, "-m", "yt_dlp", "--ignore-config", url,
        "-P", str(job_dir), "-o", "%(playlist_index|)03d%(playlist_index&_|)s%(title).80B [%(id)s].%(ext)s",
        "-O", f"after_move:%(.{{{FIELDS}}})j", "--no-quiet", "--no-progress", "--no-playlist",
        "-I", f"1:{cfg.max_playlist_items}", "--max-filesize", str(cfg.max_convert_file_size), "--cache-dir", str(cfg.cache_dir),
        "--ppa", f"ffmpeg:-threads {cfg.ffmpeg_threads}",
        *(VIDEO_ARGS if kind == VIDEO else MP3_ARGS if mp3 else AUDIO_ARGS), *(PHOTO_ARGS if photos else ()), *extra, *cfg.ytdlp_args,
    ]  # fmt: skip


def _remember(text: str, tail: deque[str], errors: list[str]) -> None:
    tail.append(text)
    if text.startswith("ERROR") and len(errors) < 100:
        errors.append(text)
    log.debug("yt-dlp: %s", text)


async def _collect(stream: asyncio.StreamReader, tail: deque[str], errors: list[str]) -> None:
    async for line in stream:
        _remember(line.decode(errors="replace").rstrip(), tail, errors)


async def download(url: str, kind: str, job_dir: Path, cfg: Config, extra: list[str] = (), photos: bool = False, mp3: bool = False) -> AsyncIterator[dict]:
    """Yield yt-dlp info (with "filepath") for every finished file; raise DownloadError if nothing or not everything was downloaded.

    With photos=True, photos are yielded too, with "_photo": True.
    Use with contextlib.aclosing(), so the process is killed as soon as the caller stops iterating.
    """
    command = build_command(url, kind, job_dir, cfg, list(extra), photos, mp3)
    log.info("Running: %s", shlex.join(command))
    process = await asyncio.create_subprocess_exec(
        *command, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        # TMPDIR: temp files (e.g. yt-dlp's copy of the Firefox cookie DB) go to the job dir, deleted even if yt-dlp is killed.
        # DENO_DIR: deno's cache (yt-dlp runs deno for YouTube), next to yt-dlp's own, instead of ~/.cache.
        start_new_session=True, limit=1 << 20,
        env=os.environ | {"PYTHONUNBUFFERED": "1", "TMPDIR": str(job_dir), "DENO_DIR": str(cfg.cache_dir / "deno")},
    )  # fmt: skip
    oom_first(process)
    tail: deque[str] = deque(maxlen=50)
    errors: list[str] = []
    stderr = asyncio.create_task(_collect(process.stderr, tail, errors))
    count = photo_count = 0
    try:
        while True:
            try:
                async with asyncio.timeout(cfg.dl_timeout):  # restarts for every item
                    line = await process.stdout.readline()
            except TimeoutError:
                raise DownloadError("timeout") from None
            if not line:
                break
            text = line.decode(errors="replace").rstrip()
            if text.startswith("{"):
                info = json.loads(text)
                if "filepath" not in info:  # printed before a download (PHOTO_ARGS): only entries without video matter
                    if info.get("format_id") or not info.get("__files_to_move"):
                        continue
                    info["filepath"] = next(iter(info.pop("__files_to_move").values()))
                    info["_photo"] = True
                    photo_count += 1
                count += 1
                yield info
            else:
                _remember(text, tail, errors)
        await stderr
        code = await process.wait()
    finally:
        kill(process)
        stderr.cancel()
    if photo_count:  # every saved photo also counts as a failed entry for yt-dlp
        errors = [line for line in errors if "No video formats found" not in line]
    if (errors if photo_count else code) or not count:
        raise DownloadError(classify_error(errors or list(tail)), "; ".join(errors)[-500:])


def to_item(info: dict, kind: str, site: str | None = None) -> Item:
    title = info.get("track") or info.get("title")
    performer = info.get("artist")
    if not performer and title and " - " in title:  # "Artist - Title", common on SoundCloud and YouTube
        performer, title = title.split(" - ", 1)
    uploader = info.get("uploader") or info.get("channel")
    duration = info.get("duration")
    author_name, author_handle, author_url = uploader, None, None
    if (known := site_by_name(site or "")) and known.author:
        author_name = info.get(known.author.name)
        author_handle = str(info.get(known.author.handle) or "").lstrip("@") or None
        author_url = known.author.url.format(author_handle) if author_handle else None
    source_url, playlist_url = info.get("webpage_url"), info.get("playlist_webpage_url")
    return Item(
        path=Path(info["filepath"]),
        kind=PHOTO if info.get("_photo") else kind,
        title=title,
        performer=performer or uploader,
        duration=round(duration) if duration else None,
        width=info.get("width"),
        height=info.get("height"),
        vcodec=info.get("vcodec"),
        author_name=author_name,
        author_handle=author_handle,
        author_url=author_url,
        source_url=clean_url(source_url, site or "", author_handle) if source_url else None,
        playlist_url=clean_url(playlist_url, site or "", author_handle) if playlist_url else None,
        description=info.get("description"),
        playlist_index=info.get("playlist_index"),
        playlist_count=info.get("playlist_count"),
    )
