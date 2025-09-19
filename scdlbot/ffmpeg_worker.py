"""Huey-backed ffmpeg worker for audio/video processing."""

from __future__ import annotations

import logging
import os
import pathlib
import subprocess
import tempfile
from typing import Any, Dict, List, Optional

import ffmpeg
from huey import SqliteHuey
from huey.exceptions import HueyException, ResultTimeout, TaskException
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


class FFmpegError(RuntimeError):
    """Raised when ffmpeg operations fail."""


def _get_bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return bool(int(value))
    except ValueError:
        return default


FFMPEG_BIN = os.getenv("FFMPEG_BIN", "ffmpeg")
FFMPEG_TIMEOUT = int(os.getenv("FFMPEG_TIMEOUT", "300"))
HUEY_QUEUE_NAME = os.getenv("FFMPEG_HUEY_QUEUE", "scdlbot-ffmpeg")
HUEY_DB_FILE = os.getenv(
    "FFMPEG_HUEY_DB_FILE",
    os.path.join(tempfile.gettempdir(), "scdlbot-ffmpeg-huey.sqlite"),
)
HUEY_IMMEDIATE = _get_bool_env("FFMPEG_HUEY_IMMEDIATE", default=False)
HUEY_WORKERS = int(os.getenv("FFMPEG_HUEY_WORKERS", "2"))

huey = SqliteHuey(
    HUEY_QUEUE_NAME,
    filename=HUEY_DB_FILE,
    immediate=HUEY_IMMEDIATE,
)


class VideoToAudioRequest(BaseModel):
    """Request model for video to audio conversion."""

    input_file: str = Field(description="Path to input video file")
    output_file: str = Field(description="Path to output audio file")
    audio_bitrate: Optional[str] = Field(default=None, description="Audio bitrate (e.g., '320k')")
    threads: int = Field(default=1, description="Number of threads to use")

    @field_validator("input_file")
    @classmethod
    def validate_input_exists(cls, v: str) -> str:
        if not pathlib.Path(v).exists():
            raise ValueError(f"Input file does not exist: {v}")
        return v


class VideoToAudioResponse(BaseModel):
    """Response model for video to audio conversion."""

    output_file: str = Field(description="Path to converted file")
    success: bool = Field(description="Whether conversion succeeded")
    error: Optional[str] = Field(default=None, description="Error message if failed")


class FileSplitRequest(BaseModel):
    """Request model for file splitting."""

    input_file: str = Field(description="Path to input file")
    max_size: int = Field(description="Maximum size per part in bytes")
    output_pattern: str = Field(description="Output pattern with {} for part number")
    threads: int = Field(default=1, description="Number of threads to use")

    @field_validator("input_file")
    @classmethod
    def validate_input_exists(cls, v: str) -> str:
        if not pathlib.Path(v).exists():
            raise ValueError(f"Input file does not exist: {v}")
        return v

    @field_validator("max_size")
    @classmethod
    def validate_max_size(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("Max size must be positive")
        return v


class FileSplitResponse(BaseModel):
    """Response model for file splitting."""

    parts: List[str] = Field(description="List of split file paths")
    success: bool = Field(description="Whether splitting succeeded")
    error: Optional[str] = Field(default=None, description="Error message if failed")


class ProbeMediaRequest(BaseModel):
    """Request model for media probing."""

    file_path: str = Field(description="Path to media file")

    @field_validator("file_path")
    @classmethod
    def validate_file_exists(cls, v: str) -> str:
        if not pathlib.Path(v).exists():
            raise ValueError(f"File does not exist: {v}")
        return v


class ProbeMediaResponse(BaseModel):
    """Response model for media probing."""

    format: Dict[str, Any] = Field(description="Format information")
    streams: List[Dict[str, Any]] = Field(description="Stream information")
    duration: Optional[float] = Field(default=None, description="Duration in seconds")
    size: Optional[int] = Field(default=None, description="File size in bytes")
    error: Optional[str] = Field(default=None, description="Error message if failed")


@huey.task()
def convert_video_to_audio_task(request_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Convert video file to audio format."""
    request = VideoToAudioRequest(**request_dict)

    try:
        ffinput = ffmpeg.input(request.input_file)
        output_args = {
            "vn": None,  # No video
            "threads": request.threads,
        }
        if request.audio_bitrate:
            output_args["audio_bitrate"] = request.audio_bitrate

        ffmpeg.output(ffinput, request.output_file, **output_args).overwrite_output().run()

        return VideoToAudioResponse(
            output_file=request.output_file,
            success=True
        ).model_dump()
    except Exception as e:
        logger.error(f"Failed to convert video to audio: {e}")
        return VideoToAudioResponse(
            output_file=request.output_file,
            success=False,
            error=str(e)
        ).model_dump()


@huey.task()
def split_file_task(request_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Split large file into smaller parts."""
    request = FileSplitRequest(**request_dict)

    try:
        from scdlbot.ffprobe import probe_media

        file_size = os.path.getsize(request.input_file)
        parts_number = file_size // request.max_size + 1

        if parts_number == 1:
            return FileSplitResponse(
                parts=[request.input_file],
                success=True
            ).model_dump()

        file_parts = []
        cur_position = 0

        for i in range(parts_number):
            file_part = request.output_pattern.format(i + 1)
            ffinput = ffmpeg.input(request.input_file)

            if i == (parts_number - 1):
                # Last part - no size limit
                ffmpeg.output(
                    ffinput,
                    file_part,
                    codec="copy",
                    vn=None,
                    ss=cur_position,
                    threads=request.threads
                ).overwrite_output().run()
            else:
                # Other parts - limit by size
                part_size = request.max_size
                ffmpeg.output(
                    ffinput,
                    file_part,
                    codec="copy",
                    vn=None,
                    ss=cur_position,
                    fs=part_size,
                    threads=request.threads
                ).overwrite_output().run()

                # Get duration of this part for next iteration
                probe_result = probe_media(file_part)
                part_duration = float(probe_result["format"]["duration"])
                cur_position += part_duration

            file_parts.append(file_part)

        return FileSplitResponse(
            parts=file_parts,
            success=True
        ).model_dump()
    except Exception as e:
        logger.error(f"Failed to split file: {e}")
        return FileSplitResponse(
            parts=[],
            success=False,
            error=str(e)
        ).model_dump()


def convert_video_to_audio(
    input_file: str,
    output_file: str,
    audio_bitrate: Optional[str] = None,
    timeout: Optional[int] = None
) -> VideoToAudioResponse:
    """Synchronously convert video to audio via Huey."""
    request = VideoToAudioRequest(
        input_file=input_file,
        output_file=output_file,
        audio_bitrate=audio_bitrate
    )

    resolved_timeout = timeout or FFMPEG_TIMEOUT

    if huey.immediate:
        result_dict = convert_video_to_audio_task(request.model_dump())
        return VideoToAudioResponse(**result_dict)

    result = convert_video_to_audio_task(request.model_dump())
    try:
        result_dict = result(blocking=True, timeout=resolved_timeout)
        return VideoToAudioResponse(**result_dict)
    except ResultTimeout as exc:
        raise FFmpegError(f"Video conversion timed out after {resolved_timeout}s") from exc
    except TaskException as exc:
        error_meta = exc.metadata or {}
        error_message = error_meta.get("error") or str(exc) or "unknown error"
        raise FFmpegError(f"Video conversion failed: {error_message}") from exc
    except HueyException as exc:
        raise FFmpegError(f"Huey failed to execute video conversion") from exc


def split_file(
    input_file: str,
    max_size: int,
    output_pattern: str,
    timeout: Optional[int] = None
) -> FileSplitResponse:
    """Synchronously split file via Huey."""
    request = FileSplitRequest(
        input_file=input_file,
        max_size=max_size,
        output_pattern=output_pattern
    )

    resolved_timeout = timeout or FFMPEG_TIMEOUT

    if huey.immediate:
        result_dict = split_file_task(request.model_dump())
        return FileSplitResponse(**result_dict)

    result = split_file_task(request.model_dump())
    try:
        result_dict = result(blocking=True, timeout=resolved_timeout)
        return FileSplitResponse(**result_dict)
    except ResultTimeout as exc:
        raise FFmpegError(f"File splitting timed out after {resolved_timeout}s") from exc
    except TaskException as exc:
        error_meta = exc.metadata or {}
        error_message = error_meta.get("error") or str(exc) or "unknown error"
        raise FFmpegError(f"File splitting failed: {error_message}") from exc
    except HueyException as exc:
        raise FFmpegError(f"Huey failed to execute file splitting") from exc