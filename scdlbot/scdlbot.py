# -*- coding: utf-8 -*-

import configparser
import logging
import os
# import shelve
import shutil
from logging.handlers import SysLogHandler
# import time
from urllib.parse import urljoin
from urllib.request import URLopener
from uuid import uuid4

import mutagen.id3
import pkg_resources
import youtube_dl
from boltons.urlutils import find_all_links
from plumbum import local
from pydub import AudioSegment
from telegram import MessageEntity, InlineQueryResultCachedAudio, ChatAction, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.contrib.botan import Botan
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, InlineQueryHandler, CallbackQueryHandler

# http://help.papertrailapp.com/kb/configuration/configuring-centralized-logging-from-python-apps/
console_handler = logging.StreamHandler()
handlers = [console_handler]

SYSLOG_ADDRESS = os.getenv('SYSLOG_ADDRESS', '')
if SYSLOG_ADDRESS:
    syslog_hostname, syslog_udp_port = SYSLOG_ADDRESS.split(":")
    syslog_udp_port = int(syslog_udp_port)
    syslog_handler = SysLogHandler(address=(syslog_hostname, syslog_udp_port))
    handlers.append(syslog_handler)

logging.basicConfig(format='%(asctime)s {} %(name)s: %(message)s'.format(os.getenv("HOSTNAME", "unknown_host")),
                    datefmt='%b %d %H:%M:%S',
                    level=logging.DEBUG, handlers=handlers)
logger = logging.getLogger(__name__)


class SCDLBot:
    MAX_TG_FILE_SIZE = 45000000
    BANDCAMP_TEMPLATE = "%{track} - %{artist} - %{title} [%{album}]"

    patterns = {
        "soundcloud": "soundcloud.com",
        "bandcamp": "bandcamp.com",
        "youtube": "youtube.com",
        "youtu.be": "youtu.be",
        "mixcloud": "mixcloud.com"
    }

    def __init__(self, tg_bot_token, botan_token, use_webhook,
                 app_url, app_port, bin_path,
                 sc_auth_token, store_chat_id, no_clutter_chat_ids, dl_dir):
        self.WAIT_TEXT = self.get_response_text('wait.txt')
        self.NO_AUDIO_TEXT = self.get_response_text('no_audio.txt')
        self.HELP_TEXT = self.get_response_text('help.tg.md')
        self.NO_CLUTTER_CHAT_IDS = no_clutter_chat_ids if no_clutter_chat_ids else []
        self.STORE_CHAT_ID = store_chat_id
        self.DL_DIR = dl_dir
        self.scdl = local[os.path.join(bin_path, 'scdl')]
        self.bcdl = local[os.path.join(bin_path, 'bandcamp-dl')]
        self.botan = Botan(botan_token) if botan_token else None
        self.msg_store = {}  # TODO shelve

        config = configparser.ConfigParser()
        config['scdl'] = {
            'auth_token': sc_auth_token,
            'path': self.DL_DIR,
        }
        config_dir = os.path.join(os.path.expanduser('~'), '.config', 'scdl')
        config_path = os.path.join(config_dir, 'scdl.cfg')
        os.makedirs(config_dir, exist_ok=True)
        with open(config_path, 'w') as config_file:
            config.write(config_file)

        updater = Updater(token=tg_bot_token)
        dispatcher = updater.dispatcher

        start_command_handler = CommandHandler('start', self.start_command_callback)
        dispatcher.add_handler(start_command_handler)
        help_command_handler = CommandHandler('help', self.help_command_callback)
        dispatcher.add_handler(help_command_handler)

        dl_command_handler = CommandHandler('dl', self.dl_command_callback, pass_args=True)
        dispatcher.add_handler(dl_command_handler)

        message_with_links_handler = MessageHandler(Filters.text & (Filters.entity(MessageEntity.URL) |
                                                                    Filters.entity(MessageEntity.TEXT_LINK)),
                                                    self.message_callback)
        dispatcher.add_handler(message_with_links_handler)

        callback_query_handler = CallbackQueryHandler(self.callback_query_callback)
        dispatcher.add_handler(callback_query_handler)

        inline_query_handler = InlineQueryHandler(self.inline_query_callback)
        dispatcher.add_handler(inline_query_handler)

        if use_webhook:
            url_path = tg_bot_token.replace(":", "")
            updater.start_webhook(listen="0.0.0.0",
                                  port=app_port,
                                  url_path=url_path)
            updater.bot.set_webhook(urljoin(app_url, url_path))
            updater.idle()
        else:
            updater.start_polling()

    @staticmethod
    def get_response_text(file_name):
        path = '/'.join(('texts', file_name))
        return pkg_resources.resource_string(__name__, path).decode("UTF-8")

    def start_command_callback(self, bot, update):
        self.help_command_callback(bot, update, event_name="start")

    def help_command_callback(self, bot, update, event_name="help"):
        logger.debug(event_name)
        self.botan.track(update.message, event_name) if self.botan else None
        bot.send_message(chat_id=update.message.chat_id, text=self.HELP_TEXT,
                         parse_mode='Markdown', disable_web_page_preview=True)

    def inline_query_callback(self, bot, update):
        event_name = "dl_inline"
        logger.debug(event_name)
        urls = find_all_links(update.inline_query.query, default_scheme="http")
        self.download_and_send(bot, urls, self.STORE_CHAT_ID, inline_query_id=update.inline_query.id)

    def dl_command_callback(self, bot, update, args=None):
        event_name = "dl_cmd"
        logger.debug(event_name)
        self.botan.track(update.message, event_name) if self.botan else None
        chat_id = update.message.chat_id

        if not args:
            rant = " in my PM, don't clutter the group chat, okay?" if update.message.chat.type != "private" else ""
            bot.send_message(chat_id=chat_id, reply_to_message_id=update.message.message_id,
                             text="Learn how to use me in /help" + rant)
            return

        urls = find_all_links(" ".join(args), default_scheme="http")
        wait_message = bot.send_message(chat_id=chat_id, reply_to_message_id=update.message.message_id,
                                        parse_mode='Markdown', text=self.md_italic(self.WAIT_TEXT))
        self.download_and_send(bot, urls, chat_id=chat_id, reply_to_message_id=update.message.message_id,
                               wait_message_id=wait_message.message_id)

    def test_urls(self, urls):  # TODO make it better
        str_urls = " ".join([url.to_text() for url in urls])
        return any((pattern in str_urls for pattern in self.patterns.values()))

    @staticmethod
    def md_italic(text):
        return "".join(["_", text, "_"])

    def callback_query_callback(self, bot, update):
        action, orig_msg_id = update.callback_query.data.split("_")
        event_name = "_".join([action, "msg"])
        logger.debug(event_name)
        self.botan.track(self.msg_store[orig_msg_id], event_name) if self.botan else None
        urls = find_all_links(self.msg_store[orig_msg_id].text, default_scheme="http")
        chat_id = update.callback_query.message.chat_id

        if action == "dl":
            update.callback_query.answer(text=self.WAIT_TEXT)
            update.callback_query.edit_message_text(parse_mode='Markdown', text=self.md_italic(self.WAIT_TEXT))
        elif action == "nodl" or action == "destroy":
            # if action == "destroy":
            #     update.callback_query.answer(show_alert=True, text="Destroyed!")
            bot.delete_message(chat_id=chat_id, message_id=update.callback_query.message.message_id)
            return

        self.msg_store.pop(orig_msg_id)
        self.download_and_send(bot, urls, chat_id=chat_id,
                               wait_message_id=update.callback_query.message.message_id)

    def message_callback(self, bot, update):
        urls = find_all_links(update.message.text, default_scheme="http")
        chat_id = update.message.chat_id
        reply_to_message_id = update.message.message_id

        if update.message.chat.type == "private" or self.test_urls(urls):
            event_name = "_".join(["dl", "msg"])
            logger.debug(event_name)
            if update.message.chat.type == "private":
                self.botan.track(update.message, event_name) if self.botan else None
                self.download_and_send(bot, urls, chat_id=chat_id, reply_to_message_id=reply_to_message_id)
            elif self.test_urls(urls):
                orig_msg_id = str(reply_to_message_id)
                self.msg_store[orig_msg_id] = update.message
                button_download = InlineKeyboardButton(text="YES", callback_data="_".join(["dl", orig_msg_id]))
                button_cancel = InlineKeyboardButton(text="NO", callback_data="_".join(["nodl", orig_msg_id]))
                inline_keyboard = InlineKeyboardMarkup([[button_download, button_cancel]])
                bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id,
                                 reply_markup=inline_keyboard, text="Download 🎶?")

    # @run_async
    def download_audio_urls(self, url, download_dir):
        downloader = URLopener()
        url_parts_len = len([part for part in url.path_parts if part])
        if self.patterns["soundcloud"] in url.host:
            if 2 <= url_parts_len <= 3:
                self.scdl(
                    "-l", url.to_text(full_quote=True),  # URL of track/playlist/user
                    "-c",  # Continue if a music already exist
                    "--path", download_dir,  # Download the music to a custom path
                    "--onlymp3",  # Download only the mp3 file even if the track is Downloadable
                    "--addtofile",  # Add the artist name to the filename if it isn't in the filename already
                )
        elif (self.patterns["bandcamp"] in url.host) or \
            ("/track/" in url.path) or ("/album/" in url.path):  # TODO try/except/log
            if 2 <= url_parts_len <= 2:
                self.bcdl(
                    "--base-dir=" + download_dir,  # Base location of which all files are downloaded
                    "--template=" + self.BANDCAMP_TEMPLATE,  # Output filename template
                    "--overwrite",  # Overwrite tracks that already exist
                    "--group",  # Use album/track Label as iTunes grouping
                    "--embed-art",  # Embed album art (if available)
                    "--no-slugify",  # Disable slugification of track, album, and artist names
                    url.to_text(full_quote=True)  # URL of album/track
                )
        elif (self.patterns["youtube"] in url.host and ("watch" in url.path or "playlist" in url.path)) or \
            (self.patterns["youtu.be"] in url.host) or \
            (self.patterns["mixcloud"] in url.host and 2 <= url_parts_len <= 2):
            ydl_opts = {
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '128',
                }],
            }
            prev_cwd = os.getcwd()
            os.chdir(download_dir)
            with youtube_dl.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url.to_text(full_quote=True)])
            os.chdir(prev_cwd)
            # else:
            #     try:
            #         file_name, headers = downloader.retrieve(url.to_text(full_quote=True))
            #         patoolib.extract_archive(file_name, outdir=DL_DIR)
            #         os.remove(file_name)
            #     except Exception as exc:
            #         return str(exc)
            #     #     return str(sys.exc_info()[0:1])
            #
            # return "success"

    # @run_async
    def split_and_send_audio_file(self, bot, chat_id, reply_to_message_id=None, file=""):
        sent_audio_ids = []
        file_root, file_ext = os.path.splitext(file)
        file_format = file_ext.replace(".", "")
        if file_format == "mp3" or file_format == "m4a" or file_format == "mp4":
            file_parts = []
            file_size = os.path.getsize(file)
            parts_number = 1
            if file_size > self.MAX_TG_FILE_SIZE:
                id3 = mutagen.id3.ID3(file, translate=False)
                parts_number = file_size // self.MAX_TG_FILE_SIZE + 1
                sound = AudioSegment.from_file(file, file_format)
                part_size = len(sound) / parts_number
                for i in range(parts_number):
                    file_part = file.replace(file_ext, ".part" + str(i + 1) + file_ext)
                    part = sound[part_size * i:part_size * (i + 1)]
                    part.export(file_part, format="mp3")
                    id3.save(file_part, v1=2, v2_version=4)
                    file_parts.append(file_part)
            else:
                file_parts.append(file)
            for index, file in enumerate(file_parts):
                bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_AUDIO)
                # file = translit(file, 'ru', reversed=True)
                # TODO add site hashtag
                caption = None
                if file_size > self.MAX_TG_FILE_SIZE:
                    caption = " ".join(["Part", str(index + 1), "of", str(parts_number)])
                audio_msg = bot.send_audio(chat_id=chat_id, reply_to_message_id=reply_to_message_id,
                                           audio=open(file, 'rb'), caption=caption)
                sent_audio_ids.append(audio_msg.audio.file_id)
        return sent_audio_ids

    def download_and_send(self, bot, urls, chat_id, reply_to_message_id=None,
                          wait_message_id=None, inline_query_id=None):
        if chat_id in self.NO_CLUTTER_CHAT_IDS:
            reply_to_message_id = None

        download_dir = os.path.join(self.DL_DIR, str(uuid4()))
        shutil.rmtree(download_dir, ignore_errors=True)
        os.makedirs(download_dir)

        for url in urls:
            bot.send_chat_action(chat_id=chat_id, action=ChatAction.RECORD_AUDIO)
            self.download_audio_urls(url, download_dir)
            # if status != "success":
            #     bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id,
            #                      parse_mode='Markdown', text="`" + status + "`")

        file_list = []
        for d, dirs, files in os.walk(download_dir):
            for file in files:
                file_list.append(os.path.join(d, file))
        file_list = sorted(file_list)

        sent_audio_ids = []
        for file in file_list:
            sent_audio_ids.extend(self.split_and_send_audio_file(bot, chat_id, reply_to_message_id, file))

        shutil.rmtree(download_dir, ignore_errors=True)
        if wait_message_id:
            bot.delete_message(chat_id=chat_id, message_id=wait_message_id)

        if not sent_audio_ids:
            bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id,
                             parse_mode='Markdown', text=self.md_italic(self.NO_AUDIO_TEXT))
            return

        if inline_query_id:
            results = []
            for audio_id in sent_audio_ids:
                if audio_id:
                    results.append(InlineQueryResultCachedAudio(id=str(uuid4()), audio_file_id=audio_id))
            bot.answer_inline_query(inline_query_id, results)
