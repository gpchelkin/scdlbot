# -*- coding: utf-8 -*-

import configparser
import logging
import os
# import shelve
import shutil
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
from telegram.error import (TelegramError, Unauthorized, BadRequest,
                            TimedOut, ChatMigrated, NetworkError)
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, InlineQueryHandler, CallbackQueryHandler

logger = logging.getLogger(__name__)


class SCDLBot:
    MAX_TG_FILE_SIZE = 45000000
    SITES = {
        "sc": "soundcloud",
        "bc": "bandcamp",
        "yt": "youtu",
    }

    def __init__(self, tg_bot_token, botan_token, bin_path,
                 sc_auth_token, store_chat_id, no_clutter_chat_ids, dl_dir):
        self.WAIT_TEXT = self.get_response_text('wait.txt')
        self.NO_AUDIO_TEXT = self.get_response_text('no_audio.txt')
        self.HELP_TEXT = self.get_response_text('help.tg.md')
        self.NO_CLUTTER_CHAT_IDS = no_clutter_chat_ids if no_clutter_chat_ids else []
        self.STORE_CHAT_ID = store_chat_id
        self.DL_DIR = dl_dir
        self.scdl = local[os.path.join(bin_path, 'scdl')]
        # self.bcdl = local[os.path.join(bin_path, 'bandcamp-dl')]
        self.youtube_dl = local[os.path.join(bin_path, 'youtube-dl')]
        self.tg_bot_token = tg_bot_token
        self.botan = Botan(botan_token) if botan_token else None
        self.msg_store = {}  # TODO prune it

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

        self.updater = Updater(token=self.tg_bot_token)
        dispatcher = self.updater.dispatcher

        start_command_handler = CommandHandler('start', self.start_command_callback)
        dispatcher.add_handler(start_command_handler)
        help_command_handler = CommandHandler('help', self.help_command_callback)
        dispatcher.add_handler(help_command_handler)
        clutter_command_handler = CommandHandler('clutter', self.clutter_command_callback)
        dispatcher.add_handler(clutter_command_handler)

        dl_command_handler = CommandHandler('dl', self.dl_command_callback, filters=~ Filters.forwarded, pass_args=True)
        dispatcher.add_handler(dl_command_handler)

        link_command_handler = CommandHandler('link', self.link_command_callback, filters=~ Filters.forwarded,
                                              pass_args=True)
        dispatcher.add_handler(link_command_handler)

        message_with_links_handler = MessageHandler(Filters.text & (Filters.entity(MessageEntity.URL) |
                                                                    Filters.entity(MessageEntity.TEXT_LINK)),
                                                    self.message_callback)
        dispatcher.add_handler(message_with_links_handler)

        callback_query_handler = CallbackQueryHandler(self.callback_query_callback)
        dispatcher.add_handler(callback_query_handler)

        inline_query_handler = InlineQueryHandler(self.inline_query_callback)
        dispatcher.add_handler(inline_query_handler)

        unknown_handler = MessageHandler(Filters.command, self.unknown_command_callback)
        dispatcher.add_handler(unknown_handler)

        dispatcher.add_error_handler(self.error_callback)

    def run(self, use_webhook=False, app_url=None, app_port=None, cert_file=None):
        if use_webhook:
            url_path = self.tg_bot_token.replace(":", "")
            self.updater.start_webhook(listen="0.0.0.0",
                                       port=app_port,
                                       url_path=url_path)
            self.updater.bot.set_webhook(url=urljoin(app_url, url_path))
            # ... certificate=open(cert_file, 'rb')
        else:
            self.updater.start_polling()
        self.updater.idle()

    @staticmethod
    def get_response_text(file_name):
        # https://stackoverflow.com/a/20885799/2490759
        path = '/'.join(('texts', file_name))
        return pkg_resources.resource_string(__name__, path).decode("UTF-8")

    @staticmethod
    def md_italic(text):
        return "".join(["_", text, "_"])

    def unknown_command_callback(self, bot, update):
        pass
        # bot.send_message(chat_id=update.message.chat_id,
        #                  text="Unknown command")

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

    def start_command_callback(self, bot, update):
        self.help_command_callback(bot, update, event_name="start")

    def help_command_callback(self, bot, update, event_name="help"):
        logger.debug(event_name)
        self.botan.track(update.message, event_name) if self.botan else None
        bot.send_message(chat_id=update.message.chat_id, text=self.HELP_TEXT,
                         parse_mode='Markdown', disable_web_page_preview=True)

    def clutter_command_callback(self, bot, update):
        event_name = "clutter"
        logger.debug(event_name)
        self.botan.track(update.message, event_name) if self.botan else None
        if update.message.chat_id in self.NO_CLUTTER_CHAT_IDS:
            self.NO_CLUTTER_CHAT_IDS.remove(update.message.chat_id)
            bot.send_message(chat_id=update.message.chat_id, text="Chat will be cluttered with replies",
                             parse_mode='Markdown', disable_web_page_preview=True)
        else:
            self.NO_CLUTTER_CHAT_IDS.append(update.message.chat_id)
            bot.send_message(chat_id=update.message.chat_id, text="Chat will not be cluttered with replies",
                             parse_mode='Markdown', disable_web_page_preview=True)

    def inline_query_callback(self, bot, update):
        urls = self.prepare_urls(update.inline_query.query)
        if urls:
            event_name = "dl_inline"
            logger.debug(event_name)
            self.download_and_send(bot, urls.keys(), self.STORE_CHAT_ID, inline_query_id=update.inline_query.id)

    def link_command_callback(self, bot, update, args=None):
        chat_id = update.message.chat_id
        urls = self.prepare_urls(" ".join(args), get_direct_urls=True)
        if urls:
            event_name = "link"
            logger.debug(event_name)
            self.botan.track(update.message, event_name) if self.botan else None
            link_text = ""
            for link in urls.values():
                link_text += "[Download link](" + link + ")"
            link_message = bot.send_message(chat_id=chat_id, reply_to_message_id=update.message.message_id,
                                            parse_mode='Markdown', text="[test](http://pchelk.in)")

    def dl_command_callback(self, bot, update, args=None):
        chat_id = update.message.chat_id
        if not args:
            rant = " in my PM, don't clutter the group chat, okay?" if update.message.chat.type != "private" else ""
            bot.send_message(chat_id=chat_id, reply_to_message_id=update.message.message_id,
                             text="Learn how to use me in /help" + rant)
            return
        urls = self.prepare_urls(" ".join(args))
        if urls:
            event_name = "dl_cmd"
            logger.debug(event_name)
            self.botan.track(update.message, event_name) if self.botan else None
            wait_message = bot.send_message(chat_id=chat_id, reply_to_message_id=update.message.message_id,
                                            parse_mode='Markdown', text=self.md_italic(self.WAIT_TEXT))
            self.download_and_send(bot, urls.keys(), chat_id=chat_id, reply_to_message_id=update.message.message_id,
                                   wait_message_id=wait_message.message_id)

    def callback_query_callback(self, bot, update):
        action, orig_msg_id = update.callback_query.data.split("_")
        urls = self.prepare_urls(self.msg_store[orig_msg_id].text)
        if urls:
            event_name = "_".join([action, "msg"])
            logger.debug(event_name)
            self.botan.track(self.msg_store[orig_msg_id], event_name) if self.botan else None
            self.msg_store.pop(orig_msg_id)
            chat_id = update.callback_query.message.chat_id

            if action == "dl":
                update.callback_query.answer(text=self.WAIT_TEXT)
                edited_msg = update.callback_query.edit_message_text(parse_mode='Markdown',
                                                                     text=self.md_italic(self.WAIT_TEXT))
                self.download_and_send(bot, urls.keys(), chat_id=chat_id,
                                       wait_message_id=edited_msg.message_id)
            elif action == "nodl" or action == "destroy":
                # update.callback_query.answer(text="Cancelled!", show_alert=True)
                bot.delete_message(chat_id=chat_id, message_id=update.callback_query.message.message_id)

    def message_callback(self, bot, update):
        urls = self.prepare_urls(update.message.text)
        if urls:
            chat_id = update.message.chat_id
            reply_to_message_id = update.message.message_id

            if update.message.chat.type == "private" or self.prepare_urls(urls):
                event_name = "_".join(["dl", "msg"])
                logger.debug(event_name)
                if update.message.chat.type == "private":
                    self.botan.track(update.message, event_name) if self.botan else None
                    wait_message = bot.send_message(chat_id=chat_id, reply_to_message_id=update.message.message_id,
                                                    parse_mode='Markdown', text=self.md_italic(self.WAIT_TEXT))
                    self.download_and_send(bot, urls.keys(), chat_id=chat_id, reply_to_message_id=reply_to_message_id,
                                           wait_message_id=wait_message.message_id)
                else:
                    orig_msg_id = str(reply_to_message_id)
                    self.msg_store[orig_msg_id] = update.message
                    button_download = InlineKeyboardButton(text="YES", callback_data="_".join(["dl", orig_msg_id]))
                    button_cancel = InlineKeyboardButton(text="NO", callback_data="_".join(["nodl", orig_msg_id]))
                    inline_keyboard = InlineKeyboardMarkup([[button_download, button_cancel]])
                    bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id,
                                     reply_markup=inline_keyboard, text="Download 🎶?")

    def youtube_dl_get_direct_urls(self, url):
        try:
            direct_urls = self.youtube_dl("--get-url", url)
        except:
            direct_urls = None
        return direct_urls

    def prepare_urls(self, text, get_direct_urls=False):
        urls = find_all_links(text, default_scheme="http")
        urls_dict = {}
        for url in urls:
            url_parts_num = len([part for part in url.path_parts if part])
            if (
                    # SoundCloud: tracks, sets and widget pages
                    (self.SITES["sc"] in url.host and (2 <= url_parts_num <= 3 or "/player/" in url.path)) or
                    # Bandcamp: tracks and albums
                    (self.SITES["bc"] in url.host and (2 <= url_parts_num <= 2)) or
                    # YouTube: videos and playlists
                    (self.SITES["yt"] in url.host and ("youtu.be" in url.host or "watch" in url.path or "playlist" in url.path))
            ):
                if get_direct_urls:
                    direct_urls = self.youtube_dl_get_direct_urls(url.to_text(True))
                    if direct_urls:
                        urls_dict[url.to_text(True)] = direct_urls
                else:
                    urls_dict[url.to_text(True)] = ""
            elif not any((site in url.host for site in self.SITES.values())):
                direct_urls = self.youtube_dl_get_direct_urls(url.to_text(True))
                if direct_urls:
                    urls_dict[url.to_text(True)] = direct_urls
        return urls_dict

    # @run_async
    def download_audio_url(self, url, download_dir):
        if self.SITES["sc"] in url:
            self.scdl(
                "-l", url,  # URL of track/playlist/user
                "-c",  # Continue if a music already exist
                "--path", download_dir,  # Download the music to a custom path
                "--onlymp3",  # Download only the mp3 file even if the track is Downloadable
                "--addtofile",  # Add the artist name to the filename if it isn't in the filename already
            )
        else:
            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': '%(autonumber)s - %(title)s.%(ext)s',  # %(title)s-%(id)s.%(ext)s
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


            prev_cwd = os.getcwd()
            os.chdir(download_dir)
            try:
                with youtube_dl.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
            except Exception as e:
                logger.debug(url, e)  # TODO
            os.chdir(prev_cwd)

        downloader = URLopener()
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


        # self.bcdl(
        #     "--base-dir=" + download_dir,  # Base location of which all files are downloaded
        #     "--template=" + self.BANDCAMP_TEMPLATE,  # Output filename template
        #     "--overwrite",  # Overwrite tracks that already exist
        #     "--group",  # Use album/track Label as iTunes grouping
        #     "--embed-art",  # Embed album art (if available)
        #     "--no-slugify",  # Disable slugification of track, album, and artist names
        #     url.to_text(full_quote=True)  # URL of album/track
        # )

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
                for i in range(3):
                    try:
                        audio_msg = bot.send_audio(chat_id=chat_id, reply_to_message_id=reply_to_message_id,
                                                   audio=open(file, 'rb'), caption=caption)
                        sent_audio_ids.append(audio_msg.audio.file_id)
                        break
                    except TelegramError as exc:
                        logger.debug('Caught TelegramError: {}'.format(exc))
                        pass
        return sent_audio_ids

    def download_and_send(self, bot, urls, chat_id, reply_to_message_id=None,
                          wait_message_id=None, inline_query_id=None):
        if chat_id in self.NO_CLUTTER_CHAT_IDS:
            reply_to_message_id = None

        sent_audio_ids = []
        for url in urls:
            download_dir = os.path.join(self.DL_DIR, str(uuid4()))
            shutil.rmtree(download_dir, ignore_errors=True)
            os.makedirs(download_dir)

            bot.send_chat_action(chat_id=chat_id, action=ChatAction.RECORD_AUDIO)
            self.download_audio_url(url, download_dir)
            # if status != "success":
            #     bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id,
            #                      parse_mode='Markdown', text="`" + status + "`")

            file_list = []
            for d, dirs, files in os.walk(download_dir):
                for file in files:
                    file_list.append(os.path.join(d, file))
            file_list = sorted(file_list)

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
