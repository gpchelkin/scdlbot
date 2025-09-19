"""Huey-backed ffprobe helpers."""

from __future__ import annotations

import json
import logging
import os
import pathlib
import subprocess
import tempfile
from typing import Any, Dict

from huey import SqliteHuey
from huey.exceptions import HueyException, ResultTimeout, TaskException

logger = logging.getLogger(__name__)


class FFprobeError(RuntimeError):
    """Raised when ffprobe fails or returns invalid data."""


def _get_bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return bool(int(value))
    except ValueError:
        return default


FFPROBE_BIN = os.getenv("FFPROBE_BIN", "ffprobe")
FFPROBE_TIMEOUT = int(os.getenv("FFPROBE_TIMEOUT", "60"))
HUEY_QUEUE_NAME = os.getenv("FFPROBE_HUEY_QUEUE", "scdlbot-ffprobe")
HUEY_DB_FILE = os.getenv(
    "FFPROBE_HUEY_DB_FILE",
    os.path.join(tempfile.gettempdir(), "scdlbot-ffprobe-huey.sqlite"),
)
HUEY_IMMEDIATE = _get_bool_env("FFPROBE_HUEY_IMMEDIATE", default=False)

huey = SqliteHuey(
    HUEY_QUEUE_NAME,
    filename=HUEY_DB_FILE,
    immediate=HUEY_IMMEDIATE,
)


@huey.task()
def ffprobe_task(path: str) -> Dict[str, Any]:
    """Run ffprobe for *path* and return parsed JSON output."""

    file_path = pathlib.Path(path)
    if not file_path.exists():
        raise FFprobeError(f"ffprobe target does not exist: {file_path}")

    cmd = [
        FFPROBE_BIN,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(file_path),
    ]
    try:
        completed = subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as exc:
        raise FFprobeError(f"ffprobe binary not found: {FFPROBE_BIN}") from exc
    except subprocess.CalledProcessError as exc:  # pragma: no cover - subprocess failure path
        logger.debug("ffprobe stderr: %s", exc.stderr)
        raise FFprobeError(f"ffprobe failed for {file_path} (exit code {exc.returncode})") from exc

    try:
        data = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        logger.debug("ffprobe stdout: %s", completed.stdout)
        raise FFprobeError(f"Unable to parse ffprobe output for {file_path}") from exc

    return data


def probe_media(path: str, timeout: int | None = None) -> Dict[str, Any]:
    """Synchronously probe *path* via Huey and return ffprobe metadata."""

    resolved_timeout = timeout or FFPROBE_TIMEOUT
    if huey.immediate:
        return ffprobe_task(path)

    result = ffprobe_task(path)
    try:
        return result(blocking=True, timeout=resolved_timeout)
    except ResultTimeout as exc:
        raise FFprobeError(f"ffprobe timed out after {resolved_timeout}s for {path}") from exc
    except TaskException as exc:  # pragma: no cover - depends on task failure
        error_meta = exc.metadata or {}
        error_message = error_meta.get("error") or str(exc) or "unknown error"
        raise FFprobeError(f"ffprobe raised an exception for {path}: {error_message}") from exc
    except HueyException as exc:  # pragma: no cover - defensive catch-all
        raise FFprobeError(f"Huey failed to execute ffprobe for {path}") from exc
