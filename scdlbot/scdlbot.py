# -*- coding: utf-8 -*-

"""Main module."""

import configparser
import gc
import json
import logging
import multiprocessing
import os
# import shelve
import shutil
import signal
from subprocess import PIPE, TimeoutExpired
# import time
from urllib.parse import urljoin
from uuid import uuid4

import mutagen.id3
import pkg_resources
import youtube_dl
from boltons.urlutils import find_all_links, URL
from botanio import botan
from plumbum import local
from pydub import AudioSegment
from pyshorteners import Shortener
from telegram import MessageEntity, ChatAction, InlineKeyboardMarkup, InlineKeyboardButton, InlineQueryResultAudio
from telegram.error import (TelegramError, Unauthorized, BadRequest,
                            TimedOut, ChatMigrated, NetworkError)
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, InlineQueryHandler, CallbackQueryHandler
from telegram.ext.dispatcher import run_async

logger = logging.getLogger(__name__)


class SCDLBot:
    MAX_TG_FILE_SIZE = 45000000
    SITES = {
        "sc": "soundcloud",
        "scapi": "api.soundcloud",
        "bc": "bandcamp",
        "yt": "youtu",
    }

    def __init__(self, tg_bot_token, botan_token, google_shortener_api_key, bin_path,
                 sc_auth_token, store_chat_id, no_clutter_chat_ids, alert_chat_ids, dl_dir, dl_timeout, max_convert_file_size):
        self.DL_TIMEOUT = dl_timeout
        self.MAX_CONVERT_FILE_SIZE = max_convert_file_size
        self.TIMEOUT_TEXT = self.get_response_text('dl_timeout.txt').format(self.DL_TIMEOUT // 60)
        self.WAIT_TEXT = self.get_response_text('wait.txt')
        self.NO_AUDIO_TEXT = self.get_response_text('no_audio.txt')
        self.HELP_TEXT = self.get_response_text('help.tg.md')
        self.NO_CLUTTER_CHAT_IDS = set(no_clutter_chat_ids) if no_clutter_chat_ids else set()
        self.ALERT_CHAT_IDS = set(alert_chat_ids) if alert_chat_ids else set()
        self.STORE_CHAT_ID = store_chat_id
        self.DL_DIR = dl_dir
        self.scdl = local[os.path.join(bin_path, 'scdl')]
        self.bandcamp_dl = local[os.path.join(bin_path, 'bandcamp-dl')]
        self.youtube_dl = local[os.path.join(bin_path, 'youtube-dl')]
        self.tg_bot_token = tg_bot_token
        self.botan_token = botan_token if botan_token else None
        self.shortener = Shortener('Google', api_key=google_shortener_api_key) if google_shortener_api_key else None
        self.msg_store = {}  # TODO prune it
        self.rant_msg_ids = {}

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

        message_callback_query_handler = CallbackQueryHandler(self.message_callback_query_callback)
        dispatcher.add_handler(message_callback_query_handler)

        inline_query_handler = InlineQueryHandler(self.inline_query_callback)
        dispatcher.add_handler(inline_query_handler)

        unknown_handler = MessageHandler(Filters.command, self.unknown_command_callback)
        dispatcher.add_handler(unknown_handler)

        dispatcher.add_error_handler(self.error_callback)

        self.bot_username = self.updater.bot.get_me().username
        self.RANT_TEXT = "[PRESS HERE TO READ HELP IN PM](t.me/" + self.bot_username + "?start=1)"

    def run(self, use_webhook=False, app_url=None, webhook_port=None, cert_file=None):
        if use_webhook:
            url_path = self.tg_bot_token.replace(":", "")
            self.updater.start_webhook(listen="0.0.0.0",
                                       port=webhook_port,
                                       url_path=url_path)
            if cert_file:
                self.updater.bot.set_webhook(url=urljoin(app_url, url_path), certificate=open(cert_file, 'rb'))
            else:
                self.updater.bot.set_webhook(url=urljoin(app_url, url_path))
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

    def rant_and_cleanup(self, bot, chat_id, rant_text, reply_to_message_id=None):
        if not chat_id in self.rant_msg_ids.keys():
            self.rant_msg_ids[chat_id] = []
        else:
            for rant_msg_id in self.rant_msg_ids[chat_id]:
                bot.delete_message(chat_id=chat_id, message_id=rant_msg_id)
                self.rant_msg_ids[chat_id].remove(rant_msg_id)
        rant_msg = bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id,
                                    text=rant_text, parse_mode='Markdown', disable_web_page_preview=True)
        self.rant_msg_ids[chat_id].append(rant_msg.message_id)

    def help_command_callback(self, bot, update, event_name="help"):
        chat_id = update.message.chat_id
        self.log_and_botan_track(event_name, update.message)
        if update.message.chat.type == "private":
            bot.send_message(chat_id=chat_id, text=self.HELP_TEXT,
                             parse_mode='Markdown', disable_web_page_preview=True)
        else:
            self.rant_and_cleanup(bot, chat_id, self.RANT_TEXT, reply_to_message_id=update.message.message_id)

    def clutter_command_callback(self, bot, update):
        chat_id = update.message.chat_id
        self.log_and_botan_track("clutter", update.message)
        if chat_id in self.NO_CLUTTER_CHAT_IDS:
            self.NO_CLUTTER_CHAT_IDS.remove(chat_id)
            bot.send_message(chat_id=chat_id,
                             text="Chat cluttering is now ON. I *will send audios as replies* to messages with links.",
                             parse_mode='Markdown', disable_web_page_preview=True)
        else:
            self.NO_CLUTTER_CHAT_IDS.add(chat_id)
            bot.send_message(chat_id=chat_id,
                             text="Chat cluttering is now OFF. I *will not send audios as replies* to messages with links.",
                             parse_mode='Markdown', disable_web_page_preview=True)

    def inline_query_callback(self, bot, update):
        inline_query_id = update.inline_query.id
        text = update.inline_query.query
        urls = self.prepare_urls(text=text, get_direct_urls=True)
        results = []
        if urls:
            self.log_and_botan_track("link_inline")
            for url in urls:
                # self.download_and_send(bot, url, self.STORE_CHAT_ID, inline_query_id=update.inline_query.id)
                for direct_url in urls[url].splitlines():  #TODO fix non-mp3 and only sc/bc
                    logger.debug(direct_url)
                    results.append(InlineQueryResultAudio(id=str(uuid4()), audio_url=direct_url, title="FAST_INLINE_DOWNLOAD_DUNNO_WHAT"))
        bot.answer_inline_query(inline_query_id, results)


    def link_command_callback(self, bot, update, args=None):
        self.dl_command_callback(bot, update, args, event_name="link")

    def dl_command_callback(self, bot, update, args=None, event_name="dl"):
        chat_id = update.message.chat_id
        if not args:
            if update.message.chat.type == "private":
                rant_text = "Learn how to use me in /help, you should send /{} with links.".format(event_name)
            else:
                rant_text = self.RANT_TEXT + ", you should send /{} with links.".format(event_name)
            self.rant_and_cleanup(bot, chat_id, rant_text, reply_to_message_id=update.message.message_id)
            return
        bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        urls = self.prepare_urls(msg=update.message, get_direct_urls=(event_name == "link"))  # text=" ".join(args)
        if urls:
            self.log_and_botan_track("{}_cmd".format(event_name), update.message)

            if event_name == "dl":
                wait_message = bot.send_message(chat_id=chat_id, reply_to_message_id=update.message.message_id,
                                                parse_mode='Markdown', text=self.md_italic(self.WAIT_TEXT))
                for url in urls:
                    self.download_and_send(bot, url, chat_id=chat_id, reply_to_message_id=update.message.message_id,
                                           wait_message_id=wait_message.message_id)
            elif event_name == "link":
                link_text = ""
                for i, link in enumerate("\n".join(urls.values()).split()):
                    logger.debug(link)
                    if self.shortener:
                        try:
                            link = self.shortener.short(link)
                        except:
                            pass
                    link_text += "[Download Link #" + str(i + 1) + "](" + link + ")\n"
                bot.send_message(chat_id=chat_id, reply_to_message_id=update.message.message_id,
                                 parse_mode='Markdown', text=link_text)

    def message_callback_query_callback(self, bot, update):
        chat_id = update.callback_query.message.chat_id
        btn_msg_id = update.callback_query.message.message_id
        orig_msg_id, action = update.callback_query.data.split()
        if chat_id in self.msg_store:
            if orig_msg_id in self.msg_store[chat_id]:
                self.log_and_botan_track("_".join([action, "msg"]), self.msg_store[chat_id][orig_msg_id]["message"])
                if action == "dl":
                    update.callback_query.answer(text=self.WAIT_TEXT)
                    edited_msg = update.callback_query.edit_message_text(parse_mode='Markdown',
                                                                         text=self.md_italic(self.WAIT_TEXT))
                    # bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
                    urls = self.msg_store[chat_id][orig_msg_id]["urls"]
                    for url in urls:
                        self.download_and_send(bot, url, chat_id=chat_id, reply_to_message_id=orig_msg_id,
                                               wait_message_id=edited_msg.message_id)
                elif action == "nodl":
                    # update.callback_query.answer(text="Cancelled!", show_alert=True)
                    bot.delete_message(chat_id=chat_id, message_id=btn_msg_id)
                self.msg_store[chat_id].pop(orig_msg_id)
                return
        update.callback_query.answer(text="Very old message, sorry.")
        bot.delete_message(chat_id=chat_id, message_id=btn_msg_id)

    def message_callback(self, bot, update):
        chat_id = update.message.chat_id
        # bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        urls = self.prepare_urls(msg=update.message)  # text=update.message.text
        if urls:
            reply_to_message_id = update.message.message_id
            if update.message.chat.type == "private":
                self.log_and_botan_track("dl_msg", update.message)
                wait_message = bot.send_message(chat_id=chat_id, reply_to_message_id=update.message.message_id,
                                                parse_mode='Markdown', text=self.md_italic(self.WAIT_TEXT))
                logger.debug(urls)
                for url in urls:
                    self.download_and_send(bot, url, chat_id=chat_id, reply_to_message_id=reply_to_message_id,
                                           wait_message_id=wait_message.message_id)
            else:
                self.log_and_botan_track("dl_msg_income")
                orig_msg_id = str(reply_to_message_id)
                if not chat_id in self.msg_store.keys():
                    self.msg_store[chat_id] = {}
                self.msg_store[chat_id][orig_msg_id] = {"message": update.message, "urls": urls}
                button_download = InlineKeyboardButton(text="✅ Yes", callback_data=" ".join([orig_msg_id, "dl"]))
                button_cancel = InlineKeyboardButton(text="❎ No", callback_data=" ".join([orig_msg_id, "nodl"]))
                inline_keyboard = InlineKeyboardMarkup([[button_download, button_cancel]])
                bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id,
                                 reply_markup=inline_keyboard, text="🎶 links found. Download it?")

    def youtube_dl_get_direct_urls(self, url):
        ret_code, direct_urls, std_err = self.youtube_dl["--get-url", url].run(retcode=None)
        if ret_code:
            return None
        else:
            if "returning it as such" in std_err:
                return None
            else:
                return direct_urls

    def prepare_urls(self, msg=None, text=None, get_direct_urls=False):
        if text:
            urls = find_all_links(text, default_scheme="http")
        elif msg:
            urls = []
            for url_str in msg.parse_entities(types=["url"]).values():
                if "://" not in url_str:
                    url_str = "http://" + url_str
                urls.append(URL(url_str))
        else:
            logger.debug("Text or msg is required")
            return
        urls_dict = {}
        for url in urls:
            url_text = url.to_text(True)
            url_parts_num = len([part for part in url.path_parts if part])
            if (
                # SoundCloud: tracks, sets and widget pages
                (self.SITES["sc"] in url.host and (2 <= url_parts_num <= 3 or self.SITES["scapi"] in url_text)) or
                # Bandcamp: tracks and albums
                (self.SITES["bc"] in url.host and (2 <= url_parts_num <= 2)) or
                # YouTube: videos and playlists
                (self.SITES["yt"] in url.host and (
                    "youtu.be" in url.host or "watch" in url.path or "playlist" in url.path))
            ):
                if get_direct_urls:
                    direct_urls = self.youtube_dl_get_direct_urls(url_text)
                    if direct_urls:
                        urls_dict[url_text] = direct_urls
                else:
                    urls_dict[url_text] = ""
            elif not any((site in url.host for site in self.SITES.values())):
                direct_urls = self.youtube_dl_get_direct_urls(url_text)
                if direct_urls:
                    urls_dict[url_text] = direct_urls
        return urls_dict

    def send_alert(self, bot, text, url=None):
        for alert_chat_id in self.ALERT_CHAT_IDS:
            try:
                bot.send_message(chat_id=alert_chat_id, text="ALERT:" + url + "\n" + text)
            except:
                pass

    @run_async
    def download_and_send(self, bot, url, chat_id, reply_to_message_id=None,
                          wait_message_id=None, inline_query_id=None):
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
        ydl = youtube_dl.YoutubeDL(ydl_opts)

        # direct_urls = self.youtube_dl_get_direct_urls(url)

        status = 0
        logger.info("Trying to download URL: %s", url)
        if self.SITES["sc"] in url and self.SITES["scapi"] not in url:
            logger.info("Started scdl process...")
            try:
                cmd_popen = scdl_cmd.popen(stdin=PIPE, stdout=PIPE, stderr=PIPE, universal_newlines=True)
                try:
                    std_out, std_err = cmd_popen.communicate(timeout=self.DL_TIMEOUT)
                    if cmd_popen.returncode or "Error resolving url" in std_err:
                        text = "Failed download with scdl"
                        self.send_alert(bot, text + "\nstdout:\n" + std_out + "\nstderr:\n" + std_err, url)
                    else:
                        text = "Success download with scdl"
                        status = 1
                    logger.info(text)
                    logger.debug("\nstderr:\n" + std_err)
                except TimeoutExpired:
                    text = "Download took with scdl too long, dropped"
                    logger.info(text)
                    cmd_popen.kill()
                    status = -1
            except Exception as exc:
                text = "Failed download with scdl"
                logger.exception(text)
                self.send_alert(bot, text + "\n" + str(exc), url)
        elif self.SITES["bc"] in url:  # or self.SITES["bc"] in " ".join(direct_urls)
            logger.info("Started bandcamp-dl process...")
            try:
                cmd_popen = bandcamp_dl_cmd.popen(stdin=PIPE, stdout=PIPE, stderr=PIPE, universal_newlines=True)
                try:
                    std_out, std_err = cmd_popen.communicate(input="yes", timeout=self.DL_TIMEOUT)
                    if cmd_popen.returncode:
                        text = "Failed download with bandcamp-dl"
                        self.send_alert(bot, text + "\nstdout:\n" + std_out + "\nstderr:\n" + std_err, url)
                    else:
                        text = "Success download with bandcamp-dl"
                        status = 1
                    logger.info(text)
                    logger.debug("\nstderr:\n" + std_err)
                except TimeoutExpired:
                    text = "Download with bandcamp-dl took too long, dropped"
                    logger.info(text)
                    cmd_popen.kill()
                    status = -1
            except Exception as exc:
                text = "Failed download with bandcamp-dl"
                logger.exception(text)
                self.send_alert(bot, text + "\n" + str(exc), url)

        # def handler(signum, frame):
        #     raise TimeoutError(cmd="youtube-dl", timeout=self.DL_TIMEOUT)

        def ydl_download():
            ydl.download([url])

        if status == 0:
            logger.info("Started youtube-dl process...")
            # signal.signal(signal.SIGALRM, handler)
            # signal.alarm(5)
            try:
                p = multiprocessing.Process(target=ydl_download)
                p.start()
                logger.info("start")
                # Wait for seconds or until process finishes
                p.join(self.DL_TIMEOUT)
                logger.info("join")
                # If thread is still active
                if p.is_alive():
                    logger.info("is_alive")
                    # Terminate
                    p.terminate()
                    logger.info("terminate")
                    p.join()
                    logger.info("join2")
                    raise TimeoutError()

                # ydl.download([url])
                text = "Success download with youtube-dl"
                logger.info(text)
                status = 1
            except TimeoutError:
                text = "Download with youtube-dl took too long, dropped"
                logger.exception(text)
                status = -1
            except Exception as exc:
                text = "Failed download with youtube-dl"
                logger.exception(text)
                self.send_alert(bot, text + "\n" + str(exc), url)
                status = -2
            # signal.alarm(0)
            gc.collect()

        if status == -1:
            bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id,
                             text=self.TIMEOUT_TEXT)

        if status == -2:
            bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id,
                             text=self.NO_AUDIO_TEXT)

        if status == 1:
            if chat_id in self.NO_CLUTTER_CHAT_IDS:
                reply_to_message_id = None
            file_list = []
            for d, dirs, files in os.walk(download_dir):
                for file in files:
                    file_list.append(os.path.join(d, file))
            for file in sorted(file_list):
                self.split_and_send_audio_file(bot, chat_id, reply_to_message_id, file)

        shutil.rmtree(download_dir, ignore_errors=True)

        if wait_message_id:
            try:
                bot.delete_message(chat_id=chat_id, message_id=wait_message_id)
            except:
                pass

        # if inline_query_id:
        #     results = []
        #     for audio_id in sent_audio_ids:
        #         if audio_id:
        #             results.append(InlineQueryResultCachedAudio(id=str(uuid4()), audio_file_id=audio_id))
        #     bot.answer_inline_query(inline_query_id, results)

        # downloader = URLopener()

        #     try:
        #         file_name, headers = downloader.retrieve(url.to_text(full_quote=True))
        #         patoolib.extract_archive(file_name, outdir=DL_DIR)
        #         os.remove(file_name)
        #     except Exception as exc:
        #         return str(exc)
        #     #     return str(sys.exc_info()[0:1])
        #
        # return "success"

    def split_and_send_audio_file(self, bot, chat_id, reply_to_message_id=None, file=""):
        sent_audio_ids = []
        file_root, file_ext = os.path.splitext(file)
        file_format = file_ext.replace(".", "")
        if file_format == "mp3" or file_format == "m4a" or file_format == "mp4":
            file_parts = []
            file_size = os.path.getsize(file)
            parts_number = 1
            if file_size > self.MAX_CONVERT_FILE_SIZE:
                logger.info("Large file for convert: %s", file)
                bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id,
                                 text="Downloaded file is larger than I could convert, sorry...")
                return
            else:
                if file_size > self.MAX_TG_FILE_SIZE:
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
                            if id3:
                                id3.save(file_part, v1=2, v2_version=4)
                            file_parts.append(file_part)
                        # https://github.com/jiaaro/pydub/issues/135
                        # https://github.com/jiaaro/pydub/issues/89#issuecomment-75245610
                        del sound
                        gc.collect()
                    except (OSError, MemoryError) as exc:
                        text = "Failed pydub convert"
                        logger.exception(text)
                        self.send_alert(bot, text + "\n" + str(exc), file)
                        bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id,
                                         text="Not enough memory to convert, you may try again later...")
                        gc.collect()
                        return

                else:
                    file_parts.append(file)
                for index, file in enumerate(file_parts):
                    logger.info("Sending: %s", file)
                    bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_AUDIO)
                    # file = translit(file, 'ru', reversed=True)
                    if chat_id in self.NO_CLUTTER_CHAT_IDS:
                        caption = ""
                    else:
                        caption = "Downloaded with @{}".format(self.bot_username)
                    if file_size > self.MAX_TG_FILE_SIZE:
                        caption += "\n" + " ".join(["Part", str(index + 1), "of", str(parts_number)])
                    for i in range(3):
                        try:
                            audio_msg = bot.send_audio(chat_id=chat_id, reply_to_message_id=reply_to_message_id,
                                                       audio=open(file, 'rb'), caption=caption)
                            sent_audio_ids.append(audio_msg.audio.file_id)
                            break
                        except TelegramError as exc:
                            logger.exception('TelegramError')
                            self.send_alert(bot, str(exc), file)
        # return sent_audio_ids
