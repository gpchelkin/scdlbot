from dataclasses import dataclass, field
from typing import Optional, Union, List

@dataclass
class ErrorMessage:
    chat_id: int
    text: str
    reply_to_message_id: Optional[int] = None
    parse_mode: str = "Markdown"

@dataclass
class SendAudio:
    chat_id: int
    file_path: str
    duration: int
    reply_to_message_id: Optional[int] = None
    performer: Optional[str] = None
    title: Optional[str] = None
    caption: Optional[str] = None
    parse_mode: str = "Markdown"

@dataclass
class SendVideo:
    chat_id: int
    file_path: str
    duration: int
    width: int
    height: int
    reply_to_message_id: Optional[int] = None
    caption: Optional[str] = None
    parse_mode: str = "Markdown"
    supports_streaming: bool = True

@dataclass
class SendDocument:
    chat_id: int
    file_path: str
    reply_to_message_id: Optional[int] = None
    caption: Optional[str] = None
    parse_mode: str = "Markdown"

SendIntent = Union[ErrorMessage, SendAudio, SendVideo, SendDocument]

@dataclass
class DownloadResult:
    sends: List[SendIntent] = field(default_factory=list)
    cleanup_paths: List[str] = field(default_factory=list)