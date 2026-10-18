"""Send downloaded files to Telegram: single files or albums of up to 10, with optional captions."""

import asyncio
import html
import logging

from telegram import Bot, InputMediaAudio, InputMediaVideo, ReplyParameters
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, NetworkError, TelegramError

from scdlbot import observe
from scdlbot.config import Config
from scdlbot.db import Job
from scdlbot.links import VIDEO
from scdlbot.media import Item

log = logging.getLogger(__name__)

ALBUM_SIZE = 10  # Telegram's limit for media groups
DESCRIPTION_LIMIT = 700  # captions are limited to 1024 characters


class Sender:
    """Delivers the files of one job. Playlist items and parts of split files go out as albums."""

    def __init__(self, bot: Bot, cfg: Config, job: Job):
        self.bot, self.cfg, self.job = bot, cfg, job
        self.album: list[Item] = []
        self.sent = self.failed = self.bytes = 0
        self.reply = ReplyParameters(job.message_id, allow_sending_without_reply=True) if job.captions else None

    async def add(self, item: Item) -> None:
        if item.playlist_index is None and item.part is None:
            await self._send([item])
            return
        # Without a local Bot API server albums are uploaded from memory, so keep them small:
        if self.album and not self.cfg.local_mode and sum(i.size for i in self.album) + item.size > self.cfg.max_tg_file_size:
            await self.flush()
        self.album.append(item)
        if len(self.album) == ALBUM_SIZE:
            await self.flush()

    async def flush(self) -> None:
        if self.album:
            items, self.album = self.album, []
            await self._send(items)

    async def _send(self, items: list[Item]) -> None:
        size = sum(item.size for item in items)
        timeout = 30 + size / 1_000_000  # uploads may be slow: give at least 1 MB/s
        try:
            for attempt in range(2):
                try:
                    if len(items) == 1:
                        await self._send_one(items[0], timeout)
                    else:
                        media = [self._media(item, first=(n == 0)) for n, item in enumerate(items)]
                        await self.bot.send_media_group(self.job.chat_id, media, reply_parameters=self.reply, read_timeout=timeout, write_timeout=timeout)
                    break
                except BadRequest:
                    raise
                except NetworkError:  # including timeouts; flood limits are handled by the rate limiter
                    if attempt:
                        raise
                    await asyncio.sleep(3)
        except Forbidden:
            raise  # bot was blocked or kicked: the whole job is pointless
        except TelegramError as exc:
            log.warning("Job %s: could not send %d file(s): %s", self.job.id, len(items), exc)
            observe.TELEGRAM_ERRORS.labels(type(exc).__name__).inc()
            self.failed += len(items)
        else:
            self.sent += len(items)
            self.bytes += size
            observe.ITEMS_SENT.labels(self.job.kind).inc(len(items))
            observe.BYTES_SENT.labels(self.job.kind).inc(size)
        finally:
            for item in items:
                item.path.unlink(missing_ok=True)

    def _file(self, item: Item):
        # A local Bot API server reads the file from disk by its file:// URI, the cloud one needs it uploaded:
        return item.path if self.cfg.local_mode else item.path.read_bytes()

    def _caption(self, item: Item, first: bool = True) -> str | None:
        lines = []
        if item.part:
            lines.append(f"Part {item.part[0]} of {item.part[1]}")
        if first and self.job.captions and not self.sent:  # source info once per job
            source = f'<a href="{html.escape(self.job.url)}">{html.escape(self.job.site)}</a>'
            lines.append(f"@{html.escape(self.bot.username)} <i>got it from</i> {source}")
            if item.kind == VIDEO and (item.uploader or item.description):
                description = (item.description or "")[:DESCRIPTION_LIMIT]
                lines.append(f"\n<b>{html.escape(item.uploader or '')}</b>\n{html.escape(description)}".rstrip())
        return "\n".join(lines) or None

    async def _send_one(self, item: Item, timeout: float) -> None:
        common = {"caption": self._caption(item), "parse_mode": ParseMode.HTML, "duration": item.duration, "reply_parameters": self.reply}
        timeouts = {"read_timeout": timeout, "write_timeout": timeout}
        if item.kind == VIDEO:
            video = self._file(item)
            await self.bot.send_video(self.job.chat_id, video, width=item.width, height=item.height, supports_streaming=True, **common, **timeouts)
        else:
            audio = self._file(item)
            await self.bot.send_audio(self.job.chat_id, audio, performer=item.performer, title=item.title, filename=item.path.name, **common, **timeouts)

    def _media(self, item: Item, first: bool) -> InputMediaAudio | InputMediaVideo:
        common = {"caption": self._caption(item, first), "parse_mode": ParseMode.HTML, "duration": item.duration}
        if item.kind == VIDEO:
            return InputMediaVideo(self._file(item), width=item.width, height=item.height, supports_streaming=True, filename=item.path.name, **common)
        return InputMediaAudio(self._file(item), performer=item.performer, title=item.title, filename=item.path.name, **common)
