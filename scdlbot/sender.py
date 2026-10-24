"""Send downloaded files to Telegram: single files or albums of up to 10 (photos and videos can be mixed), with optional captions."""

import asyncio
import html
import logging

from telegram import Bot, InputMediaAudio, InputMediaPhoto, InputMediaVideo, LinkPreviewOptions, ReplyParameters
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, NetworkError, TelegramError

from scdlbot import observe
from scdlbot.config import Config
from scdlbot.db import Job
from scdlbot.links import AUDIO, PHOTO, VIDEO, clean_url
from scdlbot.media import Item

log = logging.getLogger(__name__)

ALBUM_SIZE = 10  # Telegram's limit for media groups
DESCRIPTION_LIMIT = 700  # captions are limited to 1024 characters
TEXT_LIMIT = 3500  # messages to 4096
# Telegram's format for source attribution: a link over two zero-width spaces at the very start of the caption
HIDDEN_LINK = '<a href="{}">\u200b\u200b</a>'


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
        if self.album and not self.cfg.local_mode and sum(_size(i) for i in (*self.album, item)) > self.cfg.max_tg_file_size:
            await self.flush()
        self.album.append(item)
        if len(self.album) == ALBUM_SIZE:
            await self.flush()

    async def flush(self) -> None:
        if self.album:
            items, self.album = self.album, []
            await self._send(items)

    async def _send(self, items: list[Item]) -> None:
        if missing := [item for item in items if not item.path.exists()]:  # shouldn't happen, but must not crash the job
            log.warning("Job %s: %d file(s) disappeared before sending: %s", self.job.id, len(missing), missing[0].path.name)
            self.failed += len(missing)
            items = [item for item in items if item.path.exists()]
            if not items:
                return
        sizes = [item.size for item in items]
        size = sum(sizes)
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
            for item, item_size in zip(items, sizes, strict=True):
                observe.ITEMS_SENT.labels(item.kind).inc()
                observe.BYTES_SENT.labels(item.kind).inc(item_size)
        finally:
            for item in items:
                item.path.unlink(missing_ok=True)

    def _file(self, item: Item):
        # A local Bot API server reads the file from disk by its file:// URI, the cloud one needs it uploaded:
        return item.path if self.cfg.local_mode else item.path.read_bytes()

    def _url(self, item: Item, playlist: bool = False) -> str:
        """Where an item came from: the link that was sent, or with NORMALIZE_URLS the post's (or playlist's) clean link."""
        if not self.cfg.normalize_urls:
            return self.job.url
        return (playlist and item.playlist_url) or item.source_url or clean_url(self.job.url, self.job.site)

    def _source(self, item: Item) -> str:
        url = html.escape(self._url(item, playlist=True))
        return f'@{html.escape(self.bot.username)} <i>got it from</i> <a href="{url}">{html.escape(self.job.site)}</a>'

    async def send_text(self, item: Item) -> None:
        """A post without media, like a text-only tweet: its text is the content, so it goes out even without captions."""
        lines = [self._source(item)] if self.job.captions else []
        if author := author_line(item):
            lines.append(author)
        lines.append(f"<blockquote expandable>{html.escape((item.description or '').strip()[:TEXT_LIMIT])}</blockquote>")
        try:
            await self.bot.send_message(
                self.job.chat_id, "\n".join(lines), parse_mode=ParseMode.HTML, reply_parameters=self.reply, link_preview_options=LinkPreviewOptions(is_disabled=True)
            )
        except Forbidden:
            raise
        except TelegramError as exc:
            log.warning("Job %s: could not send text: %s", self.job.id, exc)
            observe.TELEGRAM_ERRORS.labels(type(exc).__name__).inc()
            self.failed += 1
        else:
            self.sent += 1
            observe.ITEMS_SENT.labels("text").inc()

    def _caption(self, item: Item, first: bool = True) -> str | None:
        lines = []
        if item.part:
            lines.append(f"Part {item.part[0]} of {item.part[1]}")
        if first and self.job.captions and not self.sent:  # source info once per job
            lines.append(self._source(item))
            if item.kind != AUDIO:  # audio has performer and title tags instead
                if author := author_line(item):
                    lines.append(author)
                if description := (item.description or "").strip()[:DESCRIPTION_LIMIT]:
                    lines.append(f"<blockquote expandable>{html.escape(description)}</blockquote>")
        caption = "\n".join(lines)
        if self.cfg.hidden_source_links:  # on every file, even in albums and with captions off
            caption = HIDDEN_LINK.format(html.escape(self._url(item))) + caption
        return caption or None

    async def _send_one(self, item: Item, timeout: float) -> None:
        common = {"caption": self._caption(item), "parse_mode": ParseMode.HTML, "duration": item.duration, "reply_parameters": self.reply}
        timeouts = {"read_timeout": timeout, "write_timeout": timeout}
        if item.kind == PHOTO:
            del common["duration"]
            await self.bot.send_photo(self.job.chat_id, self._file(item), filename=item.path.name, **common, **timeouts)
        elif item.kind == VIDEO:
            video = self._file(item)
            await self.bot.send_video(self.job.chat_id, video, width=item.width, height=item.height, supports_streaming=True, **common, **timeouts)
        else:
            audio = self._file(item)
            await self.bot.send_audio(self.job.chat_id, audio, performer=item.performer, title=item.title, filename=item.path.name, **common, **timeouts)

    def _media(self, item: Item, first: bool) -> InputMediaAudio | InputMediaPhoto | InputMediaVideo:
        common = {"caption": self._caption(item, first), "parse_mode": ParseMode.HTML, "duration": item.duration}
        if item.kind == PHOTO:
            del common["duration"]
            return InputMediaPhoto(self._file(item), filename=item.path.name, **common)
        if item.kind == VIDEO:
            return InputMediaVideo(self._file(item), width=item.width, height=item.height, supports_streaming=True, filename=item.path.name, **common)
        return InputMediaAudio(self._file(item), performer=item.performer, title=item.title, filename=item.path.name, **common)


def author_line(item: Item) -> str | None:
    """'Author: <b>Display Name</b> (@handle)', the handle linked to the profile.

    The link matters: a bare @handle would look like a Telegram username to Telegram apps.
    """
    name = (item.author_name or "").strip()
    if not item.author_handle:
        return f"Author: <b>{html.escape(name)}</b>" if name else None
    handle = f"@{html.escape(item.author_handle)}"
    handle = f'<a href="{html.escape(item.author_url)}">{handle}</a>' if item.author_url else f"<code>{handle}</code>"
    if name and name.lstrip("@").lower() != item.author_handle.lower():
        return f"Author: <b>{html.escape(name)}</b> ({handle})"
    return f"Author: {handle}"


def _size(item: Item) -> int:
    """Size of a file that may have disappeared (then _send skips it)."""
    try:
        return item.size
    except FileNotFoundError:
        return 0
