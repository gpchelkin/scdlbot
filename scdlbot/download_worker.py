"""Huey-backed download worker that delegates to the legacy download routine."""

from __future__ import annotations

from typing import List

import asyncio
import logging
import os
import shutil
import tempfile
from typing import Any, Dict, Optional, cast

from huey import SqliteHuey
from huey.exceptions import HueyException, ResultTimeout, TaskException
from huey.contrib.asyncio import aget_result
from pydantic import BaseModel, Field

from scdlbot.download_execution import prepare_download_sends, get_download_context, _require_download_context
from scdlbot.models import DownloadResult, ErrorMessage, SendAudio, SendVideo, SendDocument, SendIntent
from scdlbot.metrics import track_huey_enqueue
from telegram import Bot
from telegram.request import HTTPXRequest

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _get_bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return bool(int(value))
    except ValueError:
        return default


HUEY_QUEUE_NAME = os.getenv("DOWNLOAD_HUEY_QUEUE", "scdlbot-downloads")
HUEY_DB_FILE = os.getenv(
    "DOWNLOAD_HUEY_DB_FILE",
    os.path.join(tempfile.gettempdir(), "scdlbot-downloads-huey.sqlite"),
)
HUEY_IMMEDIATE = _get_bool_env("DOWNLOAD_HUEY_IMMEDIATE", default=False)
DOWNLOAD_TIMEOUT = int(os.getenv("DOWNLOAD_TIMEOUT", "300") or 300)
_configured_workers = int(os.getenv("DOWNLOAD_HUEY_WORKERS", "4") or 4)
HUEY_WORKERS = max(1, min(_configured_workers, 4))

huey = SqliteHuey(
    HUEY_QUEUE_NAME,
    filename=HUEY_DB_FILE,
    immediate=HUEY_IMMEDIATE,
)


def _ensure_download_context() -> None:
    ctx = get_download_context()
    if ctx is None:
        # Try to setup from __main__
        try:
            from scdlbot import __main__

            setup = getattr(__main__, "setup_download_context", None)
            if callable(setup):
                setup()
        except ImportError:
            pass
        ctx = get_download_context()
        if ctx is None:
            raise RuntimeError("Download context is not configured")


class DownloadRequest(BaseModel):
    """Serializable payload for running the legacy download routine."""

    chat_id: int = Field(description="Telegram chat ID")
    url: str = Field(description="URL to download")
    flood: bool = Field(default=False, description="Flood mode flag")
    reply_to_message_id: Optional[int] = Field(default=None, description="Message ID to reply to")
    wait_message_id: Optional[int] = Field(default=None, description="Waiting message ID")
    cookies_file: Optional[str] = Field(default=None, description="Path to cookies file")
    source_ip: Optional[str] = Field(default=None, description="Source IP address")
    proxy: Optional[str] = Field(default=None, description="Proxy configuration")
    bot_username: Optional[str] = Field(default=None, description="Bot username for captions")


class DownloadResponse(BaseModel):
    """Result envelope used for synchronous callers/tests."""

    success: bool = Field(description="Whether download succeeded")
    error: Optional[str] = Field(default=None, description="Error message if failed")


@huey.task()
def download_url_and_get_sends(request: DownloadRequest) -> DownloadResult:
    """Huey task wrapper that only prepares downloads, doesn't send."""

    try:
        # Ensure context is set up
        _ensure_download_context()

        # Just prepare the downloads, don't send
        result = asyncio.run(
            prepare_download_sends(
                chat_id=request.chat_id,
                url=request.url,
                flood=request.flood,
                reply_to_message_id=request.reply_to_message_id,
                bot_username=request.bot_username,
                cookies_file=request.cookies_file,
                source_ip=request.source_ip,
                proxy=request.proxy,
            )
        )

        # Return the DownloadResult directly - Huey will pickle it
        return result
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Download task failed")
        # Return empty result with error
        return DownloadResult(sends=[], cleanup_paths=[])


async def _send_download_results(bot: Bot, result: DownloadResult, wait_message_id: Optional[int] = None) -> None:
    """Send download results using bot after worker completes."""

    ctx = get_download_context()
    if ctx is None:
        raise RuntimeError("Download context not configured")

    # Send each result
    for send in result.sends:
        try:
            if isinstance(send, ErrorMessage):
                await bot.send_message(
                    chat_id=send.chat_id,
                    text=send.text,
                    parse_mode=send.parse_mode,
                    reply_to_message_id=send.reply_to_message_id,
                )
            elif isinstance(send, SendAudio):
                with open(send.file_path, "rb") as audio:
                    await bot.send_audio(
                        chat_id=send.chat_id,
                        audio=audio,
                        duration=send.duration,
                        performer=send.performer,
                        title=send.title,
                        caption=send.caption,
                        parse_mode=send.parse_mode,
                        reply_to_message_id=send.reply_to_message_id,
                        read_timeout=ctx.common_connection_timeout,
                        write_timeout=ctx.common_connection_timeout,
                        connect_timeout=ctx.common_connection_timeout,
                        pool_timeout=ctx.common_connection_timeout,
                    )
            elif isinstance(send, SendVideo):
                with open(send.file_path, "rb") as video:
                    await bot.send_video(
                        chat_id=send.chat_id,
                        video=video,
                        duration=send.duration,
                        width=send.width,
                        height=send.height,
                        caption=send.caption,
                        parse_mode=send.parse_mode,
                        supports_streaming=send.supports_streaming,
                        reply_to_message_id=send.reply_to_message_id,
                        read_timeout=ctx.common_connection_timeout,
                        write_timeout=ctx.common_connection_timeout,
                        connect_timeout=ctx.common_connection_timeout,
                        pool_timeout=ctx.common_connection_timeout,
                    )
            elif isinstance(send, SendDocument):
                with open(send.file_path, "rb") as document:
                    await bot.send_document(
                        chat_id=send.chat_id,
                        document=document,
                        caption=send.caption,
                        parse_mode=send.parse_mode,
                        reply_to_message_id=send.reply_to_message_id,
                        read_timeout=ctx.common_connection_timeout,
                        write_timeout=ctx.common_connection_timeout,
                        connect_timeout=ctx.common_connection_timeout,
                        pool_timeout=ctx.common_connection_timeout,
                    )
        except Exception as exc:
            logger.error("Failed to send result: %s", exc)

    # Delete wait message if specified
    if wait_message_id and result.sends:
        try:
            chat_id = result.sends[0].chat_id
            await bot.delete_message(
                chat_id=chat_id,
                message_id=wait_message_id,
            )
        except Exception:
            logger.debug("Failed to delete wait message", exc_info=True)

    # Cleanup files and directories
    for path in result.cleanup_paths:
        try:
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            elif os.path.exists(path):
                os.unlink(path)
        except Exception:
            logger.debug("Failed to cleanup %s", path)


@track_huey_enqueue(HUEY_QUEUE_NAME)
async def download_url_async(request: DownloadRequest, bot: Bot) -> DownloadResponse:
    """Queue a download task and wait for completion (mainly for tests).

    Args:
        request: Download request parameters
        bot: Bot instance to use for sending
    """
    # Add bot username to request
    request.bot_username = bot.username or ""

    job = download_url_and_get_sends(request)

    try:
        # Always use aget_result for consistency
        result = await aget_result(job)
    except Exception as exc:
        logger.exception("Download failed")
        return DownloadResponse(success=False, error=str(exc))

    # Now use the bot to send the results
    if result and result.sends:
        await _send_download_results(bot, result, request.wait_message_id)
        return DownloadResponse(success=True, error=None)
    else:
        return DownloadResponse(success=False, error="No results returned")


@track_huey_enqueue(HUEY_QUEUE_NAME)
async def download_and_send_reply(request: DownloadRequest, bot: Bot) -> None:
    """Queue a download task and handle results asynchronously.

    Args:
        request: Download request parameters
        bot: Bot instance to use for sending results
    """
    # Add bot username to request
    request.bot_username = bot.username or ""

    job = download_url_and_get_sends(request)

    try:
        # Always use aget_result for async result retrieval
        result = await aget_result(job)

        # Send results using bot
        if result and result.sends:
            await _send_download_results(bot, result, request.wait_message_id)

    except Exception as exc:
        logger.error("Download task failed: %s", exc)
