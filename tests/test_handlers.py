from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from telegram import CallbackQuery, Chat, Message, MessageEntity, Update, User

from scdlbot import handlers
from scdlbot.config import Config
from scdlbot.db import DB
from scdlbot.handlers import Runtime
from scdlbot.jobs import JobQueue
from scdlbot.routes import RoutePool, from_env

from .test_jobs import FakeBot

USER = User(7, "Ann", is_bot=False, language_code="ru")
LINKS = "https://soundcloud.com/a/b https://youtu.be/x"
ENTITIES = [MessageEntity(MessageEntity.URL, 0, 26), MessageEntity(MessageEntity.URL, 27, 18)]


@pytest.fixture
async def env():
    cfg = Config(max_user_jobs=5)
    db = await DB.open(":memory:")
    bot = FakeBot()
    queue = JobQueue(cfg, db, bot, RoutePool(*from_env(cfg)))
    rt = Runtime(cfg, db, queue, httpx.AsyncClient())
    context = SimpleNamespace(bot=bot, bot_data={"runtime": rt}, args=[])
    yield rt, bot, context
    await rt.http.aclose()
    await db.close()


def message(chat: Chat, text: str = LINKS, entities=ENTITIES, message_id: int = 10, **kwargs) -> Message:
    return Message(message_id, datetime.now(UTC), chat, from_user=USER, text=text, entities=entities, **kwargs)


async def auto_mode(rt: Runtime, chat: Chat) -> None:
    await rt.db.touch_chat(chat.id, chat.type, default_captions=True)
    await rt.db.set_chat(chat.id, mode="dl")


async def test_private_chats_download_and_ask_about_video_by_default(env):
    rt, bot, context = env
    await handlers.links_message(Update(1, message=message(Chat(7, Chat.PRIVATE))), context)
    assert rt.queue.size() == 1  # SoundCloud; the YouTube link waits for 🎵 or 🎬
    assert bot.names() == ["send_message", "send_message"]  # "wait a bit", then the question
    assert await rt.db.active_users() == {"day": 1, "week": 1, "month": 1}
    assert await rt.db.languages() == [("ru", 1)]


async def test_ask_mode_in_group_then_press_download(env):
    rt, bot, context = env
    group = Chat(-100, Chat.SUPERGROUP)
    await handlers.links_message(Update(1, message=message(group)), context)
    assert rt.queue.size() == 0
    name, _, kwargs = bot.calls[-1]
    assert name == "send_message" and kwargs["reply_markup"].inline_keyboard[0][0].callback_data == "ask:dl:10"

    prompt = message(group, text="🎶 links found, what to do?", entities=[], message_id=11)
    prompt.set_bot(bot)
    query = CallbackQuery("q1", USER, "chat", message=prompt, data="ask:dl:10")
    query.set_bot(bot)
    await handlers.ask_button(Update(2, callback_query=query), context)
    assert rt.queue.size() == 2
    assert "edit_message_text" in bot.names()

    await handlers.ask_button(Update(3, callback_query=query), context)  # pressed again: nothing left to queue
    assert rt.queue.size() == 2


async def test_dl_reply_and_no_links(env):
    rt, bot, context = env
    chat = Chat(-100, Chat.SUPERGROUP)
    command = message(chat, text="/dl", entities=[MessageEntity(MessageEntity.BOT_COMMAND, 0, 3)], message_id=12, reply_to_message=message(chat))
    await handlers.dl_command(Update(1, message=command), context)
    assert rt.queue.size() == 2
    job = rt.queue.queues["main"].get_nowait()  # the SoundCloud link; youtu.be went to the youtube pool
    assert job.message_id == 10  # files will reply to the message with the links

    await handlers.links_message(Update(2, message=message(Chat(7, Chat.PRIVATE), text="https://example.com", entities=[MessageEntity(MessageEntity.URL, 0, 19)])), context)
    assert "no supported links" in bot.calls[-1][1][1]


async def test_user_limit(env):
    rt, bot, context = env
    rt.cfg = Config(max_user_jobs=1)
    rt.queue.cfg = rt.cfg
    await auto_mode(rt, Chat(7, Chat.PRIVATE))
    await handlers.links_message(Update(1, message=message(Chat(7, Chat.PRIVATE))), context)
    assert rt.queue.size() == 1
    await handlers.links_message(Update(2, message=message(Chat(7, Chat.PRIVATE), message_id=13)), context)
    assert rt.queue.size() == 1
    assert "downloads in progress" in bot.calls[-1][1][1]


async def test_stats_for_owner_only(env):
    rt, bot, context = env
    await auto_mode(rt, Chat(7, Chat.PRIVATE))
    await handlers.links_message(Update(1, message=message(Chat(7, Chat.PRIVATE))), context)
    await handlers.stats_command(Update(2, message=message(Chat(7, Chat.PRIVATE), text="/stats", entities=[])), context)
    assert bot.names() == ["send_message"]  # only the "wait a bit" reply: user 7 is not the owner
    rt.cfg = Config(owner_id=7)
    await handlers.stats_command(Update(3, message=message(Chat(7, Chat.PRIVATE), text="/stats", entities=[])), context)
    text = bot.calls[-1][1][1]
    assert "30 days 1" in text and "App languages 30 days</b>: ru 100%" in text
    assert "<b>Last 24 hours</b>: 2 links from 1 users\n  Jobs: queued 2\n  Files: none" in text


async def test_mention_in_reply_downloads_now_even_in_ask_mode(env):
    rt, bot, context = env
    group = Chat(-100, Chat.SUPERGROUP)  # Ask mode by default
    mention = message(group, text="@ScdlBot please", entities=[MessageEntity(MessageEntity.MENTION, 0, 8)], message_id=12, reply_to_message=message(group))
    await handlers.mention_reply(Update(1, message=mention), context)
    assert rt.queue.size() == 2  # both links of the replied message, no Ask prompt
    assert rt.queue.queues["main"].get_nowait().message_id == 10  # files reply to the message with the links


async def test_mention_with_links_skips_ask(env):
    rt, bot, context = env
    group = Chat(-100, Chat.SUPERGROUP)
    text = "@scdlbot " + LINKS
    entities = [MessageEntity(MessageEntity.MENTION, 0, 8), *(MessageEntity(e.type, e.offset + 9, e.length) for e in ENTITIES)]
    await handlers.links_message(Update(1, message=message(group, text=text, entities=entities)), context)
    assert rt.queue.size() == 2


async def test_other_mentions_are_ignored(env):
    rt, bot, context = env
    group = Chat(-100, Chat.SUPERGROUP)
    mention = message(group, text="@someone look", entities=[MessageEntity(MessageEntity.MENTION, 0, 8)], message_id=12, reply_to_message=message(group))
    await handlers.mention_reply(Update(1, message=mention), context)
    assert rt.queue.size() == 0 and bot.calls == []


async def test_privacy_command_and_help_link_to_the_policy(env):
    rt, bot, context = env
    chat = Chat(7, Chat.PRIVATE)
    await handlers.privacy_command(Update(1, message=message(chat, text="/privacy", entities=[])), context)
    assert 'href="https://telegram.org/privacy-tpa"' in bot.calls[-1][1][1]  # Telegram's standard policy, until the operator sets one
    rt.cfg = Config(privacy_policy_url="https://example.com/privacy?a=1&b=2", jobs_retention_days=30)
    await handlers.privacy_command(Update(2, message=message(chat, text="/privacy", entities=[])), context)
    await handlers.help_command(Update(3, message=message(chat, text="/help", entities=[])), context)
    privacy, help_text = bot.calls[-2][1][1], bot.calls[-1][1][1]
    policy = 'href="https://example.com/privacy?a=1&amp;b=2"'
    assert policy in privacy and "README.md#disclaimer" in privacy and "for 30 days" in privacy
    assert policy in help_text and "/privacy" in help_text and "electrocircle_online" in help_text


def queued(rt: Runtime) -> list[tuple[str, str]]:
    return sorted((job.site, job.kind) for queue in rt.queue.queues.values() for job in list(queue._queue))


def buttons(bot) -> list[str]:
    return [button.callback_data for button in bot.calls[-1][2]["reply_markup"].inline_keyboard[0]]


async def press(rt: Runtime, bot, context, chat: Chat, data: str) -> None:
    prompt = message(chat, text="🎶 links found, what to do?", entities=[], message_id=99)
    prompt.set_bot(bot)
    query = CallbackQuery("q1", USER, "chat", message=prompt, data=data)
    query.set_bot(bot)
    await handlers.ask_button(Update(3, callback_query=query), context)


async def test_video_only_on_request(env):
    rt, bot, context = env
    private, group = Chat(7, Chat.PRIVATE), Chat(-100, Chat.SUPERGROUP)
    await auto_mode(rt, private)
    await handlers.links_message(Update(1, message=message(private)), context)
    assert queued(rt) == [("SoundCloud", "audio"), ("YouTube", "audio")]

    command = message(private, text="/video", entities=[MessageEntity(MessageEntity.BOT_COMMAND, 0, 6)], message_id=12, reply_to_message=message(private))
    await handlers.video_command(Update(5, message=command), context)
    assert queued(rt) == [("SoundCloud", "audio"), ("SoundCloud", "audio"), ("YouTube", "audio"), ("YouTube", "video")]

    await handlers.links_message(Update(2, message=message(group, message_id=20)), context)  # Ask by default in groups
    assert buttons(bot) == ["ask:dl:20", "ask:video:20", "ask:no:20"]
    await press(rt, bot, context, group, "ask:video:20")
    rows = await rt.db._all("SELECT site, kind FROM jobs WHERE message_id = 20 ORDER BY site")
    assert [tuple(row) for row in rows] == [("SoundCloud", "audio"), ("YouTube", "video")]  # only the link that has a video

    sc = "https://soundcloud.com/a/b"
    await handlers.links_message(Update(4, message=message(group, text=sc, entities=[MessageEntity(MessageEntity.URL, 0, len(sc))], message_id=30)), context)
    assert buttons(bot) == ["ask:dl:30", "ask:no:30"]  # audio only: nothing to choose


async def test_auto_and_ask_about_video_mode(env):
    rt, bot, context = env
    group = Chat(-100, Chat.SUPERGROUP)
    await rt.db.touch_chat(group.id, group.type, default_captions=True)
    await rt.db.set_chat(group.id, mode="ask_video")
    await handlers.links_message(Update(1, message=message(group, message_id=20)), context)
    assert queued(rt) == [("SoundCloud", "audio")]  # downloaded right away
    assert bot.names() == ["send_message", "send_message"]  # "wait a bit", then the question about the YouTube link
    assert buttons(bot) == ["ask:dl:20", "ask:video:20", "ask:no:20"]
    await press(rt, bot, context, group, "ask:video:20")
    assert queued(rt) == [("SoundCloud", "audio"), ("YouTube", "video")]

    await handlers.links_message(Update(4, message=message(group, text=LINKS.split()[0], entities=ENTITIES[:1], message_id=30)), context)
    assert "reply_markup" not in bot.calls[-1][2]  # "wait a bit": nothing with a video, nothing to ask
    assert len(queued(rt)) == 3


async def test_settings_mode_buttons(env):
    rt, bot, context = env
    chat = Chat(7, Chat.PRIVATE)
    await handlers.settings_command(Update(1, message=message(chat, text="/settings", entities=[])), context)
    assert "for <b>private chats</b> (like this one) and <b>Ask before downloading</b> for <b>group chats</b>." in bot.calls[-1][1][1]
    prompt = message(chat, text="settings", entities=[], message_id=40)
    prompt.set_bot(bot)
    query = CallbackQuery("q2", USER, "chat", message=prompt, data="settings:dl")
    query.set_bot(bot)
    await handlers.settings_button(Update(2, callback_query=query), context)
    assert (await rt.db.touch_chat(7, "private", default_captions=True)).mode == "dl"
    keyboard = bot.calls[-1][2]["reply_markup"].inline_keyboard
    assert [row[0].text for row in keyboard[:3]] == ["🔘 Auto-download all", "⚪ Auto-download + ask about video", "⚪ Ask before downloading"]
    assert keyboard[3][0].text == "⬜ Unknown sites"
