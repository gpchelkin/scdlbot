"""Telegram update handlers. They stay fast: parse, write a row, enqueue, reply. Downloads happen in jobs.py."""

import logging
from dataclasses import dataclass, field

import httpx
from telegram import (
    Chat,
    ChatMember,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
    MessageEntity,
    ReplyParameters,
    Update,
    User,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, NetworkError, TelegramError
from telegram.ext import ContextTypes

from scdlbot import db, observe, texts
from scdlbot.config import Config
from scdlbot.db import DB, ChatSettings, Job
from scdlbot.jobs import JobQueue
from scdlbot.links import Link, find_links, resolve

log = logging.getLogger(__name__)
HTML = ParseMode.HTML


@dataclass
class Runtime:
    """Everything handlers need, stored in application.bot_data["runtime"]."""

    cfg: Config
    db: DB
    queue: JobQueue
    http: httpx.AsyncClient
    tasks: list = field(default_factory=list)


def runtime(context: ContextTypes.DEFAULT_TYPE) -> Runtime:
    return context.bot_data["runtime"]


async def chat_settings(rt: Runtime, chat: Chat) -> ChatSettings:
    return await rt.db.touch_chat(chat.id, chat.type, default_captions=chat.id not in rt.cfg.no_flood_chat_ids)


def reply_to(message_id: int) -> ReplyParameters:
    return ReplyParameters(message_id, allow_sending_without_reply=True)


# /start, /help, /stats


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rt = runtime(context)
    observe.UPDATES.labels("help").inc()
    if update.effective_user:
        await rt.db.touch_user(update.effective_user.id, update.effective_user.language_code)
    text = texts.HELP.format(max_items=rt.cfg.max_playlist_items, bot=context.bot.username)
    await context.bot.send_message(update.effective_chat.id, text, parse_mode=HTML, link_preview_options=LinkPreviewOptions(is_disabled=True))


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rt = runtime(context)
    if not update.effective_user or update.effective_user.id != rt.cfg.owner_id:
        return
    users = await rt.db.active_users()
    today, week = await rt.db.jobs_by_status(db.today()), await rt.db.jobs_by_status(db.today(6))
    sites = await rt.db.top_sites(db.today(6))
    month = users["month"] or 1
    languages = ", ".join(f"{lang or '?'} {count * 100 // month}%" for lang, count in (await rt.db.languages())[:10])
    lines = [
        f"<b>Active users</b>: day {users['day']}, week {users['week']}, month {users['month']}",
        "<b>Queue</b>: " + ", ".join(f"{pool} {q.qsize()} waiting, {rt.queue.busy[pool]}/{rt.queue.pools[pool]} busy" for pool, q in rt.queue.queues.items()),
        "<b>Jobs today</b>: " + (", ".join(f"{k} {v}" for k, v in today.items()) or "none"),
        "<b>Jobs 7 days</b>: " + (", ".join(f"{k} {v}" for k, v in week.items()) or "none"),
        "<b>Top sites 7 days</b> (done / all):",
        *(f"  {site}: {done} / {count}" for site, count, done in sites),
        f"<b>App languages 30 days</b>: {languages or 'none'}",
    ]
    await context.bot.send_message(update.effective_chat.id, "\n".join(lines), parse_mode=HTML)


# /settings


def settings_keyboard(settings: ChatSettings) -> InlineKeyboardMarkup:
    def radio(mode: str, label: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(f"{'🟢' if settings.mode == mode else '🟡'} {label}", callback_data=f"settings:{mode}")

    def toggle(key: str, value: bool, label: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(f"{'✅' if value else '❌'} {label}", callback_data=f"settings:{key}")

    return InlineKeyboardMarkup(
        [
            [radio("dl", "Download"), radio("ask", "Ask")],
            [toggle("unknown_sites", settings.unknown_sites, "Unknown sites"), toggle("captions", settings.captions, "Captions")],
            [toggle("mp3", settings.mp3, "Always MP3")],
            [InlineKeyboardButton("❌ Close settings", callback_data="settings:close")],
        ]
    )


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rt = runtime(context)
    observe.UPDATES.labels("settings").inc()
    settings = await chat_settings(rt, update.effective_chat)
    await context.bot.send_message(update.effective_chat.id, texts.SETTINGS, parse_mode=HTML, reply_markup=settings_keyboard(settings))


async def settings_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rt = runtime(context)
    query, chat = update.callback_query, update.effective_chat
    if not rt.cfg.chat_allowed(chat.id):
        await query.answer(texts.NOT_ALLOWED)
        return
    if chat.type != Chat.PRIVATE and query.from_user.id != rt.cfg.owner_id:
        member = await chat.get_member(query.from_user.id)
        if member.status not in (ChatMember.OWNER, ChatMember.ADMINISTRATOR):
            await query.answer(texts.NOT_ADMIN)
            return
    action = query.data.removeprefix("settings:")
    observe.UPDATES.labels(f"settings_{action}").inc()
    if action == "close":
        await query.answer()
        await query.message.delete()
        return
    settings = await chat_settings(rt, chat)
    if action in ("dl", "ask") and action != settings.mode:
        await rt.db.set_chat(chat.id, mode=action)
    elif action == "captions":
        await rt.db.set_chat(chat.id, captions=not settings.captions)
    elif action == "unknown_sites":
        await rt.db.set_chat(chat.id, unknown_sites=not settings.unknown_sites)
    elif action == "mp3":
        await rt.db.set_chat(chat.id, mp3=not settings.mp3)
    else:
        await query.answer(texts.SETTINGS_NOT_CHANGED)
        return
    await query.answer(texts.SETTINGS_CHANGED)
    await query.edit_message_reply_markup(settings_keyboard(await chat_settings(rt, chat)))


# Messages with links and /dl


async def links_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # "@bot <links>" is an explicit request, like /dl:
    await handle_links(update, context, trigger="mention" if mentions_bot(update.effective_message, context.bot) else "msg")


async def dl_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await handle_links(update, context, trigger="cmd")


async def mention_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """ "@bot" in reply to a message with links: e.g. the links were sent while the bot was down or not in the chat yet."""
    if mentions_bot(update.effective_message, context.bot):
        await handle_links(update, context, trigger="mention")


def mentions_bot(message: Message, bot) -> bool:
    username = f"@{bot.username}".lower()
    types = [MessageEntity.MENTION, MessageEntity.TEXT_MENTION]
    for entities in (message.parse_entities(types), message.parse_caption_entities(types)):
        for entity, text in entities.items():
            if text.lower() == username or (entity.user and entity.user.id == bot.id):
                return True
    return False


def is_own_forward(message: Message, bot_id: int) -> bool:
    origin = message.forward_origin
    return origin is not None and getattr(getattr(origin, "sender_user", None), "id", None) == bot_id


async def handle_links(update: Update, context: ContextTypes.DEFAULT_TYPE, trigger: str) -> None:
    """trigger: "msg" (a message with links), "cmd" (/dl) or "mention" (@bot). The last two download right away, whatever the mode."""
    rt = runtime(context)
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    explicit = trigger != "msg"
    if not rt.cfg.chat_allowed(chat.id):
        if explicit:
            await message.reply_text(texts.NOT_ALLOWED)
        return
    if is_own_forward(message, context.bot.id):
        return
    settings = await chat_settings(rt, chat)
    source = message
    links = find_links(message, rt.cfg, settings.unknown_sites)
    if explicit and not links and message.reply_to_message:  # "/dl" or "@bot" in reply to a message with links
        source = message.reply_to_message
        links = find_links(source, rt.cfg, settings.unknown_sites)
    links = await resolve_short_links(rt, links, settings.unknown_sites)
    mode = "dl" if explicit else settings.mode
    observe.UPDATES.labels(f"{mode}_{trigger}").inc()
    if user:
        await rt.db.touch_user(user.id, user.language_code)
    if not links:
        if (explicit and (context.args or source is not message)) or (not explicit and chat.type == Chat.PRIVATE):
            await context.bot.send_message(chat.id, texts.NO_URLS, parse_mode=HTML, reply_parameters=reply_to(message.message_id))
        return
    if mode == "ask":
        await ask(rt, context, chat, user, source, links, settings)
    else:
        await enqueue(rt, context, chat, user, source, links, settings)


async def resolve_short_links(rt: Runtime, links: list[Link], unknown_sites: bool) -> list[Link]:
    resolved = []
    for link in links:
        if link.shortener:
            link = await resolve(link, rt.cfg, rt.http, unknown_sites)
        if link and link.url not in {r.url for r in resolved}:
            resolved.append(link)
    return resolved


def new_job(chat: Chat, user: User | None, source: Message, link: Link, settings: ChatSettings, status: str, wait_message_id: int | None = None) -> Job:
    return Job(
        chat_id=chat.id,
        chat_type=chat.type,
        user_id=user.id if user else None,
        message_id=source.message_id,
        url=link.url,
        site=link.site,
        kind=link.kind,
        captions=settings.captions,
        status=status,
        wait_message_id=wait_message_id,
        mp3=settings.mp3,
    )


async def enqueue(rt: Runtime, context, chat: Chat, user: User | None, source: Message, links: list[Link], settings: ChatSettings) -> None:
    allowed, reason = rt.queue.room_for(user.id if user else chat.id, len(links))
    if reason:
        observe.REJECTED.labels(reason).inc(len(links) - allowed)
    if not allowed:
        text = texts.BUSY if reason == "queue" else texts.USER_LIMIT.format(count=rt.cfg.max_user_jobs)
        await context.bot.send_message(chat.id, text, parse_mode=HTML, reply_parameters=reply_to(source.message_id))
        return
    waiting = rt.queue.waiting(links[0].site)
    wait_text = texts.wait() + (f" <i>(#{waiting} in queue)</i>" if waiting else "")
    wait = await context.bot.send_message(chat.id, wait_text, parse_mode=HTML, reply_parameters=reply_to(source.message_id))
    for link in links[:allowed]:
        rt.queue.put(await rt.db.add_job(new_job(chat, user, source, link, settings, "queued", wait.message_id)))


async def ask(rt: Runtime, context, chat: Chat, user: User | None, source: Message, links: list[Link], settings: ChatSettings) -> None:
    for link in links:
        await rt.db.add_job(new_job(chat, user, source, link, settings, "asked"))
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬇️ Download", callback_data=f"ask:dl:{source.message_id}"), InlineKeyboardButton("❌", callback_data=f"ask:no:{source.message_id}")]]
    )
    await context.bot.send_message(chat.id, texts.ASK, reply_markup=keyboard, reply_parameters=reply_to(source.message_id))


async def ask_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rt = runtime(context)
    query, chat = update.callback_query, update.effective_chat
    _, action, message_id = query.data.split(":")
    observe.UPDATES.labels(f"ask_{action}").inc()
    if not rt.cfg.chat_allowed(chat.id):
        await query.answer(texts.NOT_ALLOWED)
        return
    if action != "dl":
        await rt.db.cancel_asked(chat.id, int(message_id))
        await query.answer()
        await delete_quietly(query.message)
        return
    jobs = await rt.db.queue_asked(chat.id, int(message_id), wait_message_id=query.message.message_id)
    if not jobs:
        await query.answer(texts.OLD_MESSAGE)
        await delete_quietly(query.message)
        return
    allowed, reason = rt.queue.room_for(jobs[0].owner, len(jobs))
    for job in jobs[allowed:]:
        job.status, job.error = "rejected", reason
        await rt.db.finish_job(job)
        observe.REJECTED.labels(reason).inc()
    if not allowed:
        await query.answer(texts.BUSY if reason == "queue" else texts.USER_LIMIT.format(count=rt.cfg.max_user_jobs), show_alert=True)
        await delete_quietly(query.message)
        return
    await query.answer(texts.WAIT[0])
    await query.edit_message_text(texts.wait(), parse_mode=HTML)
    for job in jobs[:allowed]:
        rt.queue.put(job)


async def old_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Buttons from the previous bot version."""
    await update.callback_query.answer(texts.OLD_MESSAGE)
    await delete_quietly(update.callback_query.message)


async def delete_quietly(message: Message | None) -> None:
    try:
        if message:
            await message.delete()
    except TelegramError:
        pass


# Chat membership and errors


async def my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rt = runtime(context)
    change = update.my_chat_member
    if change.new_chat_member.status in (ChatMember.LEFT, ChatMember.BANNED):  # removed from a group, or blocked in private
        await rt.db.chat_left(change.chat.id)
    elif not rt.cfg.chat_allowed(change.chat.id):
        await context.bot.leave_chat(change.chat.id)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    error = context.error
    if isinstance(error, (Forbidden, BadRequest, NetworkError)):  # routine: blocked bot, deleted message, network blip
        log.warning("Telegram error while handling an update: %s", error)
    else:
        log.error("Error while handling an update", exc_info=error)
