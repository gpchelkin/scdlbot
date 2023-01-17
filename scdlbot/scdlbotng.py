import gc
import logging
import os
import pathlib
import random
import shutil
from logging.handlers import SysLogHandler
from multiprocessing import Process, Queue
from queue import Empty
from subprocess import PIPE, TimeoutExpired  # skipcq: BAN-B404
from urllib.parse import urljoin, urlparse
from uuid import uuid4

import ffmpeg
import pkg_resources
import prometheus_client
import requests
from boltons.urlutils import find_all_links
from mutagen.id3 import ID3
from mutagen.mp3 import EasyMP3 as MP3
from prometheus_client import Summary
from telegram import Chat, ChatMemberAdministrator, ChatMemberOwner, InlineKeyboardButton, InlineKeyboardMarkup, Message, MessageEntity, Update
from telegram.constants import ChatAction
from telegram.error import BadRequest, ChatMigrated, Forbidden, NetworkError, TelegramError, TimedOut
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, PicklePersistence, filters
from telegram_handler import TelegramHandler

try:
    import yt_dlp as youtube_dl

    youtube_dl_bin_name = "yt-dlp"
except:
    try:
        import youtube_dl

        youtube_dl_bin_name = "youtube-dl"
    except:
        import youtube_dlc as youtube_dl

        youtube_dl_bin_name = "youtube-dlc"

from boltons.urlutils import URL
from plumbum import ProcessExecutionError, ProcessTimedOut, local

logging_handlers = []
syslog_debug = bool(int(os.getenv("SYSLOG_DEBUG", "0")))
logging_level = logging.DEBUG if syslog_debug else logging.INFO
tg_bot_token = os.environ["TG_BOT_TOKEN"]
alert_chat_ids = list(map(int, os.getenv("ALERT_CHAT_IDS", "0").split(",")))

console_formatter = logging.Formatter("[%(name)s] %(levelname)s: %(message)s")
console_handler = logging.StreamHandler()
console_handler.setFormatter(console_formatter)
console_handler.setLevel(logging.DEBUG)
logging_handlers.append(console_handler)

telegram_handler = TelegramHandler(token=tg_bot_token, chat_id=str(alert_chat_ids[0]))
telegram_handler.setLevel(logging.WARNING)
logging_handlers.append(telegram_handler)

syslog_address = os.getenv("SYSLOG_ADDRESS", "")
if syslog_address:
    syslog_hostname = os.getenv("HOSTNAME", "test-host")
    syslog_formatter = logging.Formatter("%(asctime)s " + syslog_hostname + " %(name)s: %(message)s", datefmt="%b %d %H:%M:%S")
    syslog_host, syslog_udp_port = syslog_address.split(":")
    syslog_handler = SysLogHandler(address=(syslog_host, int(syslog_udp_port)))
    syslog_handler.setFormatter(syslog_formatter)
    syslog_handler.setLevel(logging_level)
    logging_handlers.append(syslog_handler)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.DEBUG,
    handlers=logging_handlers,
)

logger = logging.getLogger(__name__)

REQUEST_TIME = Summary("request_processing_seconds", "Time spent processing request")

bin_path = os.getenv("BIN_PATH", "")
scdl_bin = local[os.path.join(bin_path, "scdl")]
bandcamp_dl_bin = local[os.path.join(bin_path, "bandcamp-dl")]
youtube_dl_bin = local[os.path.join(bin_path, youtube_dl_bin_name)]

store_chat_id = int(os.getenv("STORE_CHAT_ID", "0"))
no_flood_chat_ids = list(map(int, os.getenv("NO_FLOOD_CHAT_IDS", "0").split(",")))
dl_timeout = int(os.getenv("DL_TIMEOUT", "300"))
dl_dir = os.path.expanduser(os.getenv("DL_DIR", "/tmp/scdlbot"))
chat_storage_file = os.path.expanduser(os.getenv("CHAT_STORAGE", "/tmp/scdlbotdata"))
serve_audio = bool(int(os.getenv("SERVE_AUDIO", "0")))
app_url = os.getenv("APP_URL", "")
tg_bot_api = os.getenv("TG_BOT_API", "https://api.telegram.org")
max_tg_file_size = int(os.getenv("MAX_TG_FILE_SIZE", "45_000_000"))
max_convert_file_size = int(os.getenv("MAX_CONVERT_FILE_SIZE", "80_000_000"))
proxies = os.getenv("PROXIES", None)
if proxies:
    proxies = proxies.split(",")
source_ips = os.getenv("SOURCE_IPS", None)
if source_ips:
    source_ips = source_ips.split(",")
cookies_file = os.getenv("COOKIES_FILE", "")
workers = int(os.getenv("WORKERS", 4))
use_webhook = bool(int(os.getenv("USE_WEBHOOK", "0")))
webhook_host = os.getenv("HOST", "127.0.0.1")
webhook_port = int(os.getenv("PORT", "5000"))
cert_file = os.getenv("CERT_FILE", "")
cert_key_file = os.getenv("CERT_KEY_FILE", "")
url_path = os.getenv("URL_PATH", tg_bot_token.replace(":", ""))

SITES = {
    "sc": "soundcloud",
    "scapi": "api.soundcloud",
    "bc": "bandcamp",
    "yt": "youtu",
}
if serve_audio:
    max_tg_file_size = 19_000_000


def get_response_text(file_name):
    # https://stackoverflow.com/a/20885799/2490759
    path = "/".join(("texts", file_name))
    return pkg_resources.resource_string(__name__, path).decode("UTF-8")


HELP_TEXT = get_response_text("help.tg.md")
SETTINGS_TEXT = get_response_text("settings.tg.md")
DL_TIMEOUT_TEXT = get_response_text("dl_timeout.txt").format(dl_timeout // 60)
WAIT_BIT_TEXT = [get_response_text("wait_bit.txt"), get_response_text("wait_beat.txt"), get_response_text("wait_beet.txt")]
NO_AUDIO_TEXT = get_response_text("no_audio.txt")
NO_URLS_TEXT = get_response_text("no_urls.txt")
OLD_MSG_TEXT = get_response_text("old_msg.txt")
REGION_RESTRICTION_TEXT = get_response_text("region_restriction.txt")
DIRECT_RESTRICTION_TEXT = get_response_text("direct_restriction.txt")
LIVE_RESTRICTION_TEXT = get_response_text("live_restriction.txt")
ALERT_CHAT_IDS = set(alert_chat_ids) if alert_chat_ids else set()
# https://yandex.com/support/music-app-ios/search-and-listen/listening-abroad.html
COOKIES_DOWNLOAD_FILE = "/tmp/scdlbot_cookies.txt"
bot_username = "scdlbot"
RANT_TEXT_PRIVATE = "Read /help to learn how to use me"
RANT_TEXT_PUBLIC = f"[Start me in PM to read help and learn how to use me](t.me/{bot_username}?start=1)"
# Example export BLACKLIST_DOMS = "invidious.tube invidious.kavin.rocks invidious.himiko.cloud invidious.namazso.eu dev.viewtube.io tube.cadence.moe piped.kavin.rocks"
whitelist_domains = set(x for x in os.environ.get("WHITELIST_DOMS", "").split())
blacklist_domains = set(x for x in os.environ.get("BLACKLIST_DOMS", "").split())
try:
    whitelist_chats = set(int(x) for x in os.environ.get("WHITELIST_CHATS", "").split())
except ValueError:
    raise ValueError("Your whitelisted chats does not contain valid integers.")
try:
    blacklist_chats = set(int(x) for x in os.environ.get("BLACKLIST_CHATS", "").split())
except ValueError:
    raise ValueError("Your blacklisted chats does not contain valid integers.")


class Error(Exception):
    """Base class for exceptions in this module."""


class FileNotSupportedError(Error):
    def __init__(self, file_format):
        self.file_format = file_format


class FileTooLargeError(Error):
    def __init__(self, file_size):
        self.file_size = file_size


class FileSplittedPartiallyError(Error):
    def __init__(self, file_parts):
        self.file_parts = file_parts


class FileNotConvertedError(Error):
    def __init__(self):
        pass


class FileSentPartiallyError(Error):
    def __init__(self, sent_audio_ids):
        self.sent_audio_ids = sent_audio_ids


class URLError(Error):
    def __init__(self):
        self.status = ""


class URLDirectError(URLError):
    def __init__(self):
        self.status = "direct"


class URLCountryError(URLError):
    def __init__(self):
        self.status = "country"


class URLLiveError(URLError):
    def __init__(self):
        self.status = "live"


class URLTimeoutError(URLError):
    def __init__(self):
        self.status = "timeout"


def log_and_track(event_name, message=None):
    logger.info(f"Event: {event_name}")


def get_link_text(urls):
    link_text = ""
    for i, url in enumerate(urls):
        link_text += "[Source Link #{}]({}) | `{}`\n".format(str(i + 1), url, URL(url).host)
        direct_urls = urls[url].splitlines()
        for direct_url in direct_urls:
            if "http" in direct_url:
                content_type = ""
                if "googlevideo" in direct_url:
                    if "audio" in direct_url:
                        content_type = "Audio"
                    else:
                        content_type = "Video"
                # direct_url = shorten_url(direct_url)
                link_text += "• {} [Direct Link]({})\n".format(content_type, direct_url)
    link_text += "\n*Note:* Final download URLs are only guaranteed to work on the same machine/IP where extracted"
    return link_text


def get_wait_text():
    return random.choice(WAIT_BIT_TEXT)


def get_italic(text):
    return "_{}_".format(text)


def get_settings_inline_keyboard(chat_data):
    mode = chat_data["settings"]["mode"]
    flood = chat_data["settings"]["flood"]
    emoji_yes = "✅"
    emoji_no = "❌"
    button_dl = InlineKeyboardButton(text=" ".join([emoji_yes if mode == "dl" else emoji_no, "Download"]), callback_data=" ".join(["settings", "dl"]))
    button_link = InlineKeyboardButton(text=" ".join([emoji_yes if mode == "link" else emoji_no, "Links"]), callback_data=" ".join(["settings", "link"]))
    button_ask = InlineKeyboardButton(text=" ".join([emoji_yes if mode == "ask" else emoji_no, "Ask"]), callback_data=" ".join(["settings", "ask"]))
    button_flood = InlineKeyboardButton(text=" ".join([emoji_yes if flood == "yes" else emoji_no, "Captions"]), callback_data=" ".join(["settings", "flood"]))
    button_close = InlineKeyboardButton(text=" ".join([emoji_no, "Close settings"]), callback_data=" ".join(["settings", "close"]))
    inline_keyboard = InlineKeyboardMarkup([[button_dl, button_link, button_ask], [button_flood, button_close]])
    return inline_keyboard


def main():
    # expose prometheus/openmetrics metrics:
    metrics_host = os.getenv("METRICS_HOST", "127.0.0.1")
    metrics_port = int(os.getenv("METRICS_PORT", "8000"))
    prometheus_client.start_http_server(metrics_port, addr=metrics_host)

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

    persistence = PicklePersistence(filepath=chat_storage_file)
    application = (
        ApplicationBuilder()
        .token(tg_bot_token)
        .concurrent_updates(True)
        .base_url(f"{tg_bot_api}/bot")
        .base_file_url(f"{tg_bot_api}/file/bot")
        .persistence(persistence=persistence)
        .build()
    )

    start_command_handler = CommandHandler("start", help_command_callback)
    help_command_handler = CommandHandler("help", help_command_callback)
    settings_command_handler = CommandHandler("settings", settings_command_callback)
    dl_command_handler = CommandHandler("dl", common_command_callback, filters=~filters.UpdateType.EDITED_MESSAGE & ~filters.FORWARDED)
    link_command_handler = CommandHandler("link", common_command_callback, filters=~filters.UpdateType.EDITED_MESSAGE & ~filters.FORWARDED)
    message_with_links_handler = MessageHandler(
        ~filters.UpdateType.EDITED_MESSAGE
        & ~filters.COMMAND
        & (
            (filters.TEXT & (filters.Entity(MessageEntity.URL) | filters.Entity(MessageEntity.TEXT_LINK)))
            | (filters.CAPTION & (filters.CaptionEntity(MessageEntity.URL) | filters.CaptionEntity(MessageEntity.TEXT_LINK)))
        ),
        common_command_callback,
    )

    button_query_handler = CallbackQueryHandler(button_query_callback)
    blacklist_whitelist_handler = MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, blacklist_whitelist)
    unknown_handler = MessageHandler(filters.COMMAND, unknown_command_callback)

    application.add_handler(start_command_handler)
    application.add_handler(help_command_handler)
    application.add_handler(settings_command_handler)
    application.add_handler(dl_command_handler)
    application.add_handler(link_command_handler)
    application.add_handler(message_with_links_handler)
    application.add_handler(button_query_handler)
    application.add_handler(blacklist_whitelist_handler)
    application.add_handler(unknown_handler)
    application.add_error_handler(error_callback)
    application.run_polling()


async def help_command_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.channel_post:
        message = update.channel_post
    elif update.message:
        message = update.message
    event_name = "help"
    entities = message.parse_entities(types=[MessageEntity.BOT_COMMAND])
    for entity_value in entities.values():
        event_name = entity_value.replace("/", "").replace("@{}".format(bot_username), "")
        break
    log_and_track(event_name, message)
    chat_id = update.effective_chat.id
    chat_type = update.effective_chat.type
    await context.bot.send_message(chat_id=chat_id, text=HELP_TEXT, parse_mode="Markdown", disable_web_page_preview=True)


async def settings_command_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_and_track("settings")
    chat_id = update.effective_chat.id
    chat_type = update.effective_chat.type
    init_chat_data(
        chat_data=context.chat_data,
        mode=("dl" if chat_type == Chat.PRIVATE else "ask"),
        flood=("no" if chat_id in no_flood_chat_ids else "yes"),
    )
    await context.bot.send_message(chat_id=chat_id, parse_mode="Markdown", reply_markup=get_settings_inline_keyboard(context.chat_data), text=SETTINGS_TEXT)


async def button_query_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    btn_msg = update.callback_query.message
    btn_msg_id = btn_msg.message_id
    user_id = update.callback_query.from_user.id
    chat = update.effective_chat
    chat_id = update.effective_chat.id
    chat_type = update.effective_chat.type
    orig_msg_id, action = update.callback_query.data.split()
    if not is_chat_allowed(chat_id):
        await update.callback_query.answer(text="This command isn't allowed in this chat.")
        return
    if orig_msg_id == "settings":
        if chat_type != Chat.PRIVATE:
            chat_member_status = chat.get_member(user_id).status
            if chat_member_status not in [ChatMemberAdministrator, ChatMemberOwner] and user_id not in ALERT_CHAT_IDS:
                log_and_track("settings_fail")
                await update.callback_query.answer(text="You're not chat admin")
                return
        log_and_track(f"settings_{action}", btn_msg)
        if action == "close":
            await context.bot.delete_message(chat_id, btn_msg_id)
        else:
            setting_changed = False
            if action in ["dl", "link", "ask"]:
                current_setting = context.chat_data["settings"]["mode"]
                if action != current_setting:
                    setting_changed = True
                    context.chat_data["settings"]["mode"] = action
            elif action in ["flood"]:
                current_setting = context.chat_data["settings"]["flood"]
                setting_changed = True
                context.chat_data["settings"][action] = "no" if current_setting == "yes" else "yes"
            if setting_changed:
                await update.callback_query.answer(text="Settings changed")
                await update.callback_query.edit_message_reply_markup(reply_markup=get_settings_inline_keyboard(context.chat_data))
            else:
                await update.callback_query.answer(text="Settings not changed")

    elif orig_msg_id in context.chat_data:
        msg_from_storage = context.chat_data.pop(orig_msg_id)
        orig_msg = msg_from_storage["message"]
        urls = msg_from_storage["urls"]
        source_ip = msg_from_storage["source_ip"]
        proxy = msg_from_storage["proxy"]
        log_and_track(f"{action}_msg", orig_msg)
        if action == "dl":
            await update.callback_query.answer(text=get_wait_text())
            flood = context.chat_data["settings"]["flood"]
            wait_message = await update.callback_query.edit_message_text(parse_mode="Markdown", text=get_italic(get_wait_text()))
            for url in urls:
                await download_url_and_send(
                    context.bot,
                    url,
                    urls[url],
                    chat_id=chat_id,
                    reply_to_message_id=orig_msg_id,
                    wait_message_id=wait_message.message_id,
                    source_ip=source_ip,
                    proxy=proxy,
                    flood=flood,
                )
        elif action == "link":
            await context.bot.send_message(chat_id=chat_id, reply_to_message_id=orig_msg_id, parse_mode="Markdown", disable_web_page_preview=True, text=get_link_text(urls))
            await context.bot.delete_message(chat_id=chat_id, message_id=btn_msg_id)
        elif action == "nodl":
            await context.bot.delete_message(chat_id=chat_id, message_id=btn_msg_id)
    else:
        await update.callback_query.answer(text=OLD_MSG_TEXT)
        await context.bot.delete_message(chat_id=chat_id, message_id=btn_msg_id)


async def unknown_command_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return
    # await context.bot.send_message(
    #     chat_id=update.effective_chat.id,
    #     text="Sorry, I didn't understand that command.",
    # )


async def error_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):  # skipcq: PYL-R0201
    try:
        raise context.error
    except Forbidden:
        # remove update.message.chat_id from conversation list
        logger.debug(f"Update {update} caused Forbidden error: {context.error}")
    except BadRequest:
        # handle malformed requests - read more below!
        logger.debug(f"Update {update} caused BadRequest error: {context.error}")
    except TimedOut:
        # handle slow connection problems
        logger.debug(f"Update {update} caused TimedOut error: {context.error}")
    except NetworkError:
        # handle other connection problems
        logger.debug(f"Update {update} caused NetworkError error: {context.error}")
    except ChatMigrated as e:
        # the chat_id of a group has changed, use e.new_chat_id instead
        logger.debug(f"Update {update} caused ChatMigrated error: {context.error}")
    except TelegramError:
        # handle all other telegram related errors
        logger.debug(f"Update {update} caused TelegramError error: {context.error}")


def init_chat_data(chat_data, mode="dl", flood="yes"):
    if "settings" not in chat_data:
        chat_data["settings"] = {}
    if "mode" not in chat_data["settings"]:
        chat_data["settings"]["mode"] = mode
    if "flood" not in chat_data["settings"]:
        chat_data["settings"]["flood"] = flood


async def download_url_and_send(bot, url, direct_urls, chat_id, reply_to_message_id=None, wait_message_id=None, source_ip=None, proxy=None, flood="yes"):
    await bot.send_chat_action(chat_id=chat_id, action=ChatAction.RECORD_VOICE)
    download_dir = os.path.join(dl_dir, str(uuid4()))
    shutil.rmtree(download_dir, ignore_errors=True)
    os.makedirs(download_dir)

    status = 0
    if direct_urls == "direct":
        status = -3
    elif direct_urls == "country":
        status = -4
    elif direct_urls == "live":
        status = -5
    elif direct_urls == "timeout":
        status = -6
    else:
        if (SITES["sc"] in url and SITES["scapi"] not in url) or (SITES["bc"] in url):
            cmd_name = "scdl"
            cmd_args = []
            cmd = None
            cmd_input = None
            if SITES["sc"] in url and SITES["scapi"] not in url:
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
            elif SITES["bc"] in url:
                cmd = bandcamp_dl_bin
                cmd_name = str(cmd)
                cmd_args = (
                    "--base-dir",
                    download_dir,  # Base location of which all files are downloaded
                    "--template",
                    "%{track} - %{artist} - %{title} [%{album}]",  # Output filename template
                    "--overwrite",  # Overwrite tracks that already exist
                    "--group",  # Use album/track Label as iTunes grouping
                    # "--embed-art",  # Embed album art (if available)
                    "--no-slugify",  # Disable slugification of track, album, and artist names
                    url,  # URL of album/track
                )
                cmd_input = "yes"

            logger.info("%s starts: %s", cmd_name, url)
            env = None
            if proxy:
                env = {"http_proxy": proxy, "https_proxy": proxy}
            cmd_proc = cmd[cmd_args].popen(env=env, stdin=PIPE, stdout=PIPE, stderr=PIPE, universal_newlines=True)
            try:
                cmd_stdout, cmd_stderr = cmd_proc.communicate(input=cmd_input, timeout=dl_timeout)
                cmd_retcode = cmd_proc.returncode
                # TODO listed are common scdl problems for one track with 0 retcode, all its output is always in stderr:
                if cmd_retcode or (any(err in cmd_stderr for err in ["Error resolving url", "is not streamable", "Failed to get item"]) and ".mp3" not in cmd_stderr):
                    raise ProcessExecutionError(cmd_args, cmd_retcode, cmd_stdout, cmd_stderr)
                logger.info("%s succeeded: %s", cmd_name, url)
                status = 1
            except TimeoutExpired:
                cmd_proc.kill()
                logger.info("%s took too much time and dropped: %s", cmd_name, url)
                status = -1
            except ProcessExecutionError:
                logger.exception("%s failed: %s", cmd_name, url)

    if status == 0:
        cmd = youtube_dl_func
        cmd_name = "youtube_dl_func"
        # TODO: set different ydl_opts for different sites
        host = urlparse(url).hostname
        ydl_opts = {}
        if host == "tiktok.com" or host.endswith(".tiktok.com"):
            ydl_opts = {
                "outtmpl": os.path.join(download_dir, "tiktok.%(ext)s"),
                "videoformat": "mp4",
            }
        elif "instagr" in host:
            ydl_opts = {
                "outtmpl": os.path.join(download_dir, "inst.%(ext)s"),
                "videoformat": "mp4",
                "postprocessors": [
                    {
                        "key": "FFmpegVideoConvertor",
                        "preferedformat": "mp4",
                    }
                ],
            }
        else:
            ydl_opts = {
                "outtmpl": os.path.join(download_dir, "%(title)s.%(ext)s"),
                # default: %(autonumber)s - %(title)s-%(id)s.%(ext)s
                "format": "bestaudio/best",
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "320",
                    },
                    {
                        "key": "FFmpegMetadata",
                    },
                    # {'key': 'EmbedThumbnail'},
                ],
                "noplaylist": True,
            }
        if proxy:
            ydl_opts["proxy"] = proxy
        if source_ip:
            ydl_opts["source_address"] = source_ip
        # https://github.com/ytdl-org/youtube-dl/blob/master/youtube_dl/YoutubeDL.py#L210
        if cookies_file:
            if "http" in cookies_file:
                ydl_opts["cookiefile"] = COOKIES_DOWNLOAD_FILE
            else:
                ydl_opts["cookiefile"] = cookies_file
        queue = Queue()
        cmd_args = (
            url,
            ydl_opts,
            queue,
        )
        logger.info("%s starts: %s", cmd_name, url)
        cmd_proc = Process(target=cmd, args=cmd_args)
        cmd_proc.start()
        try:
            cmd_retcode, cmd_stderr = queue.get(block=True, timeout=dl_timeout)
            cmd_stdout = ""
            cmd_proc.join()
            if cmd_retcode:
                raise ProcessExecutionError(cmd_args, cmd_retcode, cmd_stdout, cmd_stderr)
                # raise cmd_status  # TODO: pass and re-raise original Exception?
            logger.info("%s succeeded: %s", cmd_name, url)
            status = 1
        except Empty:
            cmd_proc.join(1)
            if cmd_proc.is_alive():
                cmd_proc.terminate()
            logger.info("%s took too much time and dropped: %s", cmd_name, url)
            status = -1
        except ProcessExecutionError:
            logger.exception("%s failed: %s", cmd_name, url)
            status = -2
        gc.collect()

    if status in [-1, -6]:
        await bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id, text=DL_TIMEOUT_TEXT, parse_mode="Markdown")
    elif status == -2:
        await bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id, text=NO_AUDIO_TEXT, parse_mode="Markdown")
    elif status == -3:
        await bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id, text=DIRECT_RESTRICTION_TEXT, parse_mode="Markdown")
    elif status == -4:
        await bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id, text=REGION_RESTRICTION_TEXT, parse_mode="Markdown")
    elif status == -5:
        await bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id, text=LIVE_RESTRICTION_TEXT, parse_mode="Markdown")
    elif status == 1:
        file_list = []
        for d, dirs, files in os.walk(download_dir):
            for file in files:
                file_list.append(os.path.join(d, file))
        if not file_list:
            logger.info("No files in dir: %s", download_dir)
            await bot.send_message(
                chat_id=chat_id, reply_to_message_id=reply_to_message_id, text="*Sorry*, I couldn't download any files from provided links", parse_mode="Markdown"
            )
        else:
            for file in sorted(file_list):
                file_name = os.path.split(file)[-1]
                file_parts = []
                try:
                    file_root, file_ext = os.path.splitext(file)
                    file_format = file_ext.replace(".", "").lower()
                    file_size = os.path.getsize(file)
                    if file_format not in ["mp3", "m4a", "mp4"]:
                        raise FileNotSupportedError(file_format)
                    if file_size > max_convert_file_size:
                        raise FileTooLargeError(file_size)
                    # FIXME tiktok.mp4 is for tiktok, inst.mp4 for instagram
                    if file_format not in ["mp3"] and not ("tiktok." in file or "inst." in file):
                        logger.info("Converting: %s", file)
                        try:
                            file_converted = file.replace(file_ext, ".mp3")
                            ffinput = ffmpeg.input(file)
                            # audio_bitrate="320k"
                            ffmpeg.output(ffinput, file_converted, vn=None).run()
                            file = file_converted
                            file_root, file_ext = os.path.splitext(file)
                            file_format = file_ext.replace(".", "").lower()
                            file_size = os.path.getsize(file)
                        except Exception:
                            # TODO exceptions
                            raise FileNotConvertedError

                    file_parts = []
                    if file_size <= max_tg_file_size:
                        file_parts.append(file)
                    else:
                        logger.info("Splitting: %s", file)
                        id3 = None
                        try:
                            id3 = ID3(file, translate=False)
                        except:
                            pass

                        parts_number = file_size // max_tg_file_size + 1

                        # https://github.com/c0decracker/video-splitter
                        # https://superuser.com/a/1354956/464797
                        try:
                            # file_duration = float(ffmpeg.probe(file)['format']['duration'])
                            part_size = file_size // parts_number
                            cur_position = 0
                            for i in range(parts_number):
                                file_part = file.replace(file_ext, ".part{}{}".format(str(i + 1), file_ext))
                                ffinput = ffmpeg.input(file)
                                if i == (parts_number - 1):
                                    ffmpeg.output(ffinput, file_part, codec="copy", vn=None, ss=cur_position).run()
                                else:
                                    ffmpeg.output(ffinput, file_part, codec="copy", vn=None, ss=cur_position, fs=part_size).run()
                                    part_duration = float(ffmpeg.probe(file_part)["format"]["duration"])
                                    cur_position += part_duration
                                if id3:
                                    try:
                                        id3.save(file_part, v1=2, v2_version=4)
                                    except:
                                        pass
                                file_parts.append(file_part)
                        except Exception:
                            # TODO exceptions
                            raise FileSplittedPartiallyError(file_parts)

                except FileNotSupportedError as exc:
                    if not (exc.file_format in ["m3u", "jpg", "jpeg", "png", "finished", "tmp"]):
                        logger.warning("Unsupported file format: %s", file_name)
                        await bot.send_message(
                            chat_id=chat_id,
                            reply_to_message_id=reply_to_message_id,
                            text="*Sorry*, downloaded file `{}` is in format I could not yet convert or send".format(file_name),
                            parse_mode="Markdown",
                        )
                except FileTooLargeError as exc:
                    logger.info("Large file for convert: %s", file_name)
                    await bot.send_message(
                        chat_id=chat_id,
                        reply_to_message_id=reply_to_message_id,
                        text="*Sorry*, downloaded file `{}` is `{}` MB and it is larger than I could convert (`{} MB`)".format(
                            file_name, exc.file_size // 1000000, max_convert_file_size // 1000000
                        ),
                        parse_mode="Markdown",
                    )
                except FileSplittedPartiallyError as exc:
                    file_parts = exc.file_parts
                    logger.exception("Splitting failed: %s", file_name)
                    await bot.send_message(
                        chat_id=chat_id,
                        reply_to_message_id=reply_to_message_id,
                        text="*Sorry*, not enough memory to convert file `{}`..".format(file_name),
                        parse_mode="Markdown",
                    )
                except FileNotConvertedError as exc:
                    logger.exception("Splitting failed: %s", file_name)
                    await bot.send_message(
                        chat_id=chat_id,
                        reply_to_message_id=reply_to_message_id,
                        text="*Sorry*, not enough memory to convert file `{}`..".format(file_name),
                        parse_mode="Markdown",
                    )
                try:
                    caption = None
                    if flood == "yes":
                        addition = ""
                        url_obj = URL(url)
                        if SITES["yt"] in url_obj.host:
                            source = "YouTube"
                            file_root, file_ext = os.path.splitext(file_name)
                            file_title = file_root.replace(file_ext, "")
                            addition = ": " + file_title
                        elif SITES["sc"] in url_obj.host:
                            source = "SoundCloud"
                        elif SITES["bc"] in url_obj.host:
                            source = "Bandcamp"
                        else:
                            source = url_obj.host.replace(".com", "").replace("www.", "").replace("m.", "")
                        # if "youtu.be" in url_obj.host:
                        #     url = url.replace("http://", "").replace("https://", "")
                        # else:
                        #     url = shorten_url(url)
                        caption = "@{} _got it from_ [{}]({}){}".format(bot_username.replace("_", "\_"), source, url, addition.replace("_", "\_"))
                        # logger.info(caption)
                    reply_to_message_id_send = reply_to_message_id if flood == "yes" else None
                    sent_audio_ids = []
                    for index, file_part in enumerate(file_parts):
                        path = pathlib.Path(file_part)
                        file_name = os.path.split(file_part)[-1]
                        # file_name = translit(file_name, 'ru', reversed=True)
                        logger.info("Sending: %s", file_name)
                        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VOICE)
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
                        for i in range(3):
                            try:
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
                                    if "127.0.0.1" in tg_bot_api:
                                        audio = path.absolute().as_uri()
                                        logger.debug(audio)
                                    elif serve_audio:
                                        audio = str(urljoin(app_url, str(path.relative_to(dl_dir))))
                                        logger.debug(audio)
                                    else:
                                        audio = open(file_part, "rb")
                                    if i > 0:
                                        # maybe: Reply message not found
                                        reply_to_message_id_send = None
                                    audio_msg = await bot.send_audio(
                                        chat_id=chat_id,
                                        reply_to_message_id=reply_to_message_id_send,
                                        audio=audio,
                                        duration=duration,
                                        performer=performer,
                                        title=title,
                                        caption=caption_full,
                                        parse_mode="Markdown",
                                    )
                                    sent_audio_ids.append(audio_msg.audio.file_id)
                                    logger.info("Sending succeeded: %s", file_name)
                                    break
                                elif "tiktok." in file_part or "inst." in file_part:
                                    video = open(file_part, "rb")
                                    duration = float(ffmpeg.probe(file_part)["format"]["duration"])
                                    videostream = next(item for item in ffmpeg.probe(file_part)["streams"] if item["codec_type"] == "video")
                                    width = int(videostream["width"])
                                    height = int(videostream["height"])
                                    video_msg = await bot.send_video(
                                        chat_id=chat_id,
                                        reply_to_message_id=reply_to_message_id_send,
                                        video=video,
                                        supports_streaming=True,
                                        duration=duration,
                                        width=width,
                                        height=height,
                                        caption=caption_full,
                                        parse_mode="Markdown",
                                    )
                                    sent_audio_ids.append(video_msg.video.file_id)
                                    logger.info("Sending succeeded: %s", file_name)
                                    break
                            except TelegramError:
                                if i == 2:
                                    logger.exception("Sending failed because of TelegramError: %s", file_name)
                    if len(sent_audio_ids) != len(file_parts):
                        raise FileSentPartiallyError(sent_audio_ids)

                except FileSentPartiallyError as exc:
                    sent_audio_ids = exc.sent_audio_ids
                    await bot.send_message(
                        chat_id=chat_id,
                        reply_to_message_id=reply_to_message_id,
                        text="*Sorry*, could not send file `{}` or some of it's parts..".format(file_name),
                        parse_mode="Markdown",
                    )
                    logger.warning("Sending some parts failed: %s", file_name)

    if not serve_audio:
        shutil.rmtree(download_dir, ignore_errors=True)
    if wait_message_id:  # TODO: delete only once
        try:
            await bot.delete_message(chat_id=chat_id, message_id=wait_message_id)
        except:
            pass


async def common_command_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.channel_post:
        message = update.channel_post
    elif update.message:
        message = update.message
    chat_id = update.effective_chat.id
    chat_type = update.effective_chat.type
    init_chat_data(
        chat_data=context.chat_data,
        mode=("dl" if chat_type == Chat.PRIVATE else "ask"),
        flood=("no" if chat_id in no_flood_chat_ids else "yes"),
    )
    if not is_chat_allowed(chat_id):
        await context.bot.send_message(chat_id=chat_id, text="This command isn't allowed in this chat.")
        return
    reply_to_message_id = message.message_id
    command_entities = message.parse_entities(types=[MessageEntity.BOT_COMMAND])
    command_passed = False
    if not command_entities:
        command_passed = False
        # if no command then it is just a message and use default mode
        mode = context.chat_data["settings"]["mode"]
    else:
        command_passed = True
        # try to determine mode from command
        mode = None
        for entity_value in command_entities.values():
            mode = entity_value.replace("/", "").replace("@{}".format(bot_username), "")
            break
        if not mode:
            mode = "dl"
    if command_passed and not context.args:
        # rant_text = RANT_TEXT_PRIVATE if chat_type == Chat.PRIVATE else RANT_TEXT_PUBLIC
        # rant_text += "\nYou can simply send message with links (to download) OR command as `/{} <links>`.".format(mode)
        # rant_and_cleanup(context.bot, chat_id, rant_text, reply_to_message_id=reply_to_message_id)
        return
    event_name = ("{}_cmd".format(mode)) if command_passed else ("{}_msg".format(mode))
    log_and_track(event_name, message)

    apologize = False
    # apologize and send TYPING: always in PM, only when it's command in non-PM
    if chat_type == Chat.PRIVATE or command_passed:
        apologize = True
    source_ip = None
    proxy = None
    if source_ips:
        source_ip = random.choice(source_ips)
    if proxies:
        proxy = random.choice(proxies)
    await prepare_urls(
        context=context,
        message=message,
        mode=mode,
        source_ip=source_ip,
        proxy=proxy,
        apologize=apologize,
        chat_id=chat_id,
        reply_to_message_id=reply_to_message_id,
        bot=context.bot,
    )


async def prepare_urls(context: ContextTypes.DEFAULT_TYPE, message, mode=None, source_ip=None, proxy=None, apologize=None, chat_id=None, reply_to_message_id=None, bot=None):
    direct_urls = False
    if mode == "link":
        direct_urls = True

    if apologize:
        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    if isinstance(message, Message):
        urls = []
        url_entities = message.parse_entities(types=[MessageEntity.URL])
        url_caption_entities = message.parse_caption_entities(types=[MessageEntity.URL])
        url_entities.update(url_caption_entities)
        for entity in url_entities:
            url_str = url_entities[entity]
            if url_valid(url_str):
                logger.debug("Entity URL Parsed: %s", url_str)
                if "://" not in url_str:
                    url_str = "http://{}".format(url_str)
                urls.append(URL(url_str))
            else:
                logger.debug("Entry URL not valid or blacklisted: %s", url_str)
        text_link_entities = message.parse_entities(types=[MessageEntity.TEXT_LINK])
        text_link_caption_entities = message.parse_caption_entities(types=[MessageEntity.TEXT_LINK])
        text_link_entities.update(text_link_caption_entities)
        for entity in text_link_entities:
            url_str = entity.url
            if url_valid(url_str):
                logger.debug("Entity Text Link Parsed: %s", url_str)
                urls.append(URL(url_str))
            else:
                logger.debug("Entry URL not valid or blacklisted: %s", url_str)
    else:
        all_links = find_all_links(message, default_scheme="http")
        urls = [link for link in all_links if url_valid(link)]
    logger.debug(urls)

    urls_dict = {}
    for url_item in urls:
        # unshorten soundcloud.app.goo.gl and other links, but not tiktok or instagram or youtube:
        if "tiktok" in url_item.host or "instagr" in url_item.host or SITES["yt"] in url_item.host:
            url = url_item
        else:
            try:
                url = URL(
                    requests.head(
                        url_item,
                        allow_redirects=True,
                        timeout=5,
                        proxies=dict(http=proxy, https=proxy),
                        headers={"User-Agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:105.0) Gecko/20100101 Firefox/105.0"},
                    ).url
                )
            except:
                url = url_item
        url_text = url.to_text(True)
        # FIXME crutch:
        url_text = url_text.replace("m.soundcloud.com", "soundcloud.com")
        url_parts_num = len([part for part in url.path_parts if part])
        try:
            if (
                # SoundCloud: tracks, sets and widget pages, no /you/ pages  # TODO private sets are 5
                (SITES["sc"] in url.host and (2 <= url_parts_num <= 4 or SITES["scapi"] in url_text) and (not "you" in url.path_parts))
                or
                # Bandcamp: tracks and albums
                (SITES["bc"] in url.host and (2 <= url_parts_num <= 2))
                or
                # YouTube: videos and playlists
                (SITES["yt"] in url.host and ("youtu.be" in url.host or "watch" in url.path or "playlist" in url.path))
            ):
                if direct_urls or SITES["yt"] in url.host:
                    urls_dict[url_text] = get_direct_urls(url_text, cookies_file, COOKIES_DOWNLOAD_FILE, source_ip, proxy)
                else:
                    urls_dict[url_text] = "http"
            elif not any((site in url.host for site in SITES.values())):
                urls_dict[url_text] = get_direct_urls(url_text, cookies_file, COOKIES_DOWNLOAD_FILE, source_ip, proxy)
        except ProcessExecutionError:
            logger.debug("youtube-dl get-url failed: %s", url_text)
        except URLError as exc:
            urls_dict[url_text] = exc.status

    logger.debug(urls_dict)
    if not urls_dict and apologize:
        await bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id, text=NO_URLS_TEXT, parse_mode="Markdown")
        return

    if mode == "dl":
        wait_message = await bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id, parse_mode="Markdown", text=get_italic(get_wait_text()))
        for url in urls_dict:
            await download_url_and_send(
                bot, url, urls_dict[url], chat_id=chat_id, reply_to_message_id=reply_to_message_id, wait_message_id=wait_message.message_id, source_ip=source_ip, proxy=proxy
            )
    elif mode == "link":
        wait_message = await bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id, parse_mode="Markdown", text=get_italic(get_wait_text()))
        await bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id, parse_mode="Markdown", disable_web_page_preview=True, text=get_link_text(urls_dict))
        await bot.delete_message(chat_id=chat_id, message_id=wait_message.message_id)
    elif mode == "ask":
        # ask only if good urls exist
        if "http" in " ".join(urls_dict.values()):
            orig_msg_id = str(reply_to_message_id)
            context.chat_data[orig_msg_id] = {"message": message, "urls": urls_dict, "source_ip": source_ip, "proxy": proxy}
            question = "🎶 links found, what to do?"
            button_dl = InlineKeyboardButton(text="✅ Download", callback_data=" ".join([orig_msg_id, "dl"]))
            button_link = InlineKeyboardButton(text="❇️ Links", callback_data=" ".join([orig_msg_id, "link"]))
            button_cancel = InlineKeyboardButton(text="❎", callback_data=" ".join([orig_msg_id, "nodl"]))
            inline_keyboard = InlineKeyboardMarkup([[button_dl, button_link, button_cancel]])
            await bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id, reply_markup=inline_keyboard, text=question)


def youtube_dl_func(url, ydl_opts, queue=None):
    ydl = youtube_dl.YoutubeDL(ydl_opts)
    try:
        ydl.download([url])
    except Exception as exc:
        ydl_status = 1, str(exc)
        # ydl_status = exc  #TODO: pass and re-raise original Exception
    else:
        ydl_status = 0, "OK"
    if queue:
        queue.put(ydl_status)
    else:
        return ydl_status


def get_direct_urls(url, cookies_file=None, cookies_download_file=None, source_ip=None, proxy=None):
    logger.debug("Entered get_direct_urls")
    youtube_dl_args = []

    # https://github.com/ytdl-org/youtube-dl#how-do-i-pass-cookies-to-youtube-dl
    if cookies_file:
        if "http" in cookies_file:
            try:
                r = requests.get(cookies_file, allow_redirects=True, timeout=5)
                open(cookies_download_file, "wb").write(r.content)
                youtube_dl_args.extend(["--cookies", cookies_download_file])
            except:
                pass
        else:
            youtube_dl_args.extend(["--cookies", cookies_file])

    if source_ip:
        youtube_dl_args.extend(["--source-address", source_ip])

    if proxy:
        youtube_dl_args.extend(["--proxy", proxy])

    youtube_dl_args.extend(["--get-url", url])
    try:
        ret_code, std_out, std_err = youtube_dl_bin[youtube_dl_args].run(timeout=60)
    except ProcessTimedOut as exc:
        raise URLTimeoutError
    except ProcessExecutionError as exc:
        # TODO: look at case: one page has multiple videos, some available, some not
        if "returning it as such" in exc.stderr:
            raise URLDirectError
        if "proxy server" in exc.stderr:
            raise URLCountryError
        raise exc
    if "yt_live_broadcast" in std_out:
        raise URLLiveError
    return std_out


async def blacklist_whitelist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not is_chat_allowed(chat_id):
        await context.bot.leave_chat(chat_id)


def is_chat_allowed(chat_id):
    if whitelist_chats:
        if chat_id not in whitelist_chats:
            return False
    if blacklist_chats:
        if chat_id in blacklist_chats:
            return False
    if whitelist_chats and blacklist_chats:
        if chat_id in blacklist_chats:
            return False
    return True


def url_valid(url):
    telegram_domains = ["t.me", "telegram.org", "telegram.dog", "telegra.ph", "tdesktop.com", "telesco.pe", "graph.org", "contest.dev"]
    logger.debug("Checking Url Entity: %s", url)
    try:
        netloc = urlparse(url).netloc
    except AttributeError:
        return False
    if netloc in telegram_domains:
        return False
    return url_allowed(url)


def url_allowed(url):
    netloc = urlparse(url).netloc
    if whitelist_domains:
        if netloc not in whitelist_domains:
            return False
    if blacklist_domains:
        if netloc in blacklist_domains:
            return False
    if whitelist_domains and blacklist_domains:
        if netloc in blacklist_domains:
            return False
    return True


async def caps(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    name = update.effective_chat.full_name
    text_caps = " ".join(context.args).upper()
    context.job_queue.run_once(callback_30, 2, data=name, chat_id=chat_id)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=text_caps,
    )


async def callback_30(context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(
        chat_id=context.job.chat_id,
        text=f"BEEP {context.job.data}!",
    )


if __name__ == "__main__":
    main()
