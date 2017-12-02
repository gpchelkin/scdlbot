# -*- coding: utf-8 -*-

"""Main module."""

import configparser
import gc
import json
import logging
import multiprocessing
import os
import shelve
import shutil
from datetime import datetime
from queue import Empty
from subprocess import PIPE, TimeoutExpired
from urllib.parse import urljoin
from uuid import uuid4

import mutagen.id3
import pkg_resources
import youtube_dl
from boltons.urlutils import find_all_links, URL
from botanio import botan
from plumbum import ProcessExecutionError
from plumbum import local
from pydub import AudioSegment
from pyshorteners import Shortener
from telegram import Message, Chat, MessageEntity, ChatAction, InlineKeyboardMarkup, InlineKeyboardButton, \
    InlineQueryResultAudio
from telegram.error import (TelegramError, Unauthorized, BadRequest,
                            TimedOut, ChatMigrated, NetworkError)
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, InlineQueryHandler, CallbackQueryHandler
from telegram.ext.dispatcher import run_async

from scdlbot.exceptions import *

logger = logging.getLogger(__name__)


class SCDLBot:

    def __init__(self, tg_bot_token, botan_token=None, google_shortener_api_key=None, bin_path="",
                 sc_auth_token=None, store_chat_id=None, no_flood_chat_ids=None,
                 alert_chat_ids=None, dl_dir="/tmp/scdl", dl_timeout=300,
                 max_convert_file_size=80000000, chat_storage_file="/tmp/scdlbotdata"):
        self.MAX_TG_FILE_SIZE = 45000000
        self.SITES = {
            "sc": "soundcloud",
            "scapi": "api.soundcloud",
            "bc": "bandcamp",
            "yt": "youtu",
        }
        self.DL_TIMEOUT = dl_timeout
        self.MAX_CONVERT_FILE_SIZE = max_convert_file_size
        self.HELP_TEXT = self.get_response_text('help.tg.md')
        self.SETTINGS_TEXT = self.get_response_text('settings.tg.md')
        self.DL_TIMEOUT_TEXT = self.get_response_text('dl_timeout.txt').format(self.DL_TIMEOUT // 60)
        self.WAIT_TEXT = self.get_response_text('wait.txt')
        self.NO_AUDIO_TEXT = self.get_response_text('no_audio.txt')
        self.NO_URLS_TEXT = self.get_response_text('no_urls.txt')
        self.REGION_RESTRICTION_TEXT = self.get_response_text('region_restriction.txt')
        self.DIRECT_RESTRICTION_TEXT = self.get_response_text('direct_restriction.txt')
        self.LIVE_RESTRICTION_TEXT = self.get_response_text('live_restriction.txt')
        # self.chat_storage = {}
        self.chat_storage = shelve.open(chat_storage_file, writeback=True)
        for chat_id in no_flood_chat_ids:
            self.init_chat(str(chat_id), Chat.PRIVATE if chat_id > 0 else Chat.SUPERGROUP, flood="no")
        self.ALERT_CHAT_IDS = set(alert_chat_ids) if alert_chat_ids else set()
        self.STORE_CHAT_ID = store_chat_id
        self.DL_DIR = dl_dir
        self.scdl = local[os.path.join(bin_path, 'scdl')]
        self.bandcamp_dl = local[os.path.join(bin_path, 'bandcamp-dl')]
        self.youtube_dl = local[os.path.join(bin_path, 'youtube-dl')]
        self.botan_token = botan_token if botan_token else None
        self.shortener = Shortener('Google', api_key=google_shortener_api_key) if google_shortener_api_key else None

        config = configparser.ConfigParser()
        config['scdl'] = {}
        config['scdl']['path'] = self.DL_DIR
        if sc_auth_token:
            config['scdl']['auth_token'] = sc_auth_token
        config_dir = os.path.join(os.path.expanduser('~'), '.config', 'scdl')
        config_path = os.path.join(config_dir, 'scdl.cfg')
        os.makedirs(config_dir, exist_ok=True)
        with open(config_path, 'w') as config_file:
            config.write(config_file)

        self.updater = Updater(token=tg_bot_token)
        dispatcher = self.updater.dispatcher

        start_command_handler = CommandHandler('start', self.start_command_callback)
        dispatcher.add_handler(start_command_handler)
        help_command_handler = CommandHandler('help', self.help_command_callback)
        dispatcher.add_handler(help_command_handler)
        settings_command_handler = CommandHandler('settings', self.settings_command_callback)
        dispatcher.add_handler(settings_command_handler)

        dl_command_handler = CommandHandler('dl', self.common_command_callback, filters=~ Filters.forwarded, pass_args=True)
        dispatcher.add_handler(dl_command_handler)
        link_command_handler = CommandHandler('link', self.link_command_callback, filters=~ Filters.forwarded,
                                              pass_args=True)
        dispatcher.add_handler(link_command_handler)

        message_with_links_handler = MessageHandler(Filters.text & (Filters.entity(MessageEntity.URL) |
                                                                    Filters.entity(MessageEntity.TEXT_LINK)),
                                                    self.message_callback)
        dispatcher.add_handler(message_with_links_handler)

        message_callback_query_handler = CallbackQueryHandler(self.button_query_callback)
        dispatcher.add_handler(message_callback_query_handler)

        inline_query_handler = InlineQueryHandler(self.inline_query_callback)
        dispatcher.add_handler(inline_query_handler)

        unknown_handler = MessageHandler(Filters.command, self.unknown_command_callback)
        dispatcher.add_handler(unknown_handler)

        dispatcher.add_error_handler(self.error_callback)

        self.bot_username = self.updater.bot.get_me().username
        self.RANT_TEXT_PRIVATE = "Read /help to learn how to use me"
        self.RANT_TEXT_PUBLIC = "[Press here and start to read help in my PM to learn how to use me](t.me/" + self.bot_username + "?start=1)"

    def start(self, use_webhook=False, app_url=None, webhook_port=None, cert_file=None, cert_key_file=None,
              webhook_host="0.0.0.0",
              url_path="scdlbot"):
        if use_webhook:
            url_path = url_path.replace(":", "")
            self.updater.start_webhook(listen=webhook_host,
                                       port=webhook_port,
                                       url_path=url_path, )
            # cert=cert_file if cert_file else None,
            # key=cert_key_file if cert_key_file else None,
            # webhook_url=urljoin(app_url, url_path))
            self.updater.bot.set_webhook(url=urljoin(app_url, url_path),
                                         certificate=open(cert_file, 'rb') if cert_file else None)
        else:
            self.updater.start_polling()
        # self.send_alert(self.updater.bot, "bot restarted")
        self.updater.idle()

    def unknown_command_callback(self, bot, update):
        pass
        # bot.send_message(chat_id=update.message.chat_id, text="Unknown command")

    def error_callback(self, bot, update, error):
        try:
            raise error
        except Unauthorized:
            # remove update.message.chat_id from conversation list
            logger.debug('Update {} caused Unauthorized error: {}'.format(update, error))
        except BadRequest:
            # handle malformed requests - read more below!
            logger.debug('Update {} caused BadRequest error: {}'.format(update, error))
        except TimedOut:
            # handle slow connection problems
            logger.debug('Update {} caused TimedOut error: {}'.format(update, error))
        except NetworkError:
            # handle other connection problems
            logger.debug('Update {} caused NetworkError: {}'.format(update, error))
        except ChatMigrated as e:
            # the chat_id of a group has changed, use e.new_chat_id instead
            logger.debug('Update {} caused ChatMigrated error: {}'.format(update, error))
        except TelegramError:
            # handle all other telegram related errors
            logger.debug('Update {} caused TelegramError: {}'.format(update, error))

    @staticmethod
    def get_response_text(file_name):
        # https://stackoverflow.com/a/20885799/2490759
        path = '/'.join(('texts', file_name))
        return pkg_resources.resource_string(__name__, path).decode("UTF-8")

    @staticmethod
    def md_italic(text):
        return "".join(["_", text, "_"])

    def init_chat(self, chat_id, chat_type, flood="yes"):
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
        logger.debug(self.chat_storage)

    def log_and_botan_track(self, event_name, message=None):
        logger.info("Event: %s", event_name)
        if message:
            if self.botan_token:
                try:
                    # uid = message.chat_id
                    uid = message.from_user.id
                except AttributeError:
                    logger.warning('No chat_id in message')
                    return False
                data = json.loads(message.to_json())
                return botan.track(self.botan_token, uid, data, event_name)
            else:
                return False

    def send_alert(self, bot, text, url=""):
        for alert_chat_id in self.ALERT_CHAT_IDS:
            try:
                bot.send_message(chat_id=alert_chat_id,
                                 text="BOT ADMIN ALERT\nURL or file failed:\n" + url + "\n" + text)
            except:
                pass

    def rant_and_cleanup(self, bot, chat_id, rant_text, reply_to_message_id=None):
        rant_msg = bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id,
                                    text=rant_text, parse_mode='Markdown', disable_web_page_preview=True)
        flood = self.chat_storage[str(chat_id)]["settings"]["flood"]
        if flood == "no":
            for rant_msg_id in self.chat_storage[str(chat_id)]["settings"]["rant_msg_ids"]:
                try:
                    bot.delete_message(chat_id=chat_id, message_id=rant_msg_id)
                except:
                    pass
                self.chat_storage[str(chat_id)]["settings"]["rant_msg_ids"].remove(rant_msg_id)
            self.chat_storage[str(chat_id)]["settings"]["rant_msg_ids"].append(rant_msg.message_id)
            self.chat_storage.sync()

    def get_link_text(self, urls):
        link_text = ""
        for i, url in enumerate(urls):
            link_text += "[Source Link #{}]({}) | `{}`\n".format(str(i + 1), url, URL(url).host)
            direct_urls = urls[url].splitlines()
            for j, direct_url in enumerate(direct_urls):
                logger.debug(direct_url)
                if "http" in direct_url:
                    content_type = ""
                    if "googlevideo" in direct_url:
                        if "audio" in direct_url:
                            content_type = "Audio"
                        else:
                            content_type = "Video"
                    if self.shortener:
                        try:
                            direct_url = self.shortener.short(direct_url)
                        except:
                            pass
                    link_text += "• {} [Direct Link]({})\n".format(content_type, direct_url)
        return link_text

    def get_settings_inline_keyboard(self, chat_id):
        mode = self.chat_storage[str(chat_id)]["settings"]["mode"]
        flood = self.chat_storage[str(chat_id)]["settings"]["flood"]
        emoji_yes = "✅"
        emoji_no = "❌"
        button_dl = InlineKeyboardButton(text=" ".join([emoji_yes if mode == "dl" else emoji_no, "Download"]),
                                         callback_data=" ".join(["settings", "dl"]))
        button_link = InlineKeyboardButton(text=" ".join([emoji_yes if mode == "link" else emoji_no, "Links"]),
                                           callback_data=" ".join(["settings", "link"]))
        button_ask = InlineKeyboardButton(text=" ".join([emoji_yes if mode == "ask" else emoji_no, "Ask"]),
                                          callback_data=" ".join(["settings", "ask"]))
        button_flood = InlineKeyboardButton(text=" ".join([emoji_yes if flood == "yes" else emoji_no, "Flood"]),
                                            callback_data=" ".join(["settings", "flood"]))
        inline_keyboard = InlineKeyboardMarkup([[button_dl, button_link, button_ask], [button_flood]])
        return inline_keyboard

    def start_command_callback(self, bot, update):
        self.help_command_callback(bot, update, event_name="start")

    def help_command_callback(self, bot, update, event_name="help"):
        chat_id = update.message.chat_id
        chat_type = update.message.chat.type
        self.init_chat(chat_id, chat_type)
        reply_to_message_id = update.message.message_id
        flood = self.chat_storage[str(chat_id)]["settings"]["flood"]
        if chat_type != Chat.PRIVATE and flood == "no":
            self.rant_and_cleanup(bot, chat_id, self.RANT_TEXT_PUBLIC, reply_to_message_id=reply_to_message_id)
        else:
            bot.send_message(chat_id=chat_id, text=self.HELP_TEXT,
                             parse_mode='Markdown', disable_web_page_preview=True)
        self.log_and_botan_track(event_name, update.message)

    def settings_command_callback(self, bot, update):
        chat_id = update.message.chat_id
        chat_type = update.message.chat.type
        self.init_chat(chat_id, chat_type)
        settings_message = bot.send_message(chat_id=chat_id, parse_mode='Markdown',
                                            reply_markup=self.get_settings_inline_keyboard(chat_id), text=self.SETTINGS_TEXT)
        self.log_and_botan_track("settings_cmd")

    def inline_query_callback(self, bot, update):
        inline_query_id = update.inline_query.id
        text = update.inline_query.query
        results = []
        urls = self.prepare_urls(msg_or_text=text, get_direct_urls=True)
        for url in urls:
            for direct_url in urls[url].splitlines():  # TODO: fix non-mp3 and allow only sc/bc
                logger.debug(direct_url)
                results.append(
                    InlineQueryResultAudio(id=str(uuid4()), audio_url=direct_url, title="FAST_INLINE_DOWNLOAD"))
        try:
            bot.answer_inline_query(inline_query_id, results)
        except:
            pass
        self.log_and_botan_track("link_inline")

    def link_command_callback(self, bot, update, args=None):
        self.common_command_callback(bot, update, args, mode="link")

    def message_callback(self, bot, update):
        chat_id = update.message.chat_id
        chat_type = update.message.chat.type
        self.init_chat(chat_id, chat_type)
        mode = self.chat_storage[str(chat_id)]["settings"]["mode"]
        self.common_command_callback(bot, update, args="default", mode=mode)

    def common_command_callback(self, bot, update, args=None, mode="dl"):
        chat_id = update.message.chat_id
        chat_type = update.message.chat.type
        self.init_chat(chat_id, chat_type)
        reply_to_message_id = update.message.message_id
        if not args and mode != "ask":
            rant_text = self.RANT_TEXT_PRIVATE if chat_type == Chat.PRIVATE else self.RANT_TEXT_PUBLIC
            rant_text += "\nYou can simply send message with links (to download) OR command as `/{} <links>`.".format(
                mode)
            self.rant_and_cleanup(bot, chat_id, rant_text, reply_to_message_id=update.message.message_id)
            return
        # apologize and send TYPING: in private chats AND in public chats when it wasn't message (default)
        apologize = chat_type == Chat.PRIVATE or args != "default"
        if apologize:
            bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        urls = self.prepare_urls(msg_or_text=update.message,
                                 get_direct_urls=(mode == "link"))  # text=" ".join(args)
        logger.debug(urls)
        if not urls:
            logger.info("No supported URLs found")
            if apologize:
                bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id,
                                 text=self.NO_URLS_TEXT, parse_mode='Markdown')
        else:
            if mode == "dl":
                botan_event_name = "dl_msg" if args == "default" else "dl_cmd"
                wait_message = bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id,
                                                parse_mode='Markdown', text=self.md_italic(self.WAIT_TEXT))
                self.log_and_botan_track(botan_event_name, update.message)
                for url in urls:
                    self.download_url_and_send(bot, url, urls[url], chat_id=chat_id,
                                               reply_to_message_id=reply_to_message_id,
                                               wait_message_id=wait_message.message_id)
            elif mode == "link":
                botan_event_name = "link_msg" if args == "default" else "link_cmd"
                wait_message = bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id,
                                                parse_mode='Markdown', text=self.md_italic(self.WAIT_TEXT))

                link_text = self.get_link_text(urls)
                bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id,
                                 parse_mode='Markdown', disable_web_page_preview=True,
                                 text=link_text if link_text else self.NO_URLS_TEXT)
                bot.delete_message(chat_id=chat_id, message_id=wait_message.message_id)
                self.log_and_botan_track(botan_event_name, update.message)
            elif mode == "ask":
                if chat_type == Chat.PRIVATE or "http" in " ".join(urls.values()):
                    orig_msg_id = str(reply_to_message_id)
                    self.chat_storage[str(chat_id)][orig_msg_id] = {"message": update.message, "urls": urls}
                    question = "🎶 links found, what to do?"
                    button_dl = InlineKeyboardButton(text="✅ Download", callback_data=" ".join([orig_msg_id, "dl"]))
                    button_link = InlineKeyboardButton(text="❇️ Links",
                                                       callback_data=" ".join([orig_msg_id, "link"]))
                    button_cancel = InlineKeyboardButton(text="❎", callback_data=" ".join([orig_msg_id, "nodl"]))
                    inline_keyboard = InlineKeyboardMarkup([[button_dl, button_link, button_cancel]])
                    bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id,
                                     reply_markup=inline_keyboard, text=question)
                    self.log_and_botan_track("dl_msg_income")

    def button_query_callback(self, bot, update):
        chat_id = update.callback_query.message.chat_id
        btn_msg_id = update.callback_query.message.message_id
        orig_msg_id, action = update.callback_query.data.split()
        if orig_msg_id == "settings":
            if action == "dl":
                # update.callback_query.answer(text="Defaul set")
                self.chat_storage[str(chat_id)]["settings"]["mode"] = "dl"
                # self.log_and_botan_track(("settings_dl"), orig_msg)
            elif action == "link":
                self.chat_storage[str(chat_id)]["settings"]["mode"] = "link"
                # self.log_and_botan_track(("settings_link"), orig_msg)
            elif action == "ask":
                self.chat_storage[str(chat_id)]["settings"]["mode"] = "ask"
                # self.log_and_botan_track(("settings_dl"), orig_msg)
            elif action == "flood":
                flood = self.chat_storage[str(chat_id)]["settings"]["flood"]
                self.chat_storage[str(chat_id)]["settings"]["flood"] = "no" if flood == "yes" else "yes"
                # self.log_and_botan_track(("settings_flood"), orig_msg)
            self.chat_storage.sync()
            logger.debug(self.chat_storage)
            settings_message = update.callback_query.edit_message_reply_markup(parse_mode='Markdown',
                                                                               reply_markup=self.get_settings_inline_keyboard(
                                                                                   chat_id))
        elif orig_msg_id in self.chat_storage[str(chat_id)]:
            orig_msg = self.chat_storage[str(chat_id)][orig_msg_id]["message"]
            urls = self.chat_storage[str(chat_id)][orig_msg_id]["urls"]

            if action == "dl":
                update.callback_query.answer(text=self.WAIT_TEXT)
                wait_message = update.callback_query.edit_message_text(parse_mode='Markdown',
                                                                       text=self.md_italic(self.WAIT_TEXT))
                self.log_and_botan_track(("dl_msg"), orig_msg)
                for url in urls:
                    self.download_url_and_send(bot, url, urls[url], chat_id=chat_id,
                                               reply_to_message_id=orig_msg_id,
                                               wait_message_id=wait_message.message_id)
            elif action == "link":
                update.callback_query.answer(text=self.WAIT_TEXT)
                wait_message = update.callback_query.edit_message_text(parse_mode='Markdown',
                                                                       text=self.md_italic(self.WAIT_TEXT))
                urls = self.prepare_urls(urls.keys(), get_direct_urls=True)
                link_text = self.get_link_text(urls)
                bot.send_message(chat_id=chat_id, reply_to_message_id=orig_msg_id,
                                 parse_mode='Markdown', disable_web_page_preview=True,
                                 text=link_text if link_text else self.NO_URLS_TEXT)
                bot.delete_message(chat_id=chat_id, message_id=wait_message.message_id)
                self.log_and_botan_track(("link_msg"), orig_msg)
            elif action == "nodl":
                # update.callback_query.answer(text="Cancelled!", show_alert=True)
                bot.delete_message(chat_id=chat_id, message_id=btn_msg_id)
                self.log_and_botan_track(("nodl_msg"), orig_msg)
            self.chat_storage[str(chat_id)].pop(orig_msg_id)
            for msg_id in self.chat_storage[str(chat_id)]:
                timedelta = datetime.now() - self.chat_storage[str(chat_id)][msg_id]["message"].date
                if timedelta.days > 0:
                    self.chat_storage[str(chat_id)].pop(msg_id)
            self.chat_storage.sync()
        else:
            update.callback_query.answer(text="Sorry, very old message that I don't remember.")
            bot.delete_message(chat_id=chat_id, message_id=btn_msg_id)

    @staticmethod
    def youtube_dl_download_url(url, ydl_opts, queue=None):
        ydl = youtube_dl.YoutubeDL(ydl_opts)
        try:
            ydl.download([url])
        except Exception as exc:
            ydl_status = str(exc)
            # ydl_status = exc  #TODO: throw original fails
        else:
            ydl_status = 0
        if queue:
            queue.put(ydl_status)
        else:
            return ydl_status

    def youtube_dl_get_direct_urls(self, url):
        logger.debug("youtube_dl_get_direct_urls")
        try:
            ret_code, std_out, std_err = self.youtube_dl["--get-url", url].run()
        except ProcessExecutionError as exc:
            # TODO: case when one page has multiple videos some available some not
            if "returning it as such" in exc.stderr:
                raise URLDirectError
            elif "proxy server" in exc.stderr:
                raise URLCountryError
            else:
                raise exc
        if "yt_live_broadcast" in std_out:
            raise URLLiveError
        else:
            return std_out

    def prepare_urls(self, msg_or_text, get_direct_urls=False):
        if isinstance(msg_or_text, Message):
            urls = []
            for url_str in msg_or_text.parse_entities(types=["url"]).values():
                logger.debug("Entity URL Parsed: %s", url_str)
                if "://" not in url_str:
                    url_str = "http://" + url_str
                urls.append(URL(url_str))
        else:
            urls = find_all_links(msg_or_text, default_scheme="http")
        urls_dict = {}
        for url in urls:
            url_text = url.to_text(True)
            url_parts_num = len([part for part in url.path_parts if part])
            try:
                if (
                    # SoundCloud: tracks, sets and widget pages
                    (self.SITES["sc"] in url.host and (2 <= url_parts_num <= 3 or self.SITES["scapi"] in url_text)) or
                    # Bandcamp: tracks and albums
                    (self.SITES["bc"] in url.host and (2 <= url_parts_num <= 2)) or
                    # YouTube: videos and playlists
                    (self.SITES["yt"] in url.host and (
                        "youtu.be" in url.host or "watch" in url.path or "playlist" in url.path))
                ):
                    if get_direct_urls or self.SITES["yt"] in url.host:
                        urls_dict[url_text] = self.youtube_dl_get_direct_urls(url_text)
                    else:
                        urls_dict[url_text] = "http"
                elif not any((site in url.host for site in self.SITES.values())):
                    urls_dict[url_text] = self.youtube_dl_get_direct_urls(url_text)
            except ProcessExecutionError:
                logger.info("youtube-dl get url failed")
                # logger.exception("youtube-dl get url failed")
            except URLError as exc:
                urls_dict[url_text] = exc.status
        return urls_dict

    @run_async
    def download_url_and_send(self, bot, url, direct_urls, chat_id, reply_to_message_id=None,
                              wait_message_id=None):
        bot.send_chat_action(chat_id=chat_id, action=ChatAction.RECORD_AUDIO)
        download_dir = os.path.join(self.DL_DIR, str(uuid4()))
        shutil.rmtree(download_dir, ignore_errors=True)
        os.makedirs(download_dir)

        scdl_cmd = self.scdl[
            "-l", url,  # URL of track/playlist/user
            "-c",  # Continue if a music already exist
            "--path", download_dir,  # Download the music to a custom path
            "--onlymp3",  # Download only the mp3 file even if the track is Downloadable
            "--addtofile",  # Add the artist name to the filename if it isn't in the filename already
        ]
        bandcamp_dl_cmd = self.bandcamp_dl[
            "--base-dir", download_dir,  # Base location of which all files are downloaded
            "--template", "%{track} - %{artist} - %{title} [%{album}]",  # Output filename template
            "--overwrite",  # Overwrite tracks that already exist
            "--group",  # Use album/track Label as iTunes grouping
            "--embed-art",  # Embed album art (if available)
            "--no-slugify",  # Disable slugification of track, album, and artist names
            url  # URL of album/track
        ]
        # TODO: different ydl_opts for different sites
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': os.path.join(download_dir, '%(title)s.%(ext)s'),  # %(autonumber)s - %(title)s-%(id)s.%(ext)s
            'postprocessors': [
                {
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '128',
                },
                # {
                #     'key': 'EmbedThumbnail',
                # },
                # {
                #     'key': 'FFmpegMetadata',
                # },
            ],
        }

        status = 0
        if direct_urls == "direct":
            status = -3
        elif direct_urls == "country":
            status = -4
        elif direct_urls == "live":
            status = -5
        else:
            logger.info("Trying to download URL: %s", url)
            if self.SITES["sc"] in url and self.SITES["scapi"] not in url:
                logger.info("scdl starts...")
                try:
                    cmd_popen = scdl_cmd.popen(stdin=PIPE, stdout=PIPE, stderr=PIPE, universal_newlines=True)
                    try:
                        std_out, std_err = cmd_popen.communicate(timeout=self.DL_TIMEOUT)
                        if cmd_popen.returncode or "Error resolving url" in std_err:
                            text = "scdl process failed" + "\nstdout:\n" + std_out + "\nstderr:\n" + std_err
                            logger.error(text)
                            self.send_alert(bot, text, url)
                        else:
                            text = "scdl succeeded"
                            logger.info(text)
                            status = 1
                    except TimeoutExpired:
                        text = "Download took with scdl too long, dropped"
                        logger.warning(text)
                        cmd_popen.kill()
                        status = -1
                except Exception as exc:
                    text = "scdl start failed"
                    logger.exception(text)
                    self.send_alert(bot, text + "\n" + str(exc), url)
            elif self.SITES["bc"] in url:
                logger.info("bandcamp-dl starts...")
                try:
                    cmd_popen = bandcamp_dl_cmd.popen(stdin=PIPE, stdout=PIPE, stderr=PIPE, universal_newlines=True)
                    try:
                        std_out, std_err = cmd_popen.communicate(input="yes", timeout=self.DL_TIMEOUT)
                        if cmd_popen.returncode:
                            text = "bandcamp-dl process failed" + "\nstdout:\n" + std_out + "\nstderr:\n" + std_err
                            logger.error(text)
                            self.send_alert(bot, text, url)
                        else:
                            text = "bandcamp-dl succeeded"
                            logger.info(text)
                            status = 1
                    except TimeoutExpired:
                        text = "bandcamp-dl took too much time and dropped"
                        logger.warning(text)
                        cmd_popen.kill()
                        status = -1
                except Exception as exc:
                    text = "bandcamp-dl start failed"
                    logger.exception(text)
                    self.send_alert(bot, text + "\n" + str(exc), url)

        if status == 0:
            logger.info("youtube-dl starts...")
            queue = multiprocessing.Queue()
            ydl = multiprocessing.Process(target=self.youtube_dl_download_url, args=(url, ydl_opts, queue,))
            ydl.start()
            try:
                ydl_status = queue.get(block=True, timeout=self.DL_TIMEOUT)
                ydl.join()
                if ydl_status:
                    raise Exception(ydl_status)
                    # raise ydl_status
                text = "youtube-dl succeeded"
                logger.info(text)
                status = 1
            except Empty:
                ydl.join(1)
                if ydl.is_alive():
                    ydl.terminate()
                text = "youtube-dl took too much time and dropped"
                logger.warning(text)
                status = -1
            except Exception as exc:
                text = "youtube-dl failed"
                logger.exception(text)
                self.send_alert(bot, text + "\n" + str(exc), url)
                status = -2
            gc.collect()

        if status == -1:
            bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id,
                             text=self.DL_TIMEOUT_TEXT, parse_mode='Markdown')
        elif status == -2:
            bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id,
                             text=self.NO_AUDIO_TEXT, parse_mode='Markdown')
        elif status == -3:
            bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id,
                             text=self.DIRECT_RESTRICTION_TEXT, parse_mode='Markdown')
        elif status == -4:
            bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id,
                             text=self.REGION_RESTRICTION_TEXT, parse_mode='Markdown')
        elif status == -5:
            bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id,
                             text=self.LIVE_RESTRICTION_TEXT, parse_mode='Markdown')
        elif status == 1:
            file_list = []
            for d, dirs, files in os.walk(download_dir):
                for file in files:
                    file_list.append(os.path.join(d, file))
            for file in sorted(file_list):
                file_name = os.path.split(file)[-1]
                try:
                    file_parts = self.split_audio_file(file)
                except FileNotSupportedError as exc:
                    file_parts = []
                    if not (exc.file_format == "m3u"):
                        logger.warning("Unsupported file format: %s", file)
                        bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id,
                                         text="*Sorry*, downloaded file `{}` is in format I could not yet convert or send".format(
                                             file_name),
                                         parse_mode='Markdown')
                except FileTooLargeError as exc:
                    file_parts = []
                    logger.warning("Large file for convert: %s", file)
                    bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id,
                                     text="*Sorry*, downloaded file `{}` is `{}` MB and it is larger than I could convert (`{} MB`)".format(
                                         file_name, exc.file_size // 1000000, self.MAX_CONVERT_FILE_SIZE // 1000000),
                                     parse_mode='Markdown')
                except FileConvertedPartiallyError as exc:
                    file_parts = exc.file_parts
                    logger.exception("Failed pydub convert: %s", file)
                    self.send_alert(bot, "Memory Error", file)
                    bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id,
                                     text="*Sorry*, not enough memory to convert file `{}`, you may try again later..".format(
                                         file_name),
                                     parse_mode='Markdown')

                try:
                    caption = "Downloaded from {} with @{}\n".format(URL(url).host, self.bot_username)
                    flood = self.chat_storage[str(chat_id)]["settings"]["flood"]
                    sent_audio_ids = self.send_audio_file_parts(bot, chat_id, file_parts,
                                                                reply_to_message_id if flood == "yes" else None,
                                                                caption if flood == "yes" else None)
                except FileSentPartiallyError as exc:
                    sent_audio_ids = exc.sent_audio_ids
                    bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id,
                                     text="*Sorry*, could not send file `{}` or some of it's parts, you may try again later..".format(
                                         file_name),
                                     parse_mode='Markdown')
                    logger.warning("Some parts"
                                   " of %s failed to send", file)

        shutil.rmtree(download_dir, ignore_errors=True)
        if wait_message_id:  # TODO: delete only once or append errors
            try:
                bot.delete_message(chat_id=chat_id, message_id=wait_message_id)
            except:
                pass

        # downloader = URLopener()
        #     try:
        #         file_name, headers = downloader.retrieve(url.to_text(full_quote=True))
        #         patoolib.extract_archive(file_name, outdir=DL_DIR)
        #         os.remove(file_name)
        #     except Exception as exc:
        #         return str(exc)
        #     #     return str(sys.exc_info()[0:1])

    def split_audio_file(self, file=""):
        file_root, file_ext = os.path.splitext(file)
        file_format = file_ext.replace(".", "")
        if not (file_format == "mp3" or file_format == "m4a" or file_format == "mp4"):
            raise FileNotSupportedError(file_format)
        file_size = os.path.getsize(file)
        if file_size > self.MAX_CONVERT_FILE_SIZE:
            raise FileTooLargeError(file_size)
        if file_size <= self.MAX_TG_FILE_SIZE:
            return [file]
        else:
            file_parts = []
            logger.info("Splitting: %s", file)
            try:
                id3 = mutagen.id3.ID3(file, translate=False)
            except:
                id3 = None
            parts_number = file_size // self.MAX_TG_FILE_SIZE + 1
            try:
                sound = AudioSegment.from_file(file, file_format)
                part_size = len(sound) / parts_number
                for i in range(parts_number):
                    file_part = file.replace(file_ext, ".part" + str(i + 1) + file_ext)
                    part = sound[part_size * i:part_size * (i + 1)]
                    part.export(file_part, format="mp3")
                    del part
                    gc.collect()
                    if id3:
                        try:
                            id3.save(file_part, v1=2, v2_version=4)
                        except:
                            pass
                    file_parts.append(file_part)
                # https://github.com/jiaaro/pydub/issues/135
                # https://github.com/jiaaro/pydub/issues/89#issuecomment-75245610
                del sound
                gc.collect()
            except (OSError, MemoryError) as exc:
                gc.collect()
                raise FileConvertedPartiallyError(file_parts)
            return file_parts

    def send_audio_file_parts(self, bot, chat_id, file_parts, reply_to_message_id=None, caption=None):
        sent_audio_ids = []
        for index, file in enumerate(file_parts):
            file_name = os.path.split(file)[-1]
            logger.info("Sending: %s...", file)
            bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_AUDIO)
            # file = translit(file, 'ru', reversed=True)
            caption_ = " ".join(["Part", str(index + 1), "of", str(len(file_parts))]) if len(file_parts) > 1 else ""
            if caption:
                caption_ = caption + caption_
            for i in range(3):
                try:
                    audio_msg = bot.send_audio(chat_id=chat_id, reply_to_message_id=reply_to_message_id,
                                               audio=open(file, 'rb'), caption=caption_)
                    sent_audio_ids.append(audio_msg.audio.file_id)
                    logger.info("Sending succeeded: %s", file)
                    break
                except TelegramError as exc:
                    logger.exception("Sending failed because of TelegramError: %s", file)
                    if i == 2:
                        self.send_alert(bot, "TelegramError:\n" + str(exc), file_name)
        if len(sent_audio_ids) != len(file_parts):
            raise FileSentPartiallyError(sent_audio_ids)
        return sent_audio_ids
