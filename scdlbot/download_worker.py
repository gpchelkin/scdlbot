"""Huey-backed download worker that delegates to the legacy download routine."""

from __future__ import annotations

import logging
import os
import tempfile
from importlib import import_module
from typing import Any, Dict, Optional, cast

from huey import SqliteHuey
from huey.exceptions import HueyException, ResultTimeout, TaskException
from pydantic import BaseModel, Field

from scdlbot.download_execution import download_url_and_send, get_download_context

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


class DownloadRequest(BaseModel):
    """Serializable payload for running the legacy download routine."""

    bot_options: Dict[str, Any] = Field(description="Bot configuration options")
    chat_id: int = Field(description="Telegram chat ID")
    url: str = Field(description="URL to download")
    flood: bool = Field(default=False, description="Flood mode flag")
    reply_to_message_id: Optional[int] = Field(default=None, description="Message ID to reply to")
    wait_message_id: Optional[int] = Field(default=None, description="Waiting message ID")
    cookies_file: Optional[str] = Field(default=None, description="Path to cookies file")
    source_ip: Optional[str] = Field(default=None, description="Source IP address")
    proxy: Optional[str] = Field(default=None, description="Proxy configuration")


class DownloadResponse(BaseModel):
    """Result envelope used for synchronous callers/tests."""

    success: bool = Field(description="Whether download succeeded")
    error: Optional[str] = Field(default=None, description="Error message if failed")


def _ensure_download_context() -> None:
    if get_download_context() is None:
        module = import_module("scdlbot.__main__")
        setup = getattr(module, "setup_download_context", None)
        if callable(setup):
            setup()
    if get_download_context() is None:
        raise RuntimeError("Download context is not configured")


def _run_legacy_download(request: DownloadRequest) -> None:
    """Invoke the original download_url_and_send implementation."""

    _ensure_download_context()
    download_url_and_send(
        bot_options=request.bot_options,
        chat_id=request.chat_id,
        url=request.url,
        flood=request.flood,
        reply_to_message_id=request.reply_to_message_id,
        wait_message_id=request.wait_message_id,
        cookies_file=request.cookies_file,
        source_ip=request.source_ip,
        proxy=request.proxy,
    )


@huey.task()
def download_url_and_send_task(request_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Huey task wrapper around the legacy download routine."""

    request = DownloadRequest(**request_dict)
    try:
        _run_legacy_download(request)
        return DownloadResponse(success=True).model_dump()
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Download task failed")
        return DownloadResponse(success=False, error=str(exc)).model_dump()


def download_url_async(request: DownloadRequest, timeout: Optional[int] = None) -> DownloadResponse:
    """Queue a download task and wait for completion (mainly for tests)."""

    job = download_url_and_send_task(request.model_dump())

    if not callable(job):
        # Immediate mode executes synchronously and returns the payload directly.
        result_dict = cast(Dict[str, Any], job)
        return DownloadResponse(**result_dict)

    resolved_timeout = timeout or DOWNLOAD_TIMEOUT

    try:
        result_dict = cast(Dict[str, Any], job(blocking=True, timeout=resolved_timeout))
        return DownloadResponse(**result_dict)
    except ResultTimeout as exc:
        raise RuntimeError(f"Download timed out after {resolved_timeout}s") from exc
    except TaskException as exc:
        error_meta = exc.metadata or {}
        error_message = error_meta.get("error") or str(exc) or "unknown error"
        raise RuntimeError(f"Download failed: {error_message}") from exc
    except HueyException as exc:
        raise RuntimeError("Huey failed to execute download") from exc


def download_url_fire_and_forget(request: DownloadRequest) -> str:
    """Queue a download task without blocking and return best-effort identifier."""

    job = download_url_and_send_task(request.model_dump())

    if callable(job):
        job_id = getattr(job, "id", None)
        if job_id is not None:
            return str(job_id)
        return "pending"

    # Immediate mode executes synchronously; fabricate a deterministic identifier.
    return "immediate"
