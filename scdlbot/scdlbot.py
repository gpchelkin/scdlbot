# -*- coding: utf-8 -*-

"""Main module."""

import gc
import pathlib
import random
import shelve
import shutil
from datetime import datetime
from multiprocessing import Process, Queue
from queue import Empty
from subprocess import PIPE, TimeoutExpired  # skipcq: BAN-B404
from urllib.parse import urljoin, urlparse
from uuid import uuid4

import ffmpeg
from boltons.urlutils import find_all_links
from mutagen.id3 import ID3
from mutagen.mp3 import EasyMP3 as MP3
from prometheus_client import Summary
from telegram import Chat, ChatAction, ChatMember, InlineKeyboardButton, InlineKeyboardMarkup, Message, MessageEntity, Update
from telegram.error import BadRequest, ChatMigrated, NetworkError, TelegramError, TimedOut, Unauthorized
from telegram.ext import CallbackContext, CallbackQueryHandler, CommandHandler, Filters, MessageHandler, Updater
from telegram.ext.dispatcher import run_async

from scdlbot.utils import *

logger = logging.getLogger(__name__)

REQUEST_TIME = Summary("request_processing_seconds", "Time spent processing request")


class ScdlBot:
    def __init__(
        self,
        tg_bot_token,
        tg_bot_api="https://api.telegram.org",
        proxies=None,
        store_chat_id=None,
        no_flood_chat_ids=None,
        alert_chat_ids=None,
        dl_dir="/tmp/scdlbot",
        dl_timeout=300,
        max_tg_file_size=45_000_000,
        max_convert_file_size=80_000_000,
        chat_storage_file="/tmp/scdlbotdata",
        app_url=None,
        serve_audio=False,
        cookies_file=None,
        source_ips=None,
        workers=4,
    ):
        self.SITES = {
            "sc": "soundcloud",
            "scapi": "api.soundcloud",
            "bc": "bandcamp",
            "yt": "youtu",
        }
        self.APP_URL = app_url
        self.DL_TIMEOUT = dl_timeout
        self.TG_BOT_API = tg_bot_api
        self.MAX_TG_FILE_SIZE = max_tg_file_size
        self.MAX_CONVERT_FILE_SIZE = max_convert_file_size
        self.SERVE_AUDIO = serve_audio
        if self.SERVE_AUDIO:
            self.MAX_TG_FILE_SIZE = 19_000_000
        self.HELP_TEXT = get_response_text("help.tg.md")
        self.SETTINGS_TEXT = get_response_text("settings.tg.md")
        self.DL_TIMEOUT_TEXT = get_response_text("dl_timeout.txt").format(self.DL_TIMEOUT // 60)
        self.WAIT_BIT_TEXT = [get_response_text("wait_bit.txt"), get_response_text("wait_beat.txt"), get_response_text("wait_beet.txt")]
        self.NO_AUDIO_TEXT = get_response_text("no_audio.txt")
        self.NO_URLS_TEXT = get_response_text("no_urls.txt")
        self.OLD_MSG_TEXT = get_response_text("old_msg.txt")
        self.REGION_RESTRICTION_TEXT = get_response_text("region_restriction.txt")
        self.DIRECT_RESTRICTION_TEXT = get_response_text("direct_restriction.txt")
        self.LIVE_RESTRICTION_TEXT = get_response_text("live_restriction.txt")
        # self.chat_storage = {}
        self.chat_storage = shelve.open(chat_storage_file, writeback=True)
        for chat_id in no_flood_chat_ids:
            self.init_chat(chat_id=chat_id, chat_type=Chat.PRIVATE if chat_id > 0 else Chat.SUPERGROUP, flood="no")
        self.ALERT_CHAT_IDS = set(alert_chat_ids) if alert_chat_ids else set()
        self.STORE_CHAT_ID = store_chat_id
        self.DL_DIR = dl_dir
        self.COOKIES_DOWNLOAD_FILE = "/tmp/scdlbot_cookies.txt"
        self.proxies = proxies
        self.source_ips = source_ips
        # https://yandex.com/support/music-app-ios/search-and-listen/listening-abroad.html
        self.cookies_file = cookies_file
        self.workers = workers

        # if sc_auth_token:
        #     config = configparser.ConfigParser()
        #     config['scdl'] = {}
        #     config['scdl']['path'] = self.DL_DIR
        #     config['scdl']['auth_token'] = sc_auth_token
        #     config_dir = os.path.join(os.path.expanduser('~'), '.config', 'scdl')
        #     config_path = os.path.join(config_dir, 'scdl.cfg')
        #     os.makedirs(config_dir, exist_ok=True)
        #     with open(config_path, 'w') as config_file:
        #         config.write(config_file)

        self.updater = Updater(token=tg_bot_token, base_url=f"{self.TG_BOT_API}/bot", use_context=True, base_file_url=f"{self.TG_BOT_API}/file/bot", workers=self.workers)
        dispatcher = self.updater.dispatcher

        start_command_handler = CommandHandler("start", self.help_command_callback)
        dispatcher.add_handler(start_command_handler)
        help_command_handler = CommandHandler("help", self.help_command_callback)
        dispatcher.add_handler(help_command_handler)
        settings_command_handler = CommandHandler("settings", self.settings_command_callback)
        dispatcher.add_handler(settings_command_handler)

        dl_command_handler = CommandHandler("dl", self.common_command_callback, filters=~Filters.update.edited_message & ~Filters.forwarded)
        dispatcher.add_handler(dl_command_handler)
        link_command_handler = CommandHandler("link", self.common_command_callback, filters=~Filters.update.edited_message & ~Filters.forwarded)
        dispatcher.add_handler(link_command_handler)
        message_with_links_handler = MessageHandler(
            ~Filters.update.edited_message
            & ~Filters.command
            & (
                (Filters.text & (Filters.entity(MessageEntity.URL) | Filters.entity(MessageEntity.TEXT_LINK)))
                | (Filters.caption & (Filters.caption_entity(MessageEntity.URL) | Filters.caption_entity(MessageEntity.TEXT_LINK)))
            ),
            self.common_command_callback,
        )
        dispatcher.add_handler(message_with_links_handler)

        button_query_handler = CallbackQueryHandler(self.button_query_callback)
        dispatcher.add_handler(button_query_handler)

        unknown_handler = MessageHandler(Filters.command, self.unknown_command_callback)
        dispatcher.add_handler(unknown_handler)

        dispatcher.add_error_handler(self.error_callback)

        self.bot_username = self.updater.bot.get_me().username
        self.RANT_TEXT_PRIVATE = "Read /help to learn how to use me"
        self.RANT_TEXT_PUBLIC = "[Start me in PM to read help and learn how to use me](t.me/{}?start=1)".format(self.bot_username)

    def start(self, use_webhook=False, webhook_host="127.0.0.1", webhook_port=None, cert_file=None, cert_key_file=None, url_path="scdlbot"):
        if use_webhook:
            self.updater.start_webhook(listen=webhook_host, port=webhook_port, url_path=url_path)
            # cert=cert_file if cert_file else None,
            # key=cert_key_file if cert_key_file else None,
            # webhook_url=urljoin(app_url, url_path))
            self.updater.bot.set_webhook(url=urljoin(self.APP_URL, url_path), certificate=open(cert_file, "rb") if cert_file else None)
        else:
            self.updater.start_polling()
        logger.warning("Bot started")
        self.updater.idle()

    def unknown_command_callback(self, update: Update, context: CallbackContext):
        pass
        # bot.send_message(chat_id=update.message.chat_id, text="Unknown command")

    def error_callback(self, update: Update, context: CallbackContext):  # skipcq: PYL-R0201
        try:
            raise context.error
        except Unauthorized:
            # remove update.message.chat_id from conversation list
            logger.debug("Update {} caused Unauthorized error: {}".format(update, context.error))
        except BadRequest:
            # handle malformed requests - read more below!
            logger.debug("Update {} caused BadRequest error: {}".format(update, context.error))
        except TimedOut:
            # handle slow connection problems
            logger.debug("Update {} caused TimedOut error: {}".format(update, context.error))
        except NetworkError:
            # handle other connection problems
            logger.debug("Update {} caused NetworkError: {}".format(update, context.error))
        except ChatMigrated as e:
            # the chat_id of a group has changed, use e.new_chat_id instead
            logger.debug("Update {} caused ChatMigrated error: {}".format(update, context.error))
        except TelegramError:
            # handle all other telegram related errors
            logger.debug("Update {} caused TelegramError: {}".format(update, context.error))

    def init_chat(self, message=None, chat_id=None, chat_type=None, flood="yes"):
        if message:
            chat_id = str(message.chat_id)
            chat_type = message.chat.type
        else:
            chat_id = str(chat_id)
        if chat_id not in self.chat_storage:
            self.chat_storage[chat_id] = {}
        if "settings" not in self.chat_storage[chat_id]:
            self.chat_storage[chat_id]["settings"] = {}
        if "mode" not in self.chat_storage[chat_id]["settings"]:
            if chat_type == Chat.PRIVATE:
                self.chat_storage[chat_id]["settings"]["mode"] = "dl"
            else:
                self.chat_storage[chat_id]["settings"]["mode"] = "ask"
        if "flood" not in self.chat_storage[chat_id]["settings"]:
            self.chat_storage[chat_id]["settings"]["flood"] = flood
        if "rant_msg_ids" not in self.chat_storage[chat_id]["settings"]:
            self.chat_storage[chat_id]["settings"]["rant_msg_ids"] = []
        self.chat_storage.sync()
        # logger.debug("Current chat_storage: %r", self.chat_storage)

    def cleanup_chat(self, chat_id):
        chat_msgs = self.chat_storage[str(chat_id)].copy()
        for msg_id in chat_msgs:
            if msg_id != "settings":
                timedelta = datetime.now().replace(tzinfo=None) - self.chat_storage[str(chat_id)][msg_id]["message"].date.replace(tzinfo=None)
                if timedelta.days > 0:
                    self.chat_storage[str(chat_id)].pop(msg_id)
        self.chat_storage.sync()

    def rant_and_cleanup(self, bot, chat_id, rant_text, reply_to_message_id=None):
        rant_msg = bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id, text=rant_text, parse_mode="Markdown", disable_web_page_preview=True)
        flood = self.chat_storage[str(chat_id)]["settings"]["flood"]
        if flood == "no":
            rant_msgs = self.chat_storage[str(chat_id)]["settings"]["rant_msg_ids"].copy()
            for rant_msg_id in rant_msgs:
                try:
                    bot.delete_message(chat_id=chat_id, message_id=rant_msg_id)
                except:
                    pass
                self.chat_storage[str(chat_id)]["settings"]["rant_msg_ids"].remove(rant_msg_id)
            self.chat_storage[str(chat_id)]["settings"]["rant_msg_ids"].append(rant_msg.message_id)
            self.chat_storage.sync()

    def help_command_callback(self, update: Update, context: CallbackContext):
        self.init_chat(update.message)
        event_name = "help"
        entities = update.message.parse_entities(types=[MessageEntity.BOT_COMMAND])
        for entity_value in entities.values():
            event_name = entity_value.replace("/", "").replace("@{}".format(self.bot_username), "")
            break
        log_and_track(event_name, update.message)
        chat_id = update.message.chat_id
        chat_type = update.message.chat.type
        reply_to_message_id = update.message.message_id
        flood = self.chat_storage[str(chat_id)]["settings"]["flood"]
        if chat_type != Chat.PRIVATE and flood == "no":
            self.rant_and_cleanup(context.bot, chat_id, self.RANT_TEXT_PUBLIC, reply_to_message_id=reply_to_message_id)
        else:
            context.bot.send_message(chat_id=chat_id, text=self.HELP_TEXT, parse_mode="Markdown", disable_web_page_preview=True)

    def get_wait_text(self):
        return random.choice(self.WAIT_BIT_TEXT)

    def get_settings_inline_keyboard(self, chat_id):
        mode = self.chat_storage[str(chat_id)]["settings"]["mode"]
        flood = self.chat_storage[str(chat_id)]["settings"]["flood"]
        emoji_yes = "✅"
        emoji_no = "❌"
        button_dl = InlineKeyboardButton(text=" ".join([emoji_yes if mode == "dl" else emoji_no, "Download"]), callback_data=" ".join(["settings", "dl"]))
        button_link = InlineKeyboardButton(text=" ".join([emoji_yes if mode == "link" else emoji_no, "Links"]), callback_data=" ".join(["settings", "link"]))
        button_ask = InlineKeyboardButton(text=" ".join([emoji_yes if mode == "ask" else emoji_no, "Ask"]), callback_data=" ".join(["settings", "ask"]))
        button_flood = InlineKeyboardButton(text=" ".join([emoji_yes if flood == "yes" else emoji_no, "Captions"]), callback_data=" ".join(["settings", "flood"]))
        button_close = InlineKeyboardButton(text=" ".join([emoji_no, "Close settings"]), callback_data=" ".join(["settings", "close"]))
        inline_keyboard = InlineKeyboardMarkup([[button_dl, button_link, button_ask], [button_flood, button_close]])
        return inline_keyboard

    def settings_command_callback(self, update: Update, context: CallbackContext):
        self.init_chat(update.message)
        log_and_track("settings")
        chat_id = update.message.chat_id
        context.bot.send_message(chat_id=chat_id, parse_mode="Markdown", reply_markup=self.get_settings_inline_keyboard(chat_id), text=self.SETTINGS_TEXT)

    def common_command_callback(self, update: Update, context: CallbackContext):
        self.init_chat(update.message)
        chat_id = update.message.chat_id
        chat_type = update.message.chat.type
        reply_to_message_id = update.message.message_id
        command_entities = update.message.parse_entities(types=[MessageEntity.BOT_COMMAND])
        command_passed = False
        if not command_entities:
            command_passed = False
            # if no command then it is just a message and use default mode
            mode = self.chat_storage[str(chat_id)]["settings"]["mode"]
        else:
            command_passed = True
            # try to determine mode from command
            mode = None
            for entity_value in command_entities.values():
                mode = entity_value.replace("/", "").replace("@{}".format(self.bot_username), "")
                break
            if not mode:
                mode = "dl"
        if command_passed and not context.args:
            rant_text = self.RANT_TEXT_PRIVATE if chat_type == Chat.PRIVATE else self.RANT_TEXT_PUBLIC
            rant_text += "\nYou can simply send message with links (to download) OR command as `/{} <links>`.".format(mode)
            self.rant_and_cleanup(context.bot, chat_id, rant_text, reply_to_message_id=reply_to_message_id)
            return
        event_name = ("{}_cmd".format(mode)) if command_passed else ("{}_msg".format(mode))
        log_and_track(event_name, update.message)

        apologize = False
        # apologize and send TYPING: always in PM, only when it's command in non-PM
        if chat_type == Chat.PRIVATE or command_passed:
            apologize = True
        source_ip = None
        proxy = None
        if self.source_ips:
            source_ip = random.choice(self.source_ips)
        if self.proxies:
            proxy = random.choice(self.proxies)
        self.prepare_urls(
            message=update.message, mode=mode, source_ip=source_ip, proxy=proxy, apologize=apologize, chat_id=chat_id, reply_to_message_id=reply_to_message_id, bot=context.bot
        )

    def button_query_callback(self, update: Update, context: CallbackContext):
        btn_msg = update.callback_query.message
        self.init_chat(btn_msg)
        user_id = update.callback_query.from_user.id
        btn_msg_id = btn_msg.message_id
        chat = btn_msg.chat
        chat_id = chat.id
        chat_type = chat.type
        orig_msg_id, action = update.callback_query.data.split()
        if orig_msg_id == "settings":
            if chat_type != Chat.PRIVATE:
                chat_member_status = chat.get_member(user_id).status
                if chat_member_status not in [ChatMember.ADMINISTRATOR, ChatMember.CREATOR] and user_id not in self.ALERT_CHAT_IDS:
                    log_and_track("settings_fail")
                    update.callback_query.answer(text="You're not chat admin")
                    return
            log_and_track("settings_{}".format(action), btn_msg)
            if action == "close":
                context.bot.delete_message(chat_id, btn_msg_id)
            else:
                setting_changed = False
                if action in ["dl", "link", "ask"]:
                    current_setting = self.chat_storage[str(chat_id)]["settings"]["mode"]
                    if action != current_setting:
                        setting_changed = True
                        self.chat_storage[str(chat_id)]["settings"]["mode"] = action
                elif action in ["flood"]:
                    current_setting = self.chat_storage[str(chat_id)]["settings"]["flood"]
                    setting_changed = True
                    self.chat_storage[str(chat_id)]["settings"][action] = "no" if current_setting == "yes" else "yes"
                if setting_changed:
                    self.chat_storage.sync()
                    update.callback_query.answer(text="Settings changed")
                    update.callback_query.edit_message_reply_markup(parse_mode="Markdown", reply_markup=self.get_settings_inline_keyboard(chat_id))
                else:
                    update.callback_query.answer(text="Settings not changed")

        elif orig_msg_id in self.chat_storage[str(chat_id)]:
            msg_from_storage = self.chat_storage[str(chat_id)].pop(orig_msg_id)
            orig_msg = msg_from_storage["message"]
            urls = msg_from_storage["urls"]
            source_ip = msg_from_storage["source_ip"]
            proxy = msg_from_storage["proxy"]
            log_and_track("{}_msg".format(action), orig_msg)
            if action == "dl":
                update.callback_query.answer(text=self.get_wait_text())
                wait_message = update.callback_query.edit_message_text(parse_mode="Markdown", text=get_italic(self.get_wait_text()))
                for url in urls:
                    self.download_url_and_send(
                        context.bot, url, urls[url], chat_id=chat_id, reply_to_message_id=orig_msg_id, wait_message_id=wait_message.message_id, source_ip=source_ip, proxy=proxy
                    )
            elif action == "link":
                context.bot.send_message(chat_id=chat_id, reply_to_message_id=orig_msg_id, parse_mode="Markdown", disable_web_page_preview=True, text=get_link_text(urls))
                context.bot.delete_message(chat_id=chat_id, message_id=btn_msg_id)
            elif action == "nodl":
                context.bot.delete_message(chat_id=chat_id, message_id=btn_msg_id)
        else:
            update.callback_query.answer(text=self.OLD_MSG_TEXT)
            context.bot.delete_message(chat_id=chat_id, message_id=btn_msg_id)

    @REQUEST_TIME.time()
    @run_async
    def prepare_urls(self, message, mode=None, source_ip=None, proxy=None, apologize=None, chat_id=None, reply_to_message_id=None, bot=None):
        direct_urls = False
        if mode == "link":
            direct_urls = True

        if apologize:
            bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

        if isinstance(message, Message):
            urls = []
            url_entities = message.parse_entities(types=[MessageEntity.URL])
            url_caption_entities = message.parse_caption_entities(types=[MessageEntity.URL])
            url_entities.update(url_caption_entities)
            for entity in url_entities:
                url_str = url_entities[entity]
                logger.debug("Entity URL Parsed: %s", url_str)
                if "://" not in url_str:
                    url_str = "http://{}".format(url_str)
                urls.append(URL(url_str))
            text_link_entities = message.parse_entities(types=[MessageEntity.TEXT_LINK])
            text_link_caption_entities = message.parse_caption_entities(types=[MessageEntity.TEXT_LINK])
            text_link_entities.update(text_link_caption_entities)
            for entity in text_link_entities:
                url_str = entity.url
                logger.debug("Entity Text Link Parsed: %s", url_str)
                urls.append(URL(url_str))
        else:
            urls = find_all_links(message, default_scheme="http")
        logger.debug(urls)

        urls_dict = {}
        for url_item in urls:
            # unshorten soundcloud.app.goo.gl and other links, but not tiktok or instagram or youtube:
            if "tiktok" in url_item.host or "instagr" in url_item.host or self.SITES["yt"] in url_item.host:
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
                    (self.SITES["sc"] in url.host and (2 <= url_parts_num <= 4 or self.SITES["scapi"] in url_text) and (not "you" in url.path_parts))
                    or
                    # Bandcamp: tracks and albums
                    (self.SITES["bc"] in url.host and (2 <= url_parts_num <= 2))
                    or
                    # YouTube: videos and playlists
                    (self.SITES["yt"] in url.host and ("youtu.be" in url.host or "watch" in url.path or "playlist" in url.path))
                ):
                    if direct_urls or self.SITES["yt"] in url.host:
                        urls_dict[url_text] = get_direct_urls(url_text, self.cookies_file, self.COOKIES_DOWNLOAD_FILE, source_ip, proxy)
                    else:
                        urls_dict[url_text] = "http"
                elif not any((site in url.host for site in self.SITES.values())):
                    urls_dict[url_text] = get_direct_urls(url_text, self.cookies_file, self.COOKIES_DOWNLOAD_FILE, source_ip, proxy)
            except ProcessExecutionError:
                logger.debug("youtube-dl get-url failed: %s", url_text)
            except URLError as exc:
                urls_dict[url_text] = exc.status

        logger.debug(urls_dict)
        if not urls_dict and apologize:
            bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id, text=self.NO_URLS_TEXT, parse_mode="Markdown")
            return

        if mode == "dl":
            wait_message = bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id, parse_mode="Markdown", text=get_italic(self.get_wait_text()))
            for url in urls_dict:
                self.download_url_and_send(
                    bot, url, urls_dict[url], chat_id=chat_id, reply_to_message_id=reply_to_message_id, wait_message_id=wait_message.message_id, source_ip=source_ip, proxy=proxy
                )
        elif mode == "link":
            wait_message = bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id, parse_mode="Markdown", text=get_italic(self.get_wait_text()))
            bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id, parse_mode="Markdown", disable_web_page_preview=True, text=get_link_text(urls_dict))
            bot.delete_message(chat_id=chat_id, message_id=wait_message.message_id)
        elif mode == "ask":
            # ask only if good urls exist
            if "http" in " ".join(urls_dict.values()):
                orig_msg_id = str(reply_to_message_id)
                self.chat_storage[str(chat_id)][orig_msg_id] = {"message": message, "urls": urls_dict, "source_ip": source_ip, "proxy": proxy}
                question = "🎶 links found, what to do?"
                button_dl = InlineKeyboardButton(text="✅ Download", callback_data=" ".join([orig_msg_id, "dl"]))
                button_link = InlineKeyboardButton(text="❇️ Links", callback_data=" ".join([orig_msg_id, "link"]))
                button_cancel = InlineKeyboardButton(text="❎", callback_data=" ".join([orig_msg_id, "nodl"]))
                inline_keyboard = InlineKeyboardMarkup([[button_dl, button_link, button_cancel]])
                bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id, reply_markup=inline_keyboard, text=question)
            self.cleanup_chat(chat_id)

    @REQUEST_TIME.time()
    @run_async
    def download_url_and_send(self, bot, url, direct_urls, chat_id, reply_to_message_id=None, wait_message_id=None, source_ip=None, proxy=None):
        bot.send_chat_action(chat_id=chat_id, action=ChatAction.RECORD_AUDIO)
        download_dir = os.path.join(self.DL_DIR, str(uuid4()))
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
            if (self.SITES["sc"] in url and self.SITES["scapi"] not in url) or (self.SITES["bc"] in url):
                cmd_name = "scdl"
                cmd_args = []
                cmd = None
                cmd_input = None
                if self.SITES["sc"] in url and self.SITES["scapi"] not in url:
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
                elif self.SITES["bc"] in url:
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
                    cmd_stdout, cmd_stderr = cmd_proc.communicate(input=cmd_input, timeout=self.DL_TIMEOUT)
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
                            "preferredquality": "128",
                        },
                        # {'key': 'EmbedThumbnail',}, {'key': 'FFmpegMetadata',},
                    ],
                    "noplaylist": True,
                }
            if proxy:
                ydl_opts["proxy"] = proxy
            if source_ip:
                ydl_opts["source_address"] = source_ip
            # https://github.com/ytdl-org/youtube-dl/blob/master/youtube_dl/YoutubeDL.py#L210
            if self.cookies_file:
                if "http" in self.cookies_file:
                    ydl_opts["cookiefile"] = self.COOKIES_DOWNLOAD_FILE
                else:
                    ydl_opts["cookiefile"] = self.cookies_file
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
                cmd_retcode, cmd_stderr = queue.get(block=True, timeout=self.DL_TIMEOUT)
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
            bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id, text=self.DL_TIMEOUT_TEXT, parse_mode="Markdown")
        elif status == -2:
            bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id, text=self.NO_AUDIO_TEXT, parse_mode="Markdown")
        elif status == -3:
            bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id, text=self.DIRECT_RESTRICTION_TEXT, parse_mode="Markdown")
        elif status == -4:
            bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id, text=self.REGION_RESTRICTION_TEXT, parse_mode="Markdown")
        elif status == -5:
            bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id, text=self.LIVE_RESTRICTION_TEXT, parse_mode="Markdown")
        elif status == 1:
            file_list = []
            for d, dirs, files in os.walk(download_dir):
                for file in files:
                    file_list.append(os.path.join(d, file))
            if not file_list:
                logger.info("No files in dir: %s", download_dir)
                bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id, text="*Sorry*, I couldn't download any files from provided links", parse_mode="Markdown")
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
                        if file_size > self.MAX_CONVERT_FILE_SIZE:
                            raise FileTooLargeError(file_size)
                        # FIXME tiktok.mp4 is for tiktok, inst.mp4 for instagram
                        if file_format not in ["mp3"] and not ("tiktok." in file or "inst." in file):
                            logger.info("Converting: %s", file)
                            try:
                                file_converted = file.replace(file_ext, ".mp3")
                                ffinput = ffmpeg.input(file)
                                ffmpeg.output(ffinput, file_converted, audio_bitrate="128k", vn=None).run()
                                file = file_converted
                                file_root, file_ext = os.path.splitext(file)
                                file_format = file_ext.replace(".", "").lower()
                                file_size = os.path.getsize(file)
                            except Exception:
                                # TODO exceptions
                                raise FileNotConvertedError

                        file_parts = []
                        if file_size <= self.MAX_TG_FILE_SIZE:
                            file_parts.append(file)
                        else:
                            logger.info("Splitting: %s", file)
                            id3 = None
                            try:
                                id3 = ID3(file, translate=False)
                            except:
                                pass

                            parts_number = file_size // self.MAX_TG_FILE_SIZE + 1

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
                            bot.send_message(
                                chat_id=chat_id,
                                reply_to_message_id=reply_to_message_id,
                                text="*Sorry*, downloaded file `{}` is in format I could not yet convert or send".format(file_name),
                                parse_mode="Markdown",
                            )
                    except FileTooLargeError as exc:
                        logger.info("Large file for convert: %s", file_name)
                        bot.send_message(
                            chat_id=chat_id,
                            reply_to_message_id=reply_to_message_id,
                            text="*Sorry*, downloaded file `{}` is `{}` MB and it is larger than I could convert (`{} MB`)".format(
                                file_name, exc.file_size // 1000000, self.MAX_CONVERT_FILE_SIZE // 1000000
                            ),
                            parse_mode="Markdown",
                        )
                    except FileSplittedPartiallyError as exc:
                        file_parts = exc.file_parts
                        logger.exception("Splitting failed: %s", file_name)
                        bot.send_message(
                            chat_id=chat_id,
                            reply_to_message_id=reply_to_message_id,
                            text="*Sorry*, not enough memory to convert file `{}`..".format(file_name),
                            parse_mode="Markdown",
                        )
                    except FileNotConvertedError as exc:
                        logger.exception("Splitting failed: %s", file_name)
                        bot.send_message(
                            chat_id=chat_id,
                            reply_to_message_id=reply_to_message_id,
                            text="*Sorry*, not enough memory to convert file `{}`..".format(file_name),
                            parse_mode="Markdown",
                        )
                    try:
                        caption = None
                        flood = self.chat_storage[str(chat_id)]["settings"]["flood"]
                        if flood == "yes":
                            addition = ""
                            url_obj = URL(url)
                            if self.SITES["yt"] in url_obj.host:
                                source = "YouTube"
                                file_root, file_ext = os.path.splitext(file_name)
                                file_title = file_root.replace(file_ext, "")
                                addition = ": " + file_title
                            elif self.SITES["sc"] in url_obj.host:
                                source = "SoundCloud"
                            elif self.SITES["bc"] in url_obj.host:
                                source = "Bandcamp"
                            else:
                                source = url_obj.host.replace(".com", "").replace("www.", "").replace("m.", "")
                            # if "youtu.be" in url_obj.host:
                            #     url = url.replace("http://", "").replace("https://", "")
                            # else:
                            #     url = shorten_url(url)
                            caption = "@{} _got it from_ [{}]({}){}".format(self.bot_username.replace("_", "\_"), source, url, addition.replace("_", "\_"))
                            # logger.info(caption)
                        reply_to_message_id_send = reply_to_message_id if flood == "yes" else None
                        sent_audio_ids = []
                        for index, file_part in enumerate(file_parts):
                            path = pathlib.Path(file_part)
                            file_name = os.path.split(file_part)[-1]
                            # file_name = translit(file_name, 'ru', reversed=True)
                            logger.info("Sending: %s", file_name)
                            bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_AUDIO)
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
                                        if "127.0.0.1" in self.TG_BOT_API:
                                            audio = path.absolute().as_uri()
                                            logger.debug(audio)
                                        elif self.SERVE_AUDIO:
                                            audio = str(urljoin(self.APP_URL, str(path.relative_to(self.DL_DIR))))
                                            logger.debug(audio)
                                        else:
                                            audio = open(file_part, "rb")
                                        if i > 0:
                                            # maybe: Reply message not found
                                            reply_to_message_id_send = None
                                        audio_msg = bot.send_audio(
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
                                        video_msg = bot.send_video(
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
                        bot.send_message(
                            chat_id=chat_id,
                            reply_to_message_id=reply_to_message_id,
                            text="*Sorry*, could not send file `{}` or some of it's parts..".format(file_name),
                            parse_mode="Markdown",
                        )
                        logger.warning("Sending some parts failed: %s", file_name)

        if not self.SERVE_AUDIO:
            shutil.rmtree(download_dir, ignore_errors=True)
        if wait_message_id:  # TODO: delete only once
            try:
                bot.delete_message(chat_id=chat_id, message_id=wait_message_id)
            except:
                pass
