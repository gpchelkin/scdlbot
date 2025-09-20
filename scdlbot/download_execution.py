"""Download execution helpers shared between bot and worker processes."""

from __future__ import annotations

import asyncio
import logging
import os
import pathlib
import shutil
import tempfile
import threading
import time
import traceback
from dataclasses import dataclass
from subprocess import PIPE, TimeoutExpired  # skipcq: BAN-B404
from types import ModuleType
from typing import Any, Dict, Optional, Sequence, cast
from uuid import uuid4

import requests
from boltons.urlutils import URL
from mutagen.id3 import ID3  # type: ignore[attr-defined]
from mutagen.id3 import ID3v1SaveOptions  # type: ignore[attr-defined]
from mutagen.mp3 import EasyMP3 as MP3
from plumbum import ProcessExecutionError
from plumbum.machines.local import LocalCommand
from telegram import Bot
from telegram.constants import ChatAction
from telegram.helpers import escape_markdown
from telegram.request import HTTPXRequest

from scdlbot.ffmpeg_worker import FFmpegError, convert_video_to_audio, split_file
from scdlbot.ffprobe import FFprobeError, probe_media

logger = logging.getLogger(__name__)


def _load_downloader_module() -> ModuleType:
    for candidate in ("yt_dlp", "youtube_dl", "youtube_dlc"):
        try:
            return __import__(candidate)
        except ImportError:
            continue
    raise ImportError("No supported downloader module available")


ydl = cast(Any, _load_downloader_module())


AUDIO_FORMATS: Sequence[str] = ("mp3",)
VIDEO_FORMATS: Sequence[str] = ("m4a", "mp4", "webm")


class FileNotSupportedError(Exception):
    def __init__(self, file_format: str):
        self.file_format = file_format


class FileTooLargeError(Exception):
    def __init__(self, file_size: int):
        self.file_size = file_size


class FileSplittedPartiallyError(Exception):
    def __init__(self, file_parts: Sequence[str]):
        self.file_parts = list(file_parts)


class FileNotConvertedError(Exception):
    def __init__(self, message: str = ""):
        super().__init__(message)
        self.message = message


class FileSentPartiallyError(Exception):
    def __init__(self, sent_audio_ids: Sequence[str]):
        self.sent_audio_ids = list(sent_audio_ids)


@dataclass(frozen=True)
class DownloadContext:
    http_version: str
    dl_dir: str
    scdl_bin: LocalCommand
    bcdl_bin: LocalCommand
    bcdl_enable: bool
    dl_timeout: int
    max_tg_file_size: int
    max_convert_file_size: int
    failed_text: str
    dl_timeout_text: str
    common_connection_timeout: int
    domain_sc: str
    domain_sc_on: str
    domain_sc_api: str
    domain_sc_googl: str
    domain_bc: str
    domain_yt: str
    domain_yt_be: str
    domain_tt: str
    domain_tw: str
    domain_twx: str
    domain_ymc: str
    domain_ig: str
    audio_formats: Sequence[str]
    video_formats: Sequence[str]


_context: Optional[DownloadContext] = None


def configure_download_context(context: DownloadContext) -> None:
    """Configure global download context."""

    global _context
    _context = context


def get_download_context() -> Optional[DownloadContext]:
    """Return the configured download context if available."""

    return _context


def _require_download_context() -> DownloadContext:
    context = get_download_context()
    if context is None:
        raise RuntimeError("Download context is not configured")
    return context


def download_url_and_send(
    bot_options: Dict[str, Any],
    chat_id: int,
    url: str,
    flood: bool = False,
    reply_to_message_id: Optional[int] = None,
    wait_message_id: Optional[int] = None,
    cookies_file: Optional[str] = None,
    source_ip: Optional[str] = None,
    proxy: Optional[str] = None,
) -> None:
    """Execute the legacy download flow and send results back to Telegram."""

    ctx = _require_download_context()

    logger.debug("Entering: download_url_and_send")

    loop_additional = asyncio.new_event_loop()
    thread_additional = threading.Thread(target=loop_additional.run_forever, name="Additional Async Runner", daemon=True)

    def run_async(coro):
        if not thread_additional.is_alive():
            thread_additional.start()
        future = asyncio.run_coroutine_threadsafe(coro, loop_additional)
        return future.result()

    bot = Bot(
        token=bot_options["token"],
        base_url=bot_options["base_url"],
        base_file_url=bot_options["base_file_url"],
        local_mode=bot_options["local_mode"],
        request=HTTPXRequest(http_version=ctx.http_version),
        get_updates_request=HTTPXRequest(http_version=ctx.http_version),
    )
    run_async(bot.initialize())
    logger.debug(bot.token)
    download_dir = os.path.join(ctx.dl_dir, str(uuid4()))
    shutil.rmtree(download_dir, ignore_errors=True)
    os.makedirs(download_dir)
    url_obj = URL(url)
    host = url_obj.host
    download_video = False
    status = "initial"
    add_description = ""
    cmd: LocalCommand | None = None
    cmd_name = ""
    cmd_args: tuple[str, ...] = tuple()
    cmd_input: str | None = None
    if ((ctx.domain_sc in host or ctx.domain_sc_googl in host) and ctx.domain_sc_api not in host) or (ctx.domain_bc in host and ctx.bcdl_enable):
        if (ctx.domain_sc in host or ctx.domain_sc_googl in host) and ctx.domain_sc_api not in host:
            cmd = ctx.scdl_bin
            cmd_name = str(cmd)
            cmd_args = (
                "-l",
                url,
                "-c",
                "--path",
                download_dir,
                "--onlymp3",
                "--addtofile",
                "--addtimestamp",
                "--no-playlist-folder",
                "--extract-artist",
            )
            cmd_input = None
        elif ctx.domain_bc in host and ctx.bcdl_enable:
            cmd = ctx.bcdl_bin
            cmd_name = str(cmd)
            cmd_args = (
                "--base-dir",
                download_dir,
                "--template",
                "%{track} - %{artist} - %{title} [%{album}]",
                "--overwrite",
                "--group",
                "--embed-art",
                "--no-slugify",
                url,
            )
            cmd_input = "yes"

        env = None
        if proxy:
            env = {"http_proxy": proxy, "https_proxy": proxy}
        logger.debug("%s starts: %s", cmd_name, url)
        if cmd is None:
            raise RuntimeError("Downloader command is not configured")
        if not cmd_args:
            raise RuntimeError("Downloader command arguments are not configured")
        cmd_with_args = cmd[cmd_args]
        cmd_proc = cmd_with_args.popen(env=env, stdin=PIPE, stdout=PIPE, stderr=PIPE, universal_newlines=True)
        try:
            cmd_stdout, cmd_stderr = cmd_proc.communicate(input=cmd_input, timeout=ctx.dl_timeout)
            cmd_retcode = cmd_proc.returncode
            if cmd_retcode or (any(err in cmd_stderr for err in ["Error resolving url", "is not streamable", "Failed to get item"]) and ".mp3" not in cmd_stderr):
                raise ProcessExecutionError(cmd_args, cmd_retcode, cmd_stdout, cmd_stderr)
            logger.debug("%s succeeded: %s", cmd_name, url)
            status = "success"
        except TimeoutExpired:
            cmd_proc.kill()
            logger.debug("%s took too much time and dropped: %s", cmd_name, url)
        except ProcessExecutionError:
            logger.debug("%s failed: %s", cmd_name, url)
            logger.debug(traceback.format_exc())

    if status == "initial":
        cmd_name = "ydl_download"
        ydl_opts = {
            "format": "bestaudio/best",
            "noplaylist": False,
            "paths": {"home": download_dir},
            "cachedir": os.path.join(ctx.dl_dir, ".cache"),
            "outtmpl": "%(title)s - %(uploader)s.%(ext)s",
        }

        if ctx.domain_tt in host:
            download_video = True
            ydl_opts["format"] = "mp4"
        elif (ctx.domain_tw in host or ctx.domain_twx in host) and (ctx.domain_ymc not in host):
            download_video = True
            ydl_opts["format"] = "mp4"
        elif ctx.domain_ig in host:
            download_video = True
            ydl_opts.update(
                {
                    "format": "mp4",
                    "postprocessors": [
                        {"key": "FFmpegCopyStream"},
                    ],
                    "postprocessor_args": {
                        "copystream": ["-codec:v", "libx264", "-crf", "24", "-preset", "veryfast", "-codec:a", "copy", "-f", "mp4", "-threads", "1"],
                    },
                }
            )
        else:
            ydl_opts.update(
                {
                    "format": "bestaudio/best",
                    "postprocessors": [
                        {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "320"},
                        {"key": "FFmpegMetadata"},
                        {"key": "EmbedThumbnail", "already_have_thumbnail": False},
                    ],
                    "postprocessor_args": {
                        "ExtractAudio": ["-threads", "1"],
                        "extractaudio": ["-threads", "1"],
                    },
                    "writethumbnail": True,
                    "noplaylist": True,
                }
            )
        if proxy:
            ydl_opts["proxy"] = proxy
        if source_ip:
            ydl_opts["source_address"] = source_ip
        cookies_download_file = None
        if cookies_file:
            cookies_download_file = tempfile.NamedTemporaryFile(mode="wb", delete=False)
            cookies_download_file_path = pathlib.Path(cookies_download_file.name)
            if cookies_file.startswith("http"):
                try:
                    r = requests.get(cookies_file, allow_redirects=True, timeout=5)
                    cookies_download_file.write(r.content)
                    cookies_download_file.close()
                    ydl_opts["cookiefile"] = str(cookies_download_file_path)
                except Exception:
                    logger.debug("download_url_and_send could not download cookies file")
            elif cookies_file.startswith("firefox:"):
                cookies_file_components = cookies_file.split(":", maxsplit=2)
                if len(cookies_file_components) == 3:
                    cookies_sqlite_file = cookies_file_components[2]
                    cookies_download_sqlite_path = pathlib.Path.home() / ".mozilla" / "firefox" / cookies_file_components[1] / "cookies.sqlite"
                    try:
                        r = requests.get(cookies_sqlite_file, allow_redirects=True, timeout=5)
                        with open(cookies_download_sqlite_path, "wb") as cfile:
                            cfile.write(r.content)
                        ydl_opts["cookiesfrombrowser"] = ("firefox", cookies_file_components[1], None, None)
                        logger.debug("download_url_and_send downloaded cookies.sqlite file")
                    except Exception:
                        logger.debug("download_url_and_send could not download cookies.sqlite file")
                else:
                    ydl_opts["cookiesfrombrowser"] = ("firefox", cookies_file_components[1], None, None)
            else:
                cookies_download_file.write(open(cookies_file, "rb").read())
                cookies_download_file.close()
                ydl_opts["cookiefile"] = str(cookies_download_file_path)

        try:
            ydl.YoutubeDL(ydl_opts).download([url])
            logger.debug("%s succeeded: %s", cmd_name, url)
            status = "success"
            if download_video:
                unsanitized_info_dict = ydl.YoutubeDL(ydl_opts).extract_info(url, download=False)
                info_dict = ydl.YoutubeDL(ydl_opts).sanitize_info(unsanitized_info_dict)
                if "description" in info_dict and info_dict["description"]:
                    unescaped_add_description = "\n"
                    if "channel" in info_dict and info_dict["channel"]:
                        unescaped_add_description += "@ " + info_dict["channel"]
                    if "uploader" in info_dict and info_dict["uploader"]:
                        unescaped_add_description += " " + info_dict["uploader"]
                    unescaped_add_description += "\n" + info_dict["description"][:800]
                    add_description = escape_markdown(unescaped_add_description, version=1)
        except Exception as exc:
            print(exc)
            logger.debug("%s failed: %s", cmd_name, url)
            logger.debug(traceback.format_exc())
            status = "failed"
        if cookies_file and cookies_download_file is not None:
            cookies_download_file.close()
            os.unlink(cookies_download_file.name)

    if status == "failed":
        run_async(
            bot.send_message(
                chat_id=chat_id,
                reply_to_message_id=reply_to_message_id,
                text=ctx.failed_text,
                parse_mode="Markdown",
            )
        )
    elif status == "timeout":
        run_async(
            bot.send_message(
                chat_id=chat_id,
                reply_to_message_id=reply_to_message_id,
                text=ctx.dl_timeout_text,
                parse_mode="Markdown",
            )
        )
    elif status == "success":
        file_list: list[str] = []
        for directory, _dirs, files in os.walk(download_dir):
            for file in files:
                file_list.append(os.path.join(directory, file))
        if not file_list:
            logger.debug("No files in dir: %s", download_dir)
            run_async(
                bot.send_message(
                    chat_id=chat_id,
                    reply_to_message_id=reply_to_message_id,
                    text="*Sorry*, I couldn't download any files from some of the provided links",
                    parse_mode="Markdown",
                )
            )
        else:
            for file in sorted(file_list):
                file_name = os.path.split(file)[-1]
                file_parts: list[str] = []
                try:
                    file_root, file_ext = os.path.splitext(file)
                    file_format = file_ext.replace('.', '').lower()
                    file_size = os.path.getsize(file)
                    if file_format not in list(ctx.audio_formats) + list(ctx.video_formats):
                        raise FileNotSupportedError(file_format)
                    if file_format in ctx.video_formats and not download_video:
                        if file_size > ctx.max_convert_file_size:
                            raise FileTooLargeError(file_size)
                        logger.debug("Converting video format: %s", file)
                        try:
                            file_converted = file.replace(file_ext, '.mp3')
                            result = convert_video_to_audio(
                                input_file=file,
                                output_file=file_converted,
                                audio_bitrate=None,
                            )
                            if not result.success:
                                raise FileNotConvertedError(result.error or "Conversion failed")
                            file = file_converted
                            file_root, file_ext = os.path.splitext(file)
                            file_format = file_ext.replace('.', '').lower()
                            file_size = os.path.getsize(file)
                        except (FFmpegError, Exception) as exc:
                            logger.error("Conversion failed: %s", exc)
                            raise FileNotConvertedError(str(exc))
                    if file_size <= ctx.max_tg_file_size:
                        file_parts.append(file)
                    else:
                        logger.debug("Splitting: %s", file)
                        id3 = None
                        try:
                            id3 = ID3(file, translate=False)
                        except Exception:
                            pass
                        try:
                            output_pattern = file.replace(file_ext, '.part{}{}'.format('{}', file_ext))
                            result = split_file(
                                input_file=file,
                                max_size=ctx.max_tg_file_size,
                                output_pattern=output_pattern,
                            )
                            if not result.success:
                                raise FileSplittedPartiallyError(result.parts or [])
                            file_parts = list(result.parts)
                            if id3:
                                for file_part in file_parts:
                                    try:
                                        id3.save(file_part, v1=ID3v1SaveOptions.CREATE, v2_version=4)
                                    except Exception:
                                        pass
                        except (FFmpegError, Exception) as exc:
                            logger.error("File splitting failed: %s", exc)
                            raise FileSplittedPartiallyError(file_parts)
                except FileNotSupportedError as exc:
                    if not (exc.file_format in ['m3u', 'jpg', 'jpeg', 'png', 'finished', 'tmp']):
                        logger.debug("Unsupported file format: %s", file_name)
                        run_async(
                            bot.send_message(
                                chat_id=chat_id,
                                reply_to_message_id=reply_to_message_id,
                                text="*Sorry*, downloaded file `{}` is in format I could not yet convert or send".format(file_name),
                                parse_mode="Markdown",
                            )
                        )
                    continue
                except FileTooLargeError as exc:
                    logger.debug("Large file for convert: %s", file_name)
                    run_async(
                        bot.send_message(
                            chat_id=chat_id,
                            reply_to_message_id=reply_to_message_id,
                            text="*Sorry*, downloaded file `{}` is `{}` MB and it is larger than I could convert (`{} MB`)".format(
                                file_name,
                                exc.file_size // 1_000_000,
                                ctx.max_convert_file_size // 1_000_000,
                            ),
                            parse_mode="Markdown",
                        )
                    )
                    continue
                except FileSplittedPartiallyError as exc:
                    file_parts = list(exc.file_parts)
                    logger.debug("Splitting failed: %s", file_name)
                    run_async(
                        bot.send_message(
                            chat_id=chat_id,
                            reply_to_message_id=reply_to_message_id,
                            text="*Sorry*, I do not have enough resources to convert the file `{}`..".format(file_name),
                            parse_mode="Markdown",
                        )
                    )
                    continue
                except FileNotConvertedError:
                    logger.debug("Conversion failed: %s", file_name)
                    run_async(
                        bot.send_message(
                            chat_id=chat_id,
                            reply_to_message_id=reply_to_message_id,
                            text="*Sorry*, I do not have enough resources to convert the file `{}`..".format(file_name),
                            parse_mode="Markdown",
                        )
                    )
                    continue
                caption = None
                reply_to_message_id_send = None
                if flood:
                    addition = ''
                    if ctx.domain_yt in host or ctx.domain_yt_be in host:
                        source = 'YouTube'
                        file_root, file_ext = os.path.splitext(file_name)
                        file_title = file_root.replace(file_ext, '')
                        addition = ': ' + file_title
                    elif ctx.domain_sc in host or ctx.domain_sc_googl in host:
                        source = 'SoundCloud'
                    elif ctx.domain_bc in host:
                        source = 'Bandcamp'
                    else:
                        source = url_obj.host.replace('.com', '').replace('.ru', '').replace('www.', '').replace('m.', '')
                    caption = "@{} _got it from_ [{}]({}){}".format(
                        bot.username.replace('_', r'\_'), source, url, addition.replace('_', r'\_')
                    )
                    if add_description:
                        caption += add_description
                    reply_to_message_id_send = reply_to_message_id
                sent_audio_ids: list[str] = []
                for index, file_part in enumerate(file_parts):
                    file_name = os.path.split(file_part)[-1]
                    logger.debug("Sending: %s", file_name)
                    run_async(bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VOICE))
                    caption_part = None
                    if len(file_parts) > 1:
                        caption_part = "Part {} of {}".format(str(index + 1), str(len(file_parts)))
                    if caption:
                        if caption_part:
                            caption_full = caption_part + " | " + caption
                        else:
                            caption_full = caption
                    else:
                        caption_full = caption_part or ''
                    retries = 3
                    for attempt in range(retries):
                        try:
                            logger.debug("Trying %s time to send file part: %s", attempt + 1, file_part)
                            if file_part.endswith('.mp3'):
                                mp3 = MP3(file_part)
                                duration = round(mp3.info.length)
                                performer = None
                                title = None
                                try:
                                    performer = ', '.join(mp3['artist'])
                                    title = ', '.join(mp3['title'])
                                except Exception:
                                    pass
                                audio = open(file_part, 'rb')
                                audio_msg = run_async(
                                    bot.send_audio(
                                        chat_id=chat_id,
                                        reply_to_message_id=reply_to_message_id_send,
                                        audio=audio,
                                        duration=duration,
                                        performer=performer,
                                        title=title,
                                        caption=caption_full,
                                        parse_mode='Markdown',
                                        read_timeout=ctx.common_connection_timeout,
                                        write_timeout=ctx.common_connection_timeout,
                                        connect_timeout=ctx.common_connection_timeout,
                                        pool_timeout=ctx.common_connection_timeout,
                                    ),
                                )
                                if audio_msg.audio and audio_msg.audio.file_id:
                                    sent_audio_ids.append(audio_msg.audio.file_id)
                                logger.debug("Sending audio succeeded: %s", file_name)
                                break
                            elif download_video:
                                video = open(file_part, 'rb')
                                probe_result = probe_media(file_part)
                                try:
                                    duration = int(float(probe_result['format']['duration']))
                                    videostream = next(
                                        item for item in probe_result.get('streams', []) if item.get('codec_type') == 'video'
                                    )
                                    width = int(videostream['width'])
                                    height = int(videostream['height'])
                                except (KeyError, StopIteration, TypeError, ValueError) as exc:
                                    raise FFprobeError(f"ffprobe returned incomplete data for {file_part}") from exc
                                video_msg = run_async(
                                    bot.send_video(
                                        chat_id=chat_id,
                                        reply_to_message_id=reply_to_message_id_send,
                                        video=video,
                                        duration=duration,
                                        width=width,
                                        height=height,
                                        caption=caption_full,
                                        parse_mode='Markdown',
                                        supports_streaming=True,
                                        read_timeout=ctx.common_connection_timeout,
                                        write_timeout=ctx.common_connection_timeout,
                                        connect_timeout=ctx.common_connection_timeout,
                                        pool_timeout=ctx.common_connection_timeout,
                                    ),
                                )
                                if video_msg.video and video_msg.video.file_id:
                                    sent_audio_ids.append(video_msg.video.file_id)
                                logger.debug("Sending video succeeded: %s", file_name)
                                break
                            else:
                                document = open(file_part, 'rb')
                                run_async(
                                    bot.send_document(
                                        chat_id=chat_id,
                                        document=document,
                                        reply_to_message_id=reply_to_message_id_send,
                                        caption=caption_full,
                                        parse_mode='Markdown',
                                        read_timeout=ctx.common_connection_timeout,
                                        write_timeout=ctx.common_connection_timeout,
                                        connect_timeout=ctx.common_connection_timeout,
                                        pool_timeout=ctx.common_connection_timeout,
                                    )
                                )
                                break
                        except Exception as exc:
                            logger.debug("Try %s failed to send %s: %s", attempt + 1, file_part, exc)
                            time.sleep(3)
                    else:
                        logger.debug("Failed to send %s after retries", file_part)

    shutil.rmtree(download_dir, ignore_errors=True)
    if wait_message_id is not None:
        try:
            run_async(
                bot.delete_message(
                    chat_id=chat_id,
                    message_id=wait_message_id,
                )
            )
        except Exception:
            logger.debug("Failed to delete wait message", exc_info=True)
    run_async(bot.shutdown())
    loop_additional.call_soon_threadsafe(loop_additional.stop)
    if thread_additional.is_alive():
        thread_additional.join(timeout=1)
