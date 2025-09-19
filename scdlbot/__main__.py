#!/usr/bin/env python

import asyncio
import concurrent.futures
import datetime
import importlib
import logging
import os
import pathlib
import pickle
import platform
import random
import re
import resource
import shutil
import tempfile
import threading
import time
import traceback
from types import ModuleType
from typing import Any, MutableMapping, Union, cast
from importlib import resources
from logging.handlers import SysLogHandler
from multiprocessing import get_context
from subprocess import PIPE, TimeoutExpired  # skipcq: BAN-B404
from urllib.parse import urljoin
from uuid import uuid4

import ffmpeg
import prometheus_client
import requests
import sdnotify

# import gc
# from boltons.urlutils import find_all_links
from fake_useragent import UserAgent
from mutagen.id3 import ID3, ID3v1SaveOptions
from mutagen.mp3 import EasyMP3 as MP3
from pebble import ProcessPool
from telegram import Bot, Chat, ChatMember, InlineKeyboardButton, InlineKeyboardMarkup, Message, MessageEntity, Update
from telegram.constants import ChatAction

# from telegram.error import BadRequest, ChatMigrated, Forbidden, NetworkError, TelegramError, TimedOut
from telegram.ext import AIORateLimiter, Application, ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, PicklePersistence, filters
from telegram.helpers import escape_markdown
from telegram.request import HTTPXRequest

# from telegram_handler import TelegramHandler

# Support different old versions just in case:
# https://github.com/yt-dlp/yt-dlp/wiki/Forks


def _load_downloader_module() -> ModuleType:
    for candidate in ("yt_dlp", "youtube_dl", "youtube_dlc"):
        try:
            return importlib.import_module(candidate)
        except ImportError:
            continue
    raise ImportError("No supported downloader module available")


ydl = cast(Any, _load_downloader_module())


from boltons.urlutils import URL
from plumbum import ProcessExecutionError, local
from plumbum.machines.local import LocalCommand
from telegram.error import TelegramError

from scdlbot.ffprobe import FFprobeError, probe_media

# Use maximum 1500 mebibytes per task:
# TODO Parametrize?
MAX_MEM = 1500 * 1024 * 1024


def require_chat(update: Update) -> Chat:
    chat = update.effective_chat
    if chat is None:
        raise RuntimeError("Update does not contain chat information")
    return chat


def require_message(update: Update) -> Message:
    message = update.effective_message
    if message is None:
        raise RuntimeError("Update does not contain a message")
    return message


def require_chat_data(context: ContextTypes.DEFAULT_TYPE) -> MutableMapping[str, Any]:
    chat_data = context.chat_data
    if chat_data is None:
        raise RuntimeError("Chat data is not available")
    return chat_data


TG_BOT_TOKEN = os.environ["TG_BOT_TOKEN"]
TG_BOT_API = os.getenv("TG_BOT_API", "https://api.telegram.org")
# https://github.com/python-telegram-bot/python-telegram-bot/wiki/Local-Bot-API-Server
# https://github.com/tdlib/telegram-bot-api#usage
TG_BOT_API_LOCAL_MODE = False
if "TG_BOT_API_LOCAL_MODE" in os.environ:
    TG_BOT_API_LOCAL_MODE = bool(int(os.getenv("TG_BOT_API_LOCAL_MODE", "0")))
elif "127.0.0.1" in TG_BOT_API or "localhost" in TG_BOT_API:
    TG_BOT_API_LOCAL_MODE = True
HTTP_VERSION = "2"
if TG_BOT_API_LOCAL_MODE:
    HTTP_VERSION = "1.1"
TG_BOT_OWNER_CHAT_ID = int(os.getenv("TG_BOT_OWNER_CHAT_ID", "0"))

CHAT_STORAGE = os.path.expanduser(os.getenv("CHAT_STORAGE", "/tmp/scdlbot.pickle"))
DL_DIR = os.path.expanduser(os.getenv("DL_DIR", "/tmp/scdlbot"))
BIN_PATH = os.getenv("BIN_PATH", "")
scdl_bin = local[os.path.join(BIN_PATH, "scdl")]
bcdl_bin = local[os.path.join(BIN_PATH, "bandcamp-dl")]
BCDL_ENABLE = True
WORKERS = int(os.getenv("WORKERS", 2))
# TODO 'fork' is prohibited, doesn't work. Maybe change to 'spawn' on all platforms
mp_method = "forkserver"
if platform.system() == "Windows":
    mp_method = "spawn"
# https://stackoverflow.com/a/66113051
# https://superfastpython.com/processpoolexecutor-multiprocessing-context/
# https://docs.python.org/3/library/multiprocessing.html#contexts-and-start-methods
# https://docs.python.org/3/library/concurrent.futures.html#concurrent.futures.ProcessPoolExecutor
# EXECUTOR = concurrent.futures.ProcessPoolExecutor(max_workers=WORKERS, mp_context=get_context(method=mp_method))
# IMPORTANT: Using Union type hint to avoid undefined Executor type
EXECUTOR: Union[ProcessPool, concurrent.futures.ProcessPoolExecutor] = ProcessPool(initargs=[MAX_MEM], max_workers=WORKERS, max_tasks=20, context=get_context(method=mp_method))  # type: ignore[assignment]
# EXECUTOR = ProcessPool(max_workers=WORKERS, max_tasks=20, context=get_context(method=mp_method))
DL_TIMEOUT = int(os.getenv("DL_TIMEOUT", 300))
CHECK_URL_TIMEOUT = int(os.getenv("CHECK_URL_TIMEOUT", 30))
# Timeouts: https://www.python-httpx.org/advanced/
COMMON_CONNECTION_TIMEOUT = int(os.getenv("COMMON_CONNECTION_TIMEOUT", 10))
MAX_TG_FILE_SIZE = int(os.getenv("MAX_TG_FILE_SIZE", "45_000_000"))
MAX_CONVERT_FILE_SIZE = int(os.getenv("MAX_CONVERT_FILE_SIZE", "80_000_000"))
NO_FLOOD_CHAT_IDS = list(map(int, os.getenv("NO_FLOOD_CHAT_IDS", "0").split(",")))
COOKIES_FILE = os.getenv("COOKIES_FILE", None)
PROXIES = []
proxies_env = os.getenv("PROXIES")
if proxies_env:
    PROXIES = [None if proxy == "direct" else proxy for proxy in proxies_env.split(",") if proxy]
SOURCE_IPS = []
source_ips_env = os.getenv("SOURCE_IPS")
if source_ips_env:
    SOURCE_IPS = [ip for ip in source_ips_env.split(",") if ip]
BLACKLIST_TELEGRAM_DOMAINS = [
    "telegram.org",
    "telegram.me",
    "t.me",
    "telegram.dog",
    "telegra.ph",
    "te.legra.ph",
    "graph.org",
    "tdesktop.com",
    "desktop.telegram.org",
    "telesco.pe",
    "contest.com",
    "contest.dev",
]
WHITELIST_DOMAINS: set[str] = set()
whitelist_domains_env = os.getenv("WHITELIST_DOMAINS")
if whitelist_domains_env:
    WHITELIST_DOMAINS = {domain for domain in whitelist_domains_env.split(",") if domain}
BLACKLIST_DOMAINS: set[str] = set()
blacklist_domains_env = os.getenv("BLACKLIST_DOMAINS")
if blacklist_domains_env:
    BLACKLIST_DOMAINS = {domain for domain in blacklist_domains_env.split(",") if domain}
WHITELIST_CHATS: set[int] = set()
whitelist_chats_env = os.getenv("WHITELIST_CHATS")
if whitelist_chats_env:
    try:
        WHITELIST_CHATS = {int(chat_id) for chat_id in whitelist_chats_env.split(",") if chat_id}
    except ValueError:
        raise ValueError("Your whitelisted chats list does not contain valid integers.")
BLACKLIST_CHATS: set[int] = set()
blacklist_chats_env = os.getenv("BLACKLIST_CHATS")
if blacklist_chats_env:
    try:
        BLACKLIST_CHATS = {int(chat_id) for chat_id in blacklist_chats_env.split(",") if chat_id}
    except ValueError:
        raise ValueError("Your blacklisted chats list does not contain valid integers.")

# Webhook:
WEBHOOK_ENABLE = bool(int(os.getenv("WEBHOOK_ENABLE", "0")))
WEBHOOK_HOST = os.getenv("HOST", "127.0.0.1")
WEBHOOK_PORT = int(os.getenv("PORT", "5000"))
WEBHOOK_APP_URL_ROOT = os.getenv("WEBHOOK_APP_URL_ROOT", "")
WEBHOOK_APP_URL_PATH = os.getenv("WEBHOOK_APP_URL_PATH", TG_BOT_TOKEN.replace(":", ""))
WEBHOOK_CERT_FILE = os.getenv("WEBHOOK_CERT_FILE", None)
WEBHOOK_KEY_FILE = os.getenv("WEBHOOK_KEY_FILE", None)
WEBHOOK_SECRET_TOKEN = os.getenv("WEBHOOK_SECRET_TOKEN", None)

# Prometheus metrics:
METRICS_HOST = os.getenv("METRICS_HOST", "127.0.0.1")
METRICS_PORT = int(os.getenv("METRICS_PORT", "8000"))
REGISTRY = prometheus_client.CollectorRegistry()
EXECUTOR_TASKS_REMAINING = prometheus_client.Gauge(
    "executor_tasks_remaining",
    "Value: executor_tasks_remaining",
    registry=REGISTRY,
)
BOT_REQUESTS = prometheus_client.Counter(
    "bot_requests_total",
    "Value: bot_requests_total",
    labelnames=["type", "chat_type", "mode"],
    registry=REGISTRY,
)

# Logging:
logging_handlers = []
LOGLEVEL = os.getenv("LOGLEVEL", "INFO").upper()
HOSTNAME = os.getenv("HOSTNAME", "scdlbot-host")

console_formatter = logging.Formatter("[%(name)s] %(levelname)s: %(message)s")
console_handler = logging.StreamHandler()
console_handler.setFormatter(console_formatter)
console_handler.setLevel(LOGLEVEL)
logging_handlers.append(console_handler)

SYSLOG_ADDRESS = os.getenv("SYSLOG_ADDRESS", None)
if SYSLOG_ADDRESS:
    syslog_formatter = logging.Formatter("%(asctime)s " + HOSTNAME + " %(name)s: %(message)s", datefmt="%b %d %H:%M:%S")
    syslog_host, syslog_udp_port = SYSLOG_ADDRESS.split(":")
    syslog_handler = SysLogHandler(address=(syslog_host, int(syslog_udp_port)))
    syslog_handler.setFormatter(syslog_formatter)
    syslog_handler.setLevel(LOGLEVEL)
    logging_handlers.append(syslog_handler)

# telegram_handler = TelegramHandler(token=TG_BOT_TOKEN, chat_id=str(TG_BOT_OWNER_CHAT_ID))
# telegram_handler.setLevel(logging.WARNING)
# logging_handlers.append(telegram_handler)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=LOGLEVEL,
    handlers=logging_handlers,
)
logger = logging.getLogger(__name__)

# Systemd watchdog monitoring:
SYSTEMD_NOTIFIER = sdnotify.SystemdNotifier()

# Randomize User-Agent:
# https://github.com/intoli/user-agents/tree/main/src
## https://user-agents.net/download
## https://user-agents.net/my-user-agent
UA = UserAgent(browsers=["Google", "Chrome", "Firefox", "Edge"], platforms=["desktop"], os=["Windows", "Linux", "Ubuntu"])


# Text constants from resources:
def get_response_text(file_name):
    # https://stackoverflow.com/a/20885799/2490759
    # https://docs.python.org/3/library/importlib.resources.html
    return resources.files("scdlbot").joinpath("texts").joinpath(file_name).read_text(encoding="UTF-8")


HELP_TEXT = get_response_text("help.tg.md")
SETTINGS_TEXT = get_response_text("settings.tg.md")
DL_TIMEOUT_TEXT = get_response_text("dl_timeout.txt").format(DL_TIMEOUT // 60)
WAIT_BIT_TEXT = [get_response_text("wait_bit.txt"), get_response_text("wait_beat.txt"), get_response_text("wait_beet.txt")]
NO_URLS_TEXT = get_response_text("no_urls.txt")
FAILED_TEXT = get_response_text("failed.txt")
REGION_RESTRICTION_TEXT = get_response_text("region_restriction.txt")
DIRECT_RESTRICTION_TEXT = get_response_text("direct_restriction.txt")
LIVE_RESTRICTION_TEXT = get_response_text("live_restriction.txt")
OLD_MSG_TEXT = get_response_text("old_msg.txt")
# RANT_TEXT_PRIVATE = "Read /help to learn how to use me"
# RANT_TEXT_PUBLIC = f"[Start me in PM to read help and learn how to use me](t.me/{TG_BOT_USERNAME}?start=1)"

# Known and supported site domains:
DOMAIN_SC = "soundcloud.com"
DOMAIN_SC_ON = "on.soundcloud.com"
DOMAIN_SC_API = "api.soundcloud.com"
DOMAIN_SC_GOOGL = "soundcloud.app.goo.gl"
DOMAIN_BC = "bandcamp.com"
DOMAIN_YT = "youtube.com"
DOMAIN_YT_BE = "youtu.be"
DOMAIN_YMR = "music.yandex.ru"
DOMAIN_YMC = "music.yandex.com"
DOMAIN_TT = "tiktok.com"
DOMAIN_IG = "instagram.com"
DOMAIN_TW = "twitter.com"
DOMAIN_TWX = "x.com"
DOMAINS_STRINGS = [DOMAIN_SC, DOMAIN_SC_ON, DOMAIN_SC_API, DOMAIN_SC_GOOGL, DOMAIN_BC, DOMAIN_YT, DOMAIN_YT_BE, DOMAIN_YMR, DOMAIN_YMC, DOMAIN_TT, DOMAIN_IG, DOMAIN_TW, DOMAIN_TWX]
DOMAINS = [rf"^(?:[^\s]+\.)?{re.escape(domain_string)}$" for domain_string in DOMAINS_STRINGS]

AUDIO_FORMATS = ["mp3"]
VIDEO_FORMATS = ["m4a", "mp4", "webm"]


# TODO get rid of these dumb exceptions:
class FileNotSupportedError(Exception):
    def __init__(self, file_format):
        self.file_format = file_format


class FileTooLargeError(Exception):
    def __init__(self, file_size):
        self.file_size = file_size


class FileSplittedPartiallyError(Exception):
    def __init__(self, file_parts):
        self.file_parts = file_parts


class FileNotConvertedError(Exception):
    def __init__(self):
        pass


class FileSentPartiallyError(Exception):
    def __init__(self, sent_audio_ids):
        self.sent_audio_ids = sent_audio_ids


def get_random_wait_text():
    return random.choice(WAIT_BIT_TEXT)


def get_link_text(urls):
    link_text = ""
    for i, url in enumerate(urls):
        link_text += "[Source Link #{}]({}) | `{}`\n".format(str(i + 1), url, URL(url).host)
        # TODO split long link message to multiple ones
        direct_urls = urls[url].splitlines()[:3]
        for idx, direct_url in enumerate(direct_urls):
            if direct_url.startswith("http"):
                content_type = ""
                if "googlevideo" in direct_url:
                    if "audio" in direct_url:
                        content_type = "Audio"
                    else:
                        content_type = "Video"
                link_text += "• {} #{} [Direct Link]({})\n".format(content_type, str(idx + 1), direct_url)
    link_text += "\n*Note:* Final download URLs are only guaranteed to work on the same machine/IP where extracted"
    return link_text


def get_settings_inline_keyboard(chat_data):
    mode = chat_data["settings"]["mode"]
    flood = chat_data["settings"]["flood"]
    allow_unknown_sites = chat_data["settings"]["allow_unknown_sites"]
    emoji_radio_selected = "🟢"
    emoji_radio_unselected = "🟡"
    emoji_toggle_enabled = "✅"
    emoji_toggle_disabled = "❌"
    emoji_close = "❌"
    button_dl = InlineKeyboardButton(text=" ".join([emoji_radio_selected if mode == "dl" else emoji_radio_unselected, "Download"]), callback_data=" ".join(["settings", "dl"]))
    button_link = InlineKeyboardButton(text=" ".join([emoji_radio_selected if mode == "link" else emoji_radio_unselected, "Links"]), callback_data=" ".join(["settings", "link"]))
    button_ask = InlineKeyboardButton(text=" ".join([emoji_radio_selected if mode == "ask" else emoji_radio_unselected, "Ask"]), callback_data=" ".join(["settings", "ask"]))
    button_flood = InlineKeyboardButton(text=" ".join([emoji_toggle_enabled if flood else emoji_toggle_disabled, "Captions"]), callback_data=" ".join(["settings", "flood"]))
    button_allow_unknown_sites = InlineKeyboardButton(
        text=" ".join([emoji_toggle_enabled if allow_unknown_sites else emoji_toggle_disabled, "Unknown sites"]), callback_data=" ".join(["settings", "allow_unknown_sites"])
    )
    button_close = InlineKeyboardButton(text=" ".join([emoji_close, "Close settings"]), callback_data=" ".join(["settings", "close"]))
    inline_keyboard = InlineKeyboardMarkup([[button_dl, button_link, button_ask], [button_allow_unknown_sites, button_flood], [button_close]])
    return inline_keyboard


def chat_allowed(chat_id):
    if WHITELIST_CHATS:
        if chat_id not in WHITELIST_CHATS:
            return False
    if BLACKLIST_CHATS:
        if chat_id in BLACKLIST_CHATS:
            return False
    return True


def url_valid_and_allowed(url, allow_unknown_sites=False):
    host = url.host
    if host in BLACKLIST_TELEGRAM_DOMAINS:
        return False
    if WHITELIST_DOMAINS:
        if host not in WHITELIST_DOMAINS:
            return False
    if BLACKLIST_DOMAINS:
        if host in BLACKLIST_DOMAINS:
            return False
    if allow_unknown_sites:
        return True
    if any((re.match(domain, host) for domain in DOMAINS)):
        return True
    else:
        return False


async def start_help_commands_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = require_chat(update)
    message = require_message(update)
    chat_id = chat.id
    chat_type = chat.type
    command_name = "help"
    # Determine the original command:
    entities = message.parse_entities(types=[MessageEntity.BOT_COMMAND])
    for entity_value in entities.values():
        command_name = entity_value.replace("/", "").replace(f"@{context.bot.username}", "").lower()
        break
    logger.debug(command_name)
    BOT_REQUESTS.labels(type=command_name, chat_type=chat_type, mode="None").inc()
    await context.bot.send_message(chat_id=chat_id, text=HELP_TEXT, parse_mode="Markdown", disable_web_page_preview=True)


async def settings_command_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    command_name = "settings"
    chat = require_chat(update)
    chat_id = chat.id
    chat_type = chat.type
    logger.debug(command_name)
    BOT_REQUESTS.labels(type=command_name, chat_type=chat_type, mode="None").inc()
    chat_data = require_chat_data(context)
    init_chat_data(
        chat_data=chat_data,
        mode=("dl" if chat_type == Chat.PRIVATE else "ask"),
        flood=(chat_id not in NO_FLOOD_CHAT_IDS),
    )
    await context.bot.send_message(chat_id=chat_id, parse_mode="Markdown", reply_markup=get_settings_inline_keyboard(chat_data), text=SETTINGS_TEXT)


async def dl_link_commands_and_messages_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = require_chat(update)
    message = require_message(update)
    chat_id = chat.id
    chat_type = chat.type
    if not chat_allowed(chat_id):
        await context.bot.send_message(chat_id=chat_id, text="This command isn't allowed in this chat.")
        return
    chat_data = require_chat_data(context)
    init_chat_data(
        chat_data=chat_data,
        mode=("dl" if chat_type == Chat.PRIVATE else "ask"),
        flood=(chat_id not in NO_FLOOD_CHAT_IDS),
    )
    # Determine the original command:
    command_entities = message.parse_entities(types=[MessageEntity.BOT_COMMAND])
    settings_data = chat_data["settings"]
    allow_unknown_sites = settings_data["allow_unknown_sites"]
    mode = settings_data["mode"]
    command_passed = False
    action = None
    if command_entities:
        command_passed = True
        # Try to determine action from command:
        action = None
        for entity_value in command_entities.values():
            action = entity_value.replace("/", "").replace("@{}".format(context.bot.username), "").lower()
            break
    # If no command then it is just a message and use message action from settings:
    if not action:
        action = mode
    if action == "silent":
        return
    if command_passed and not context.args:
        # TODO rant for empty commands?
        # rant_text = RANT_TEXT_PRIVATE if chat_type == Chat.PRIVATE else RANT_TEXT_PUBLIC
        # rant_text += "\nYou can simply send message with links (to download) OR command as `/{} <links>`.".format(mode)
        # rant_and_cleanup(context.bot, chat_id, rant_text, reply_to_message_id=reply_to_message_id)
        return
    command_name = f"{action}_cmd" if command_passed else f"{action}_msg"
    logger.debug(command_name)
    BOT_REQUESTS.labels(type=command_name, chat_type=chat_type, mode=mode).inc()
    apologize = False
    # Apologize for fails: always in PM; only when it was explicit command in non-PM:
    if chat_type == Chat.PRIVATE or command_passed:
        apologize = True
    reply_to_message_id = message.message_id
    source_ip = None
    if SOURCE_IPS:
        source_ip = random.choice(SOURCE_IPS)
    proxy = None
    if PROXIES:
        proxy = random.choice(PROXIES)
    wait_message_id = None
    if action in ["dl", "link"]:
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        wait_message = await context.bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id, parse_mode="Markdown", text=f"_{get_random_wait_text()}_")
        wait_message_id = wait_message.message_id

    urls_dict = {}

    # Get our main running asyncio loop:
    loop_main = asyncio.get_running_loop()

    # a) Run heavy task blocking the main running asyncio loop.
    # Needs to have timeout signals in function, but they are bad.
    # urls_dict = get_direct_urls_dict(message, action, proxy, source_ip, allow_unknown_sites)

    # b) Run heavy task in separate process or thread, but without blocking the main running asyncio loop.
    # Function will continue working till the end: https://stackoverflow.com/a/34457515/2490759
    # You may add timeout signals in function, but they are bad.
    # If ThreadPoolExecutor - no signals.
    # We monitor EXECUTOR process pool task queue, so we use it.

    # pool = concurrent.futures.ThreadPoolExecutor()
    try:
        # https://docs.python.org/3/library/asyncio-eventloop.html#asyncio.loop.run_in_executor
        # https://docs.python.org/3/library/asyncio-task.html#asyncio.wait_for
        # urls_dict = await asyncio.wait_for(
        #     loop_main.run_in_executor(EXECUTOR, get_direct_urls_dict, message, action, proxy, source_ip, allow_unknown_sites),
        #     timeout=CHECK_URL_TIMEOUT * 10,
        # )
        urls_dict = await loop_main.run_in_executor(
            cast(Any, EXECUTOR), get_direct_urls_dict, message, action, proxy, source_ip, allow_unknown_sites
        )  # IMPORTANT: Cast EXECUTOR for type compatibility
    except asyncio.TimeoutError:
        logger.debug("get_direct_urls_dict took too much time and was dropped (but still running)")
    except Exception:
        logger.debug("get_direct_urls_dict failed for some unhandled reason")
    # pool.shutdown(wait=False, cancel_futures=True)

    logger.debug(f"prepare_urls: urls dict: {urls_dict}")
    urls_values = " ".join(urls_dict.values())

    # Continue only if any good direct url status exist (or if we deal with known sites):
    if action == "dl":
        if not urls_dict:
            if apologize:
                await context.bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id, text=NO_URLS_TEXT, parse_mode="Markdown")
            if wait_message_id is not None:
                await context.bot.delete_message(chat_id=chat_id, message_id=wait_message_id)
        else:
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            for url in urls_dict:
                direct_urls_status = urls_dict[url]
                if direct_urls_status in ["failed", "restrict_direct", "restrict_region", "restrict_live", "timeout"]:
                    if direct_urls_status == "failed":
                        await context.bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id, text=FAILED_TEXT, parse_mode="Markdown")
                    elif direct_urls_status == "timeout":
                        await context.bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id, text=DL_TIMEOUT_TEXT, parse_mode="Markdown")
                    elif direct_urls_status == "restrict_direct":
                        await context.bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id, text=DIRECT_RESTRICTION_TEXT, parse_mode="Markdown")
                    elif direct_urls_status == "restrict_region":
                        await context.bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id, text=REGION_RESTRICTION_TEXT, parse_mode="Markdown")
                    elif direct_urls_status == "restrict_live":
                        await context.bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id, text=LIVE_RESTRICTION_TEXT, parse_mode="Markdown")
                else:
                    kwargs = {
                        "bot_options": {
                            "token": context.bot.token,
                            "base_url": context.bot.base_url.split("/bot")[0] + "/bot",
                            "base_file_url": context.bot.base_file_url.split("/file/bot")[0] + "/file/bot",
                            "local_mode": context.bot.local_mode,
                        },
                        "chat_id": chat_id,
                        "url": url,
                        "flood": chat_data["settings"]["flood"],
                        "reply_to_message_id": reply_to_message_id,
                        "wait_message_id": wait_message_id,
                        "cookies_file": COOKIES_FILE,
                        "source_ip": source_ip,
                        "proxy": proxy,
                    }
                    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.RECORD_VOICE)
                    # Run heavy task in separate process, "fire and forget":
                    # EXECUTOR.submit(download_url_and_send, **kwargs)
                    EXECUTOR.schedule(download_url_and_send, kwargs=kwargs, timeout=DL_TIMEOUT)

    elif action == "link":
        if "http" not in urls_values:
            if apologize:
                await context.bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id, text=NO_URLS_TEXT, parse_mode="Markdown")
        else:
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            await context.bot.send_message(
                chat_id=chat_id, reply_to_message_id=reply_to_message_id, parse_mode="Markdown", disable_web_page_preview=True, text=get_link_text(urls_dict)
            )
        if wait_message_id is not None:
            await context.bot.delete_message(chat_id=chat_id, message_id=wait_message_id)
    elif action == "ask":
        if "http" not in urls_values:
            if apologize:
                await context.bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id, text=NO_URLS_TEXT, parse_mode="Markdown")
        else:
            url_message_id = str(reply_to_message_id)
            chat_data[url_message_id] = {"urls": urls_dict, "source_ip": source_ip, "proxy": proxy}
            question = "🎶 links found, what to do?"
            button_dl = InlineKeyboardButton(text="⬇️ Download", callback_data=" ".join([url_message_id, "dl"]))
            button_link = InlineKeyboardButton(text="🔗️ Get links", callback_data=" ".join([url_message_id, "link"]))
            button_cancel = InlineKeyboardButton(text="❌", callback_data=" ".join([url_message_id, "cancel"]))
            inline_keyboard = InlineKeyboardMarkup([[button_dl, button_link, button_cancel]])
            await context.bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id, reply_markup=inline_keyboard, text=question)


async def button_press_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    callback_query = update.callback_query
    if callback_query is None:
        raise RuntimeError("Callback query is required for button handling")
    button_message = callback_query.message
    if button_message is None:
        raise RuntimeError("Callback query message is missing")
    button_message_id = button_message.message_id
    user_id = callback_query.from_user.id
    chat = require_chat(update)
    chat_id = chat.id
    chat_type = chat.type
    chat_data = require_chat_data(context)
    data = callback_query.data
    if data is None:
        raise RuntimeError("Callback data is missing")
    # get message id and action from button data:
    # TODO create separate callbacks by callback query data pattern
    url_message_id, button_action = data.split()
    if not chat_allowed(chat_id):
        await callback_query.answer(text="This command isn't allowed in this chat.")
        return
    if url_message_id == "settings":
        # button on settings message:
        if chat_type != Chat.PRIVATE:
            chat_member = await chat.get_member(user_id)
            # logger.debug(chat_member.status)
            if chat_member.status not in [ChatMember.OWNER, ChatMember.ADMINISTRATOR] and user_id != TG_BOT_OWNER_CHAT_ID:
                logger.debug("settings_fail")
                await callback_query.answer(text="You're not chat admin.")
                return
        command_name = f"settings_{button_action}"
        logger.debug(command_name)
        BOT_REQUESTS.labels(type=command_name, chat_type=chat_type, mode="None").inc()
        if button_action == "close":
            await context.bot.delete_message(chat_id, button_message_id)
        else:
            setting_changed = False
            if button_action in ["dl", "link", "ask"]:
                # Radio buttons:
                current_setting = chat_data["settings"]["mode"]
                if button_action != current_setting:
                    setting_changed = True
                    chat_data["settings"]["mode"] = button_action
            elif button_action in ["flood", "allow_unknown_sites"]:
                # Toggles:
                current_setting = chat_data["settings"][button_action]
                chat_data["settings"][button_action] = not current_setting
                setting_changed = True
            if setting_changed:
                await callback_query.answer(text="Settings changed")
                await callback_query.edit_message_reply_markup(reply_markup=get_settings_inline_keyboard(chat_data))
            else:
                await callback_query.answer(text="Settings not changed")

    elif url_message_id in chat_data:
        # mode is ask, we got data from button on asking message.
        # if it asked, then we were in prepare_urls:
        url_message_data = chat_data.pop(url_message_id)
        urls_dict = url_message_data["urls"]
        command_name = f"{button_action}_msg"
        logger.debug(command_name)
        BOT_REQUESTS.labels(type=command_name, chat_type=chat_type, mode="ask").inc()
        if button_action == "dl":
            await callback_query.answer(text=get_random_wait_text())
            wait_message = await callback_query.edit_message_text(parse_mode="Markdown", text=f"_{get_random_wait_text()}_")
            for url in urls_dict:
                kwargs = {
                    "bot_options": {
                        "token": context.bot.token,
                        "base_url": context.bot.base_url.split("/bot")[0] + "/bot",
                        "base_file_url": context.bot.base_file_url.split("/file/bot")[0] + "/file/bot",
                        "local_mode": context.bot.local_mode,
                    },
                    "chat_id": chat_id,
                    "url": url,
                    "flood": chat_data["settings"]["flood"],
                    "reply_to_message_id": url_message_id,
                    "wait_message_id": wait_message.message_id if wait_message and isinstance(wait_message, Message) else None,  # IMPORTANT: Fix type issue
                    "cookies_file": COOKIES_FILE,
                    "source_ip": url_message_data["source_ip"],
                    "proxy": url_message_data["proxy"],
                }
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.RECORD_VOICE)
                # Run heavy task in separate process, "fire and forget":
                # EXECUTOR.submit(download_url_and_send, **kwargs)
                EXECUTOR.schedule(download_url_and_send, kwargs=kwargs, timeout=DL_TIMEOUT)

        elif button_action == "link":
            await context.bot.send_message(
                chat_id=chat_id, reply_to_message_id=int(url_message_id), parse_mode="Markdown", disable_web_page_preview=True, text=get_link_text(urls_dict)
            )  # IMPORTANT: Convert url_message_id to int
            await context.bot.delete_message(chat_id=chat_id, message_id=button_message_id)
        elif button_action == "cancel":
            await context.bot.delete_message(chat_id=chat_id, message_id=button_message_id)
    else:
        await callback_query.answer(text=OLD_MSG_TEXT)
        await context.bot.delete_message(chat_id=chat_id, message_id=button_message_id)


async def blacklist_whitelist_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # IMPORTANT: Add null check for effective_chat
    if update.effective_chat is None:
        return
    chat_id = update.effective_chat.id
    if not chat_allowed(chat_id):
        await context.bot.leave_chat(chat_id)


async def unknown_command_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return


async def error_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):  # skipcq: PYL-R0201
    # https://github.com/python-telegram-bot/python-telegram-bot/blob/master/examples/errorhandlerbot.py#L29
    # TODO send telegram message to bot owner as well
    # Log the error before we do anything else, so we can see it even if something breaks.
    logger.error("Exception while handling an update:", exc_info=context.error)

    # traceback.format_exception returns the usual python message about an exception, but as a
    # list of strings rather than a single string, so we have to join them together.
    # IMPORTANT: Add proper type check for context.error
    if context.error is not None:
        tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
    else:
        tb_list = ["Unknown error occurred"]
    tb_string = "".join(tb_list)
    logger.debug(tb_string)

    # try:
    #     raise context.error
    # except Forbidden:
    #     # remove update.message.chat_id from conversation list
    #     logger.debug(f"Update {update} caused Forbidden error: {context.error}")
    # except BadRequest:
    #     # handle malformed requests - read more below!
    #     logger.debug(f"Update {update} caused BadRequest error: {context.error}")
    # except TimedOut:
    #     # handle slow connection problems
    #     logger.debug(f"Update {update} caused TimedOut error: {context.error}")
    # except NetworkError:
    #     # handle other connection problems
    #     logger.debug(f"Update {update} caused NetworkError error: {context.error}")
    # except ChatMigrated as e:
    #     # the chat_id of a group has changed, use e.new_chat_id instead
    #     logger.debug(f"Update {update} caused ChatMigrated error: {context.error}")
    # except TelegramError:
    #     # handle all other telegram related errors
    #     logger.debug(f"Update {update} caused TelegramError error: {context.error}")


def init_chat_data(chat_data, mode="dl", flood=True):
    if "settings" not in chat_data:
        chat_data["settings"] = {}
    if "mode" not in chat_data["settings"]:
        chat_data["settings"]["mode"] = mode
    if "flood" not in chat_data["settings"]:
        chat_data["settings"]["flood"] = flood
    if "allow_unknown_sites" not in chat_data["settings"]:
        chat_data["settings"]["allow_unknown_sites"] = False


def get_direct_urls_dict(message, mode, proxy, source_ip, allow_unknown_sites):
    # If telegram message passed:
    urls = []
    url_entities = message.parse_entities(types=[MessageEntity.URL])
    url_caption_entities = message.parse_caption_entities(types=[MessageEntity.URL])
    url_entities.update(url_caption_entities)
    for entity in url_entities:
        url_str = url_entities[entity]
        if "://" not in url_str:
            url_str = "http://" + url_str
        try:
            url = URL(url_str)
            if url_valid_and_allowed(url, allow_unknown_sites=allow_unknown_sites):
                logger.info("Entity URL parsed: %s", url)
                urls.append(url)
            else:
                logger.info("Entity URL is not valid or blacklisted: %s", url_str)
        except:
            logger.info("Entity URL is not valid: %s", url_str)
    text_link_entities = message.parse_entities(types=[MessageEntity.TEXT_LINK])
    text_link_caption_entities = message.parse_caption_entities(types=[MessageEntity.TEXT_LINK])
    text_link_entities.update(text_link_caption_entities)
    for entity in text_link_entities:
        url = URL(entity.url)
        if url_valid_and_allowed(url, allow_unknown_sites=allow_unknown_sites):
            logger.info("Entity Text Link parsed: %s", url)
            urls.append(url)
        else:
            logger.info("Entity Text Link is not valid or blacklisted: %s", url)
    # If message just some text passed (not isinstance(message, Message)):
    # all_links = find_all_links(message, default_scheme="http")
    # urls = [link for link in all_links if url_valid_and_allowed(link)]
    logger.info(f"prepare_urls: urls list: {urls}")

    urls_dict = {}
    for url_item in urls:
        unknown_site = not any((re.match(domain, url_item.host) for domain in DOMAINS))
        # Unshorten soundcloud.app.goo.gl and unknown sites links. Example: https://soundcloud.app.goo.gl/mBMvG
        # FIXME spotdl to transform spotify link to youtube music link?
        # TODO Unshorten unknown sites links again? Because yt-dlp may only support unshortened?
        # if unknown_site or DOMAIN_SC_GOOGL in url_item.host:
        if DOMAIN_SC_GOOGL in url_item.host or DOMAIN_SC_ON in url_item.host:
            proxy_args = None
            if proxy:
                proxy_args = {"http": proxy, "https": proxy}
            try:
                url = URL(
                    requests.head(
                        url_item.to_text(full_quote=True),
                        allow_redirects=True,
                        timeout=2,
                        proxies=proxy_args,
                        headers={"User-Agent": UA.random},
                        # headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"},
                    ).url
                )
            except:
                url = url_item
        else:
            url = url_item
        unknown_site = not any((re.match(domain, url.host) for domain in DOMAINS))
        url_text = url.to_text(full_quote=True)
        logger.debug(f"Unshortened link: {url_text}")
        # url_text = url_text.replace("m.soundcloud.com", "soundcloud.com")
        url_parts_num = len([part for part in url.path_parts if part])
        if unknown_site or mode == "link":
            # We run it if it was explicitly requested as per "link" mode.
            # We run it for links from unknown sites (if they were allowed).
            # FIXME For now we avoid extra requests on asking just to improve responsiveness. We are okay with useless asking (for unknown sites). Link mode might be removed.
            # If it's a known site, we check it more thoroughly below.
            # urls_dict[url_text] = ydl_get_direct_urls(url_text, COOKIES_FILE, source_ip, proxy)
            urls_dict[url_text] = "http"
        elif (
            ((DOMAIN_SC in url.host) and (2 <= url_parts_num <= 4) and (not "you" in url.path_parts) and (not "likes" in url.path_parts))
            or (DOMAIN_SC_GOOGL in url.host)
            or (DOMAIN_SC_API in url.host)
        ):
            # SoundCloud: tracks, sets and widget pages, no /you/ pages
            # TODO support private sets URLs that have 5 parts
            # We know for sure these links can be downloaded, so we just skip running ydl_get_direct_urls
            urls_dict[url_text] = "http"
        elif DOMAIN_BC in url.host and (2 <= url_parts_num <= 2):
            # Bandcamp: tracks and albums
            # We know for sure these links can be downloaded, so we just skip running ydl_get_direct_urls
            urls_dict[url_text] = "http"
        elif ((DOMAIN_YT in url.host) and ("watch" in url.path or "playlist" in url.path)) or (DOMAIN_YT_BE in url.host):
            # YouTube: videos and playlists
            # We still run it for checking YouTube region restriction to avoid useless asking.
            # FIXME For now we avoid extra requests on asking just to improve responsiveness. We are okay with useless asking (for youtube).
            # urls_dict[url_text] = ydl_get_direct_urls(url_text, COOKIES_FILE, source_ip, proxy)
            urls_dict[url_text] = "http"
        elif DOMAIN_YMR in url.host or DOMAIN_YMC in url.host:
            # YM: tracks. Note that the domain includes x.com..
            # We know for sure these links can be downloaded, so we just skip running ydl_get_direct_urls
            urls_dict[url_text] = "http"
        elif DOMAIN_TT in url.host:
            # TikTok: videos
            # We know for sure these links can be downloaded, so we just skip running ydl_get_direct_urls
            urls_dict[url_text] = "http"
        elif DOMAIN_IG in url.host and (2 <= url_parts_num):
            # Instagram: videos, reels
            # We run it for checking Instagram ban to avoid useless asking.
            # FIXME For now we avoid extra requests on asking just to improve responsiveness. We are okay with useless asking (for instagram).
            # urls_dict[url_text] = ydl_get_direct_urls(url_text, COOKIES_FILE, source_ip, proxy)
            urls_dict[url_text] = "http"
        elif (DOMAIN_TW in url.host or DOMAIN_TWX in url.host) and (DOMAIN_YMC not in url.host) and (3 <= url_parts_num <= 3):
            # Twitter: videos
            # We know for sure these links can be downloaded, so we just skip running ydl_get_direct_urls
            urls_dict[url_text] = "http"
    return urls_dict


def ydl_get_direct_urls(url, cookies_file=None, source_ip=None, proxy=None):
    # TODO transform into unified ydl function and deduplicate
    logger.debug("Entering: ydl_get_direct_urls: %s", url)
    status = ""
    cmd_name = "ydl_get_direct_urls"
    ydl_opts = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "skip_download": True,
        # "forceprint": {"before_dl":}
    }
    if proxy:
        ydl_opts["proxy"] = proxy
    if source_ip:
        ydl_opts["source_address"] = source_ip
    cookies_download_file = None
    if cookies_file:
        cookies_download_file = tempfile.NamedTemporaryFile(mode="wb", delete=False)
        cookies_download_file_path = pathlib.Path(cookies_download_file.name)
        if cookies_file.startswith("http"):
            # URL for downloading cookie file:
            try:
                r = requests.get(cookies_file, allow_redirects=True, timeout=5)
                cookies_download_file.write(r.content)
                cookies_download_file.close()
                ydl_opts["cookiefile"] = str(cookies_download_file_path)
            except:
                logger.debug("download_url_and_send could not download cookies file")
                pass
        elif cookies_file.startswith("firefox:"):
            # TODO handle env var better
            cookies_file_components = cookies_file.split(":", maxsplit=2)
            if len(cookies_file_components) == 3:
                cookies_sqlite_file = cookies_file_components[2]
                cookies_download_sqlite_path = pathlib.Path.home() / ".mozilla" / "firefox" / cookies_file_components[1] / "cookies.sqlite"
                # URL for downloading cookie sqlite file:
                try:
                    r = requests.get(cookies_sqlite_file, allow_redirects=True, timeout=5)
                    with open(cookies_download_sqlite_path, "wb") as cfile:
                        cfile.write(r.content)
                    ydl_opts["cookiesfrombrowser"] = ("firefox", cookies_file_components[1], None, None)
                    logger.debug("download_url_and_send downloaded cookies.sqlite file")
                except:
                    logger.debug("download_url_and_send could not download cookies.sqlite file")
                    pass
            else:
                ydl_opts["cookiesfrombrowser"] = ("firefox", cookies_file_components[1], None, None)
        else:
            # cookie file local path:
            cookies_download_file.write(open(cookies_file, "rb").read())
            cookies_download_file.close()
            ydl_opts["cookiefile"] = str(cookies_download_file_path)

    logger.debug("%s starts: %s", cmd_name, url)
    try:
        # https://github.com/yt-dlp/yt-dlp/blob/master/README.md#embedding-examples
        unsanitized_info_dict = ydl.YoutubeDL(ydl_opts).extract_info(url, download=False)
        info_dict = ydl.YoutubeDL(ydl_opts).sanitize_info(unsanitized_info_dict)
        # TODO actualize checks, fix for youtube playlists
        if "url" in info_dict:
            direct_url = info_dict["url"]
        elif "entries" in info_dict:
            direct_url = "\n".join([x["url"] for x in info_dict["entries"] if "url" in x])
        else:
            raise Exception()
        if "yt_live_broadcast" in direct_url:
            status = "restrict_live"
        elif "returning it as such" in direct_url:
            status = "restrict_direct"
        elif "proxy server" in direct_url:
            status = "restrict_region"
        # end actualize checks
        else:
            status = direct_url
            logger.debug("%s succeeded: %s", cmd_name, url)
    except Exception:
        logger.debug("%s failed: %s", cmd_name, url)
        logger.debug(traceback.format_exc())
        status = "failed"
    if cookies_file and cookies_download_file is not None:
        cookies_download_file.close()
        os.unlink(cookies_download_file.name)

    return status


def download_url_and_send(
    bot_options,
    chat_id,
    url,
    flood=False,
    reply_to_message_id=None,
    wait_message_id=None,
    cookies_file=None,
    source_ip=None,
    proxy=None,
):
    logger.debug("Entering: download_url_and_send")
    # loop_main = asyncio.get_event_loop()

    # We use ProcessPoolExecutor that runs "fork".
    # It has really useful fire-and-forget ProcessPoolExecutor.submit() that only accepts sync functions.
    # Also, there is no point in using asyncio event loop/async functions inside it.
    # Hence, download_url_and_send() is sync function, not async.
    # But we still want to use async Bot functions from ptb framework here.
    # We can't use loop_main.run_in_executor(None) because it doesn't return the result.
    # We can't use loop_main.run_until_complete() because loop is already running in our forked process.
    # We can't use asyncio.run_coroutine_threadsafe(coro, loop_main) because it doesn't work (interferes with framework?).
    # So we run additional loop in additional thread and just use it:
    loop_additional = asyncio.new_event_loop()
    thread_additional = threading.Thread(target=loop_additional.run_forever, name="Additional Async Runner", daemon=True)

    def run_async(coro):
        if not thread_additional.is_alive():
            thread_additional.start()
        future = asyncio.run_coroutine_threadsafe(coro, loop_additional)
        return future.result()

    # We must not pass context/bot here, because they need to get serialized/pickled on "fork" (and they cannot be).
    # https://docs.python-telegram-bot.org/en/v20.1/telegram.bot.html
    # So we create and use new Bot object:
    bot = Bot(
        token=bot_options["token"],
        base_url=bot_options["base_url"],
        base_file_url=bot_options["base_file_url"],
        local_mode=bot_options["local_mode"],
        request=HTTPXRequest(http_version=HTTP_VERSION),
        get_updates_request=HTTPXRequest(http_version=HTTP_VERSION),
    )
    run_async(bot.initialize())
    logger.debug(bot.token)
    download_dir = os.path.join(DL_DIR, str(uuid4()))
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
    if ((DOMAIN_SC in host or DOMAIN_SC_GOOGL in host) and DOMAIN_SC_API not in host) or (DOMAIN_BC in host and BCDL_ENABLE):
        # If link is sc/bc, we try scdl/bcdl first:
        if (DOMAIN_SC in host or DOMAIN_SC_GOOGL in host) and DOMAIN_SC_API not in host:
            cmd = scdl_bin
            cmd_name = str(cmd)
            cmd_args = (
                "-l",
                url,  # URL of track/playlist/user
                "-c",  # Continue if a music already exist
                "--path",
                download_dir,  # Download the music to a custom path
                "--onlymp3",  # Download only the mp3 file even if the track is Downloadable
                "--addtofile",  # Add the artist name to the filename if it isn't in the filename already
                "--addtimestamp",
                # Adds the timestamp of the creation of the track to the title (useful to sort chronologically)
                "--no-playlist-folder",
                # Download playlist tracks into directory, instead of making a playlist subfolder
                "--extract-artist",  # Set artist tag from title instead of username
            )
            cmd_input = None
        elif DOMAIN_BC in host and BCDL_ENABLE:
            cmd = bcdl_bin
            cmd_name = str(cmd)
            cmd_args = (
                "--base-dir",
                download_dir,  # Base location of which all files are downloaded
                "--template",
                "%{track} - %{artist} - %{title} [%{album}]",  # Output filename template
                "--overwrite",  # Overwrite tracks that already exist
                "--group",  # Use album/track Label as iTunes grouping
                "--embed-art",  # Embed album art (if available)
                "--no-slugify",  # Disable slugification of track, album, and artist names
                url,  # URL of album/track
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
            cmd_stdout, cmd_stderr = cmd_proc.communicate(input=cmd_input, timeout=DL_TIMEOUT)
            cmd_retcode = cmd_proc.returncode
            # listed are common scdl problems for one track with 0 retcode, all its log output goes to stderr (track may be in stdout):
            # https://github.com/scdl-org/scdl/issues/493
            # https://github.com/scdl-org/scdl/pull/494
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
        # If link is not sc/bc or scdl/bcdl just failed, we use ydl
        cmd_name = "ydl_download"
        # https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/YoutubeDL.py#L187
        # https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/utils/_utils.py
        ydl_opts = {
            # https://github.com/yt-dlp/yt-dlp#output-template
            # Default outtmpl is "%(title)s [%(id)s].%(ext)s"
            # Take first 16 symbols of title:
            "outtmpl": os.path.join(download_dir, "%(title).16s [%(id)s].%(ext)s"),
            "restrictfilenames": True,
            "windowsfilenames": True,
            "max_filesize": MAX_TG_FILE_SIZE * 3,
            # TODO Add optional parameter FFMPEG_PATH:
            # "ffmpeg_location": "/home/gpchelkin/.local/bin/",
            # "ffmpeg_location": "/usr/local/bin/",
            # "trim_file_name": 32,
        }
        if DOMAIN_TT in host:
            download_video = True
            ydl_opts["format"] = "mp4"
        elif (DOMAIN_TW in host or DOMAIN_TWX in host) and (DOMAIN_YMC not in host):
            download_video = True
            ydl_opts["format"] = "mp4"
        elif DOMAIN_IG in host:
            download_video = True
            ydl_opts.update(
                {
                    "format": "mp4",
                    "postprocessors": [
                        # Instagram usually gives VP9 or HEVC (x265/h265) video codec (when downloading with cookies).
                        #   VP9 doesn't play in Telegram iOS client;
                        #   # TODO Check and skip converting for HEVC? It seems to be used for 4K videos.
                        # We need to convert it to AVC (x264/h264) or HEVC (x265/h265) video (+ AAC audio) from Instagram videos.
                        # We went with AVC (x264/h264) for now.
                        # "FFmpegVideoConvertor" doesn't work here since the original file is already in mp4 format.
                        # We don't touch audio and just copy it here since it's probably OK in original. But we may want to change 'copy' to 'aac' later.
                        # yt-dlp --use-postprocessor FFmpegCopyStream --ppa copystream:"-codec:v libx264 -crf 24 -preset veryfast -codec:a copy -f mp4 -threads 1" ...
                        # https://github.com/yt-dlp/yt-dlp/issues/7607
                        # https://github.com/yt-dlp/yt-dlp/issues/5859
                        # https://github.com/yt-dlp/yt-dlp/issues/8904
                        # https://github.com/yt-dlp/yt-dlp/blob/master/devscripts/cli_to_api.py
                        # {"key": "FFmpegVideoConvertor", "preferedformat": "mp4"},
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
                    # https://old.reddit.com/r/youtubedl/comments/zh61bw/goal_is_to_download_audio_only_and_embed/
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
                # URL for downloading cookie file:
                try:
                    r = requests.get(cookies_file, allow_redirects=True, timeout=5)
                    cookies_download_file.write(r.content)
                    cookies_download_file.close()
                    ydl_opts["cookiefile"] = str(cookies_download_file_path)
                except:
                    logger.debug("download_url_and_send could not download cookies file")
                    pass
            elif cookies_file.startswith("firefox:"):
                cookies_file_components = cookies_file.split(":", maxsplit=2)
                if len(cookies_file_components) == 3:
                    cookies_sqlite_file = cookies_file_components[2]
                    cookies_download_sqlite_path = pathlib.Path.home() / ".mozilla" / "firefox" / cookies_file_components[1] / "cookies.sqlite"
                    # URL for downloading cookie sqlite file:
                    try:
                        r = requests.get(cookies_sqlite_file, allow_redirects=True, timeout=5)
                        with open(cookies_download_sqlite_path, "wb") as cfile:
                            cfile.write(r.content)
                        ydl_opts["cookiesfrombrowser"] = ("firefox", cookies_file_components[1], None, None)
                        logger.debug("download_url_and_send downloaded cookies.sqlite file")
                    except:
                        logger.debug("download_url_and_send could not download cookies.sqlite file")
                        pass
                else:
                    ydl_opts["cookiesfrombrowser"] = ("firefox", cookies_file_components[1], None, None)
            else:
                # cookie file local path:
                cookies_download_file.write(open(cookies_file, "rb").read())
                cookies_download_file.close()
                ydl_opts["cookiefile"] = str(cookies_download_file_path)

        logger.debug("%s starts: %s", cmd_name, url)
        try:
            # FIXME Check and proceed even with partial results - e.g. for playlists with only some videos failed (private or more) https://youtube.com/playlist?list=PL2C109776112A2BB3
            # https://github.com/yt-dlp/yt-dlp/blob/master/README.md#embedding-examples
            info_dict = ydl.YoutubeDL(ydl_opts).download([url])
            logger.debug("%s succeeded: %s", cmd_name, url)
            status = "success"
            if download_video:
                unsanitized_info_dict = ydl.YoutubeDL(ydl_opts).extract_info(url, download=False)
                info_dict = ydl.YoutubeDL(ydl_opts).sanitize_info(unsanitized_info_dict)
                if "description" in info_dict and info_dict["description"]:
                    # TODO handle right-to-left hashtags better (like https://www.instagram.com/reel/CtZbNhtrJv3/)
                    # TODO format as bold/link/quote
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
        # gc.collect()

    if status == "failed":
        run_async(bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id, text=FAILED_TEXT, parse_mode="Markdown"))
    elif status == "timeout":
        run_async(bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id, text=DL_TIMEOUT_TEXT, parse_mode="Markdown"))
    elif status == "success":
        file_list = []
        for d, dirs, files in os.walk(download_dir):
            for file in files:
                file_list.append(os.path.join(d, file))
        if not file_list:
            logger.debug("No files in dir: %s", download_dir)
            run_async(
                bot.send_message(
                    chat_id=chat_id, reply_to_message_id=reply_to_message_id, text="*Sorry*, I couldn't download any files from some of the provided links", parse_mode="Markdown"
                )
            )
        else:
            for file in sorted(file_list):
                file_name = os.path.split(file)[-1]
                file_parts = []
                try:
                    file_root, file_ext = os.path.splitext(file)
                    file_format = file_ext.replace(".", "").lower()
                    file_size = os.path.getsize(file)
                    if file_format not in AUDIO_FORMATS + VIDEO_FORMATS:
                        raise FileNotSupportedError(file_format)
                    # We convert if downloaded file is video (except tiktok, instagram, twitter):
                    if file_format in VIDEO_FORMATS and not download_video:
                        if file_size > MAX_CONVERT_FILE_SIZE:
                            raise FileTooLargeError(file_size)
                        logger.debug("Converting video format: %s", file)
                        try:
                            file_converted = file.replace(file_ext, ".mp3")
                            ffinput = ffmpeg.input(file)
                            # https://kkroening.github.io/ffmpeg-python/#ffmpeg.output
                            # We could set audio_bitrate="320k", but we don't need it now
                            ffmpeg.output(ffinput, file_converted, vn=None, threads=1).run()
                            file = file_converted
                            file_root, file_ext = os.path.splitext(file)
                            file_format = file_ext.replace(".", "").lower()
                            file_size = os.path.getsize(file)
                        except Exception:
                            raise FileNotConvertedError

                    file_parts = []
                    if file_size <= MAX_TG_FILE_SIZE:
                        file_parts.append(file)
                    else:
                        logger.debug("Splitting: %s", file)
                        id3 = None
                        try:
                            id3 = ID3(file, translate=False)
                        except:
                            pass

                        parts_number = file_size // MAX_TG_FILE_SIZE + 1

                        # https://github.com/c0decracker/video-splitter
                        # https://superuser.com/a/1354956/464797
                        try:
                            # file_duration = float(probe_media(file)["format"]["duration"])
                            part_size = file_size // parts_number
                            cur_position = 0
                            for i in range(parts_number):
                                file_part = file.replace(file_ext, ".part{}{}".format(str(i + 1), file_ext))
                                ffinput = ffmpeg.input(file)
                                if i == (parts_number - 1):
                                    ffmpeg.output(ffinput, file_part, codec="copy", vn=None, ss=cur_position, threads=1).run()
                                else:
                                    ffmpeg.output(ffinput, file_part, codec="copy", vn=None, ss=cur_position, fs=part_size, threads=1).run()
                                    probe_result = probe_media(file_part)
                                    part_duration = float(probe_result["format"]["duration"])
                                    cur_position += part_duration
                                if id3:
                                    try:
                                        id3.save(file_part, v1=ID3v1SaveOptions.CREATE, v2_version=4)
                                    except:
                                        pass
                                file_parts.append(file_part)
                        except Exception:
                            raise FileSplittedPartiallyError(file_parts)

                except FileNotSupportedError as exc:
                    # If format is not some extra garbage from downloaders:
                    if not (exc.file_format in ["m3u", "jpg", "jpeg", "png", "finished", "tmp"]):
                        logger.debug("Unsupported file format: %s", file_name)
                        run_async(
                            bot.send_message(
                                chat_id=chat_id,
                                reply_to_message_id=reply_to_message_id,
                                text="*Sorry*, downloaded file `{}` is in format I could not yet convert or send".format(file_name),
                                parse_mode="Markdown",
                            )
                        )
                except FileTooLargeError as exc:
                    logger.debug("Large file for convert: %s", file_name)
                    run_async(
                        bot.send_message(
                            chat_id=chat_id,
                            reply_to_message_id=reply_to_message_id,
                            text="*Sorry*, downloaded file `{}` is `{}` MB and it is larger than I could convert (`{} MB`)".format(
                                file_name, exc.file_size // 1000000, MAX_CONVERT_FILE_SIZE // 1000000
                            ),
                            parse_mode="Markdown",
                        )
                    )
                except FileSplittedPartiallyError as exc:
                    file_parts = exc.file_parts
                    logger.debug("Splitting failed: %s", file_name)
                    run_async(
                        bot.send_message(
                            chat_id=chat_id,
                            reply_to_message_id=reply_to_message_id,
                            text="*Sorry*, I do not have enough resources to convert the file `{}`..".format(file_name),
                            parse_mode="Markdown",
                        )
                    )
                except FileNotConvertedError as exc:
                    logger.debug("Splitting failed: %s", file_name)
                    run_async(
                        bot.send_message(
                            chat_id=chat_id,
                            reply_to_message_id=reply_to_message_id,
                            text="*Sorry*, I do not have enough resources to convert the file `{}`..".format(file_name),
                            parse_mode="Markdown",
                        )
                    )
                caption = None
                reply_to_message_id_send = None
                if flood:
                    addition = ""
                    if DOMAIN_YT in host or DOMAIN_YT_BE in host:
                        source = "YouTube"
                        file_root, file_ext = os.path.splitext(file_name)
                        file_title = file_root.replace(file_ext, "")
                        addition = ": " + file_title
                    elif DOMAIN_SC in host or DOMAIN_SC_GOOGL in host:
                        source = "SoundCloud"
                    elif DOMAIN_BC in host:
                        source = "Bandcamp"
                    else:
                        source = url_obj.host.replace(".com", "").replace(".ru", "").replace("www.", "").replace("m.", "")
                    # TODO fix youtube id in [] ?
                    caption = "@{} _got it from_ [{}]({}){}".format(bot.username.replace("_", r"\_"), source, url, addition.replace("_", r"\_"))
                    if add_description:
                        caption += add_description
                    # logger.debug(caption)
                    reply_to_message_id_send = reply_to_message_id
                sent_audio_ids = []
                for index, file_part in enumerate(file_parts):
                    path = pathlib.Path(file_part)
                    file_name = os.path.split(file_part)[-1]
                    # file_name = translit(file_name, 'ru', reversed=True)
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
                        if caption_part:
                            caption_full = caption_part
                        else:
                            caption_full = ""
                    # caption_full = textwrap.shorten(caption_full, width=190, placeholder="..")
                    retries = 3
                    for i in range(retries):
                        try:
                            logger.debug(f"Trying {i+1} time to send file part: {file_part}")
                            if file_part.endswith(".mp3"):
                                mp3 = MP3(file_part)
                                duration = round(mp3.info.length)
                                performer = None
                                title = None
                                try:
                                    performer = ", ".join(mp3["artist"])
                                    title = ", ".join(mp3["title"])
                                except:
                                    pass
                                if TG_BOT_API_LOCAL_MODE:
                                    audio = path.absolute().as_uri()
                                    logger.debug(audio)
                                else:
                                    audio = open(file_part, "rb")
                                # Bot.send_audio() has connection troubles when running async in parallel:
                                # Works bad on my computer with official API (good with high timeout)
                                # Works good on server with local API.
                                audio_msg = run_async(
                                    bot.send_audio(
                                        chat_id=chat_id,
                                        reply_to_message_id=reply_to_message_id_send,
                                        audio=audio,
                                        duration=duration,
                                        performer=performer,
                                        title=title,
                                        caption=caption_full,
                                        parse_mode="Markdown",
                                        read_timeout=COMMON_CONNECTION_TIMEOUT,
                                        write_timeout=COMMON_CONNECTION_TIMEOUT,
                                        connect_timeout=COMMON_CONNECTION_TIMEOUT,
                                        pool_timeout=COMMON_CONNECTION_TIMEOUT,
                                    ),
                                )
                                sent_audio_ids.append(audio_msg.audio.file_id)
                                logger.debug("Sending audio succeeded: %s", file_name)
                                break
                            elif download_video:
                                video = open(file_part, "rb")
                                probe_result = probe_media(file_part)
                                try:
                                    duration = int(float(probe_result["format"]["duration"]))
                                    videostream = next(item for item in probe_result.get("streams", []) if item.get("codec_type") == "video")
                                    width = int(videostream["width"])
                                    height = int(videostream["height"])
                                except (KeyError, StopIteration, TypeError, ValueError) as exc:
                                    raise FFprobeError(f"ffprobe returned incomplete data for {file_part}") from exc
                                video_msg = run_async(
                                    bot.send_video(
                                        chat_id=chat_id,
                                        reply_to_message_id=reply_to_message_id_send,
                                        video=video,
                                        supports_streaming=True,
                                        duration=duration,
                                        width=width,
                                        height=height,
                                        caption=caption_full,
                                        parse_mode="Markdown",
                                        read_timeout=COMMON_CONNECTION_TIMEOUT,
                                        write_timeout=COMMON_CONNECTION_TIMEOUT,
                                        connect_timeout=COMMON_CONNECTION_TIMEOUT,
                                        pool_timeout=COMMON_CONNECTION_TIMEOUT,
                                    ),
                                )
                                sent_audio_ids.append(video_msg.video.file_id)
                                logger.debug("Sending video succeeded: %s", file_name)
                                break
                        except FFprobeError as exc:
                            logger.debug("Sending failed because of ffprobe error: %s (%s)", file_name, exc)
                            break
                        except TelegramError:
                            ### ??? print(traceback.format_exc())
                            if i == retries - 1:
                                logger.debug("Sending failed because of TelegramError: %s", file_name)
                            else:
                                time.sleep(5)
                if len(sent_audio_ids) != len(file_parts):
                    run_async(
                        bot.send_message(
                            chat_id=chat_id,
                            reply_to_message_id=reply_to_message_id,
                            text="*Sorry*, could not send file `{}` or some of it's parts..".format(file_name),
                            parse_mode="Markdown",
                        )
                    )
                    logger.debug("Sending some parts failed: %s", file_name)

    shutil.rmtree(download_dir, ignore_errors=True)
    if wait_message_id:
        try:
            run_async(
                bot.delete_message(
                    chat_id=chat_id,
                    message_id=wait_message_id,
                ),
            )
        except:
            pass
    run_async(bot.shutdown())


async def post_shutdown(application: Application) -> None:
    # EXECUTOR.shutdown(wait=False, cancel_futures=True)
    EXECUTOR.stop()
    EXECUTOR.join(timeout=10)


async def post_init(application: Application) -> None:
    SYSTEMD_NOTIFIER.notify("READY=1")
    SYSTEMD_NOTIFIER.notify(f"STATUS=Application initialized")


async def callback_watchdog(context: ContextTypes.DEFAULT_TYPE):
    SYSTEMD_NOTIFIER.notify("WATCHDOG=1")
    SYSTEMD_NOTIFIER.notify(f"STATUS=Watchdog was sent {datetime.datetime.now()}")


async def callback_monitor(context: ContextTypes.DEFAULT_TYPE):
    pending_items = getattr(EXECUTOR, "_pending_work_items", None)
    pending_count = len(pending_items) if pending_items is not None else 0
    logger.debug(f"EXECUTOR pending work items: {pending_count} tasks remain")
    EXECUTOR_TASKS_REMAINING.set(pending_count)


def main():
    # Start exposing Prometheus/OpenMetrics metrics:
    prometheus_client.start_http_server(addr=METRICS_HOST, port=METRICS_PORT, registry=REGISTRY)

    # Maybe we can use token again if we will buy SoundCloud Go+
    # https://github.com/flyingrub/scdl/issues/429
    # if sc_auth_token:
    #     config = configparser.ConfigParser()
    #     config['scdl'] = {}
    #     config['scdl']['path'] = DL_DIR
    #     config['scdl']['auth_token'] = sc_auth_token
    #     config_dir = os.path.join(os.path.expanduser('~'), '.config', 'scdl')
    #     config_path = os.path.join(config_dir, 'scdl.cfg')
    #     os.makedirs(config_dir, exist_ok=True)
    #     with open(config_path, 'w') as config_file:
    #         config.write(config_file)

    try:
        with open(CHAT_STORAGE, "rb") as file:
            data = pickle.load(file)
        logger.info(f"Pickle file '{CHAT_STORAGE}' loaded successfully. Can continue loading persistence.")
    except FileNotFoundError:
        logger.info(f"The file '{CHAT_STORAGE}' does not exist, it will be created from scratch.")
    except TypeError as e:
        logger.info(f"TypeError occurred: {e}. Deleting the file...")
        os.remove(CHAT_STORAGE)
        logger.info(f"File '{CHAT_STORAGE}' has been deleted, it will be created from scratch.")
    except Exception as e:
        logger.info(f"An unexpected error occurred: {e}. Deleting the file...")
        os.remove(CHAT_STORAGE)
        logger.info(f"File '{CHAT_STORAGE}' has been deleted, it will be created from scratch.")

    persistence = PicklePersistence(filepath=CHAT_STORAGE)

    # https://docs.python-telegram-bot.org/en/v20.1/telegram.ext.applicationbuilder.html#telegram.ext.ApplicationBuilder
    # We use concurrent_updates with limit instead of unlimited create_task.
    # https://github.com/python-telegram-bot/python-telegram-bot/wiki/Concurrency#applicationconcurrent_updates
    # https://github.com/python-telegram-bot/python-telegram-bot/issues/3509
    application = (
        ApplicationBuilder()
        .token(TG_BOT_TOKEN)
        .local_mode(TG_BOT_API_LOCAL_MODE)
        # https://github.com/python-telegram-bot/python-telegram-bot/issues/3556
        .http_version(HTTP_VERSION)
        .get_updates_http_version(HTTP_VERSION)
        .base_url(f"{TG_BOT_API}/bot")
        .base_file_url(f"{TG_BOT_API}/file/bot")
        .persistence(persistence)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .rate_limiter(AIORateLimiter(max_retries=3))
        .concurrent_updates(WORKERS * 2)
        .connection_pool_size(WORKERS * 4)
        .pool_timeout(COMMON_CONNECTION_TIMEOUT)
        .connect_timeout(COMMON_CONNECTION_TIMEOUT)
        .read_timeout(COMMON_CONNECTION_TIMEOUT)
        .write_timeout(COMMON_CONNECTION_TIMEOUT)
        .build()
    )

    bot_username = requests.get(f"{TG_BOT_API}/bot{TG_BOT_TOKEN}/getMe").json()["result"]["username"]
    blacklist_whitelist_handler = MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, blacklist_whitelist_callback)
    start_command_handler = CommandHandler("start", start_help_commands_callback)
    help_command_handler = CommandHandler("help", start_help_commands_callback)
    settings_command_handler = CommandHandler("settings", settings_command_callback)
    dl_command_handler = CommandHandler("dl", dl_link_commands_and_messages_callback, filters=~filters.UpdateType.EDITED_MESSAGE & ~filters.FORWARDED)
    link_command_handler = CommandHandler("link", dl_link_commands_and_messages_callback, filters=~filters.UpdateType.EDITED_MESSAGE & ~filters.FORWARDED)
    message_with_links_handler = MessageHandler(
        ~filters.UpdateType.EDITED_MESSAGE
        & ~filters.ForwardedFrom(username=bot_username)
        & ~filters.COMMAND
        & (
            (filters.TEXT & (filters.Entity(MessageEntity.URL) | filters.Entity(MessageEntity.TEXT_LINK)))
            | (filters.CAPTION & (filters.CaptionEntity(MessageEntity.URL) | filters.CaptionEntity(MessageEntity.TEXT_LINK)))
        ),
        dl_link_commands_and_messages_callback,
    )
    button_query_handler = CallbackQueryHandler(button_press_callback)
    unknown_handler = MessageHandler(filters.COMMAND, unknown_command_callback)

    application.add_handler(blacklist_whitelist_handler)
    application.add_handler(start_command_handler)
    application.add_handler(help_command_handler)
    application.add_handler(settings_command_handler)
    application.add_handler(dl_command_handler)
    application.add_handler(link_command_handler)
    application.add_handler(message_with_links_handler)
    application.add_handler(button_query_handler)
    application.add_handler(unknown_handler)
    application.add_error_handler(error_callback)

    job_queue = application.job_queue
    if job_queue is None:
        raise RuntimeError("Job queue is not initialized")
    job_watchdog = job_queue.run_repeating(callback_watchdog, interval=60, first=10)
    # job_monitor = job_queue.run_repeating(callback_monitor, interval=5, first=5)

    if WEBHOOK_ENABLE:
        application.run_webhook(
            drop_pending_updates=True,
            listen=WEBHOOK_HOST,
            port=WEBHOOK_PORT,
            url_path=WEBHOOK_APP_URL_PATH,
            webhook_url=urljoin(WEBHOOK_APP_URL_ROOT, WEBHOOK_APP_URL_PATH),
            secret_token=WEBHOOK_SECRET_TOKEN,
            max_connections=WORKERS * 4,
            cert=WEBHOOK_CERT_FILE,
            key=WEBHOOK_KEY_FILE,
        )
    else:
        # TODO await it somehow or change to something like this:
        # https://docs.python-telegram-bot.org/en/stable/telegram.bot.html
        # https://docs.python-telegram-bot.org/en/stable/telegram.ext.application.html#telegram.ext.Application.run_polling
        # https://github.com/python-telegram-bot/python-telegram-bot/discussions/3310
        # https://github.com/python-telegram-bot/python-telegram-bot/wiki/Frequently-requested-design-patterns#running-ptb-alongside-other-asyncio-frameworks
        # https://docs.python-telegram-bot.org/en/v21.5/examples.customwebhookbot.html
        application.bot.delete_webhook()
        application.run_polling(
            drop_pending_updates=True,
        )


if __name__ == "__main__":
    main()
