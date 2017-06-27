#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import unicode_literals

import configparser
import logging
import os
# import shelve
import shutil
import socket
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


# from transliterate import translit

class ContextFilter(logging.Filter):
  hostname = socket.gethostname()

  def filter(self, record):
    record.hostname = ContextFilter.hostname
    return True

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.DEBUG)
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# http://help.papertrailapp.com/kb/configuration/configuring-centralized-logging-from-python-apps/
SYSLOG_ADDRESS = os.getenv('SYSLOG_ADDRESS', '')
if SYSLOG_ADDRESS:
    syslog_hostname, syslog_udp_port = SYSLOG_ADDRESS.split(":")
    syslog_udp_port = int(syslog_udp_port)
    f = ContextFilter()
    logger.addFilter(f)
    syslog = SysLogHandler(address=(syslog_hostname, syslog_udp_port))
    formatter = logging.Formatter('%(asctime)s %(hostname)s scdlbot: %(message)s', datefmt='%b %d %H:%M:%S')
    syslog.setFormatter(formatter)
    logger.addHandler(syslog)


TG_BOT_TOKEN = os.environ['TG_BOT_TOKEN']
BOTAN_TOKEN = os.getenv('BOTAN_TOKEN', '')
STORE_CHAT_ID = os.environ['STORE_CHAT_ID']
SC_AUTH_TOKEN = os.environ['SC_AUTH_TOKEN']
NO_CLUTTER_CHAT_IDS = list(map(int, os.getenv('NO_CLUTTER_CHAT_IDS', '').split(',')))
DL_DIR = os.path.expanduser(os.getenv('DL_DIR', '~'))
USE_WEBHOOK = int(os.getenv('USE_WEBHOOK', '0'))
PORT = int(os.getenv('PORT', '5000'))
APP_URL = os.getenv('APP_URL', '')

MAX_TG_FILE_SIZE = 45000000
WAIT_TEXT = "Wait a bit.."
WAIT_TEXT_MD = "".join(["_", WAIT_TEXT, "_"])
NO_AUDIO_TEXT_MD = "_Sorry, no audios were downloaded_"
DESTROY_TEXT = "Destroyed from the Internets!"  # TODO more fun
msg_store = {}  # TODO shelve

scdl = local[os.path.join(os.getenv('BIN_PATH', ''), 'scdl')]
bcdl = local[os.path.join(os.getenv('BIN_PATH', ''), 'bandcamp-dl')]
BANDCAMP_TEMPLATE = "%{track} - %{artist} - %{title} [%{album}]"

patterns = {
    "soundcloud": "soundcloud.com",
    "bandcamp": "bandcamp.com",
    "youtube": "youtube.com",
    "youtu.be": "youtu.be",
    "mixcloud": "mixcloud.com"
}

botan = Botan(BOTAN_TOKEN) if BOTAN_TOKEN else None
help_path = '/'.join(('messages', 'help.tg.md'))
HELP_MESSAGE = pkg_resources.resource_string(__name__, help_path).decode("UTF-8")


def configure_scdl():
    config = configparser.ConfigParser()
    config['scdl'] = {
        'auth_token': SC_AUTH_TOKEN,
        'path': DL_DIR,
    }
    config_dir = os.path.join(os.path.expanduser('~'), '.config/scdl')
    config_path = os.path.join(config_dir, 'scdl.cfg')
    os.makedirs(config_dir, exist_ok=True)
    with open(config_path, 'w') as f:
        config.write(f)


def start_command_callback(bot, update):
    event_name = "start_command"
    logger.debug(event_name)
    botan.track(update.message, event_name=event_name) if botan else None
    bot.send_message(chat_id=update.message.chat_id, text=HELP_MESSAGE,
                     parse_mode='Markdown', disable_web_page_preview=True)


def help_command_callback(bot, update):
    event_name = "help_command"
    logger.debug(event_name)
    botan.track(update.message, event_name=event_name) if botan else None
    bot.send_message(chat_id=update.message.chat_id, text=HELP_MESSAGE,
                     parse_mode='Markdown', disable_web_page_preview=True)


def inline_query_callback(bot, update):
    event_name = "inline"
    logger.debug(event_name)
    urls = find_all_links(update.inline_query.query, default_scheme="http")
    download_and_send_audio(bot, urls, inline_query_id=update.inline_query.id)


def dl_command_callback(bot, update, args=None):
    event_name = "dl_command"  # Type of chat, can be either “private”, “group”, “supergroup” or “channel”
    logger.debug(event_name)
    botan.track(update.message, event_name=event_name) if botan else None
    chat_id = update.message.chat_id

    if not args:
        bot.send_message(chat_id=chat_id, reply_to_message_id=update.message.message_id,
                         text="See /help")
        return

    urls = find_all_links(" ".join(args), default_scheme="http")
    reply_to_message_id = update.message.message_id if chat_id not in NO_CLUTTER_CHAT_IDS else None
    wait_message = bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id,
                                    parse_mode='Markdown', text=WAIT_TEXT_MD)
    download_and_send_audio(bot, urls, chat_id=chat_id, reply_to_message_id=reply_to_message_id,
                            wait_message_id=wait_message.message_id)


def test_urls(urls):  # TODO make it better
    str_urls = " ".join([url.to_text() for url in urls])
    return any((pattern in str_urls for pattern in patterns.values()))


def callback_query_callback(bot, update):
    global msg_store
    command, orig_msg_id = update.callback_query.data.split()
    event_name = "message_" + command
    logger.debug(event_name)
    botan.track(msg_store[orig_msg_id], event_name=event_name) if botan else None
    urls = find_all_links(msg_store[orig_msg_id].text, default_scheme="http")
    chat_id = update.callback_query.message.chat_id
    wait_message_id = update.callback_query.message.message_id

    if command == "download":
        update.callback_query.answer(text=WAIT_TEXT)
        update.callback_query.edit_message_text(parse_mode='Markdown', text=WAIT_TEXT_MD)
    elif command == "cancel" or command == "destroy":
        if command == "destroy":
            update.callback_query.answer(show_alert=True, text=DESTROY_TEXT)
        bot.delete_message(chat_id=chat_id, message_id=update.callback_query.message.message_id)
        return

    msg_store.pop(orig_msg_id)
    download_and_send_audio(bot, urls, chat_id=chat_id,
                            wait_message_id=update.callback_query.message.message_id)


def message_callback(bot, update):
    event_name = "message"
    logger.debug(event_name)
    botan.track(update.message, event_name=event_name) if botan else None
    urls = find_all_links(update.message.text, default_scheme="http")
    chat_id = update.message.chat_id
    reply_to_message_id = update.message.message_id

    if update.message.chat.type == "private":
        download_and_send_audio(bot, urls, chat_id=chat_id, reply_to_message_id=reply_to_message_id)
    elif test_urls(urls):
        orig_msg_id = str(reply_to_message_id)
        msg_store[orig_msg_id] = update.message
        button_download = InlineKeyboardButton(text="YES", callback_data=" ".join(["download", orig_msg_id]))
        button_cancel = InlineKeyboardButton(text="NO", callback_data=" ".join(["cancel", orig_msg_id]))
        inline_keyboard = InlineKeyboardMarkup([[button_download, button_cancel]])
        bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id,
                         reply_markup=inline_keyboard, text="Download?")


# @run_async
def download_audio(url, download_dir):
    downloader = URLopener()
    url_parts_len = len([part for part in url.path_parts if part])
    if patterns["soundcloud"] in url.host:
        if 2 <= url_parts_len <= 3:
            scdl(
                "-l", url.to_text(full_quote=True),  # URL of track/playlist/user
                "-c",  # Continue if a music already exist
                "--path", download_dir,  # Download the music to a custom path
                "--onlymp3",  # Download only the mp3 file even if the track is Downloadable
                "--addtofile",  # Add the artist name to the filename if it isn't in the filename already
            )
    elif (patterns["bandcamp"] in url.host) or \
        ("/track/" in url.path) or ("/album/" in url.path):  # TODO try/except/log
        if 2 <= url_parts_len <= 2:
            bcdl(
                "--base-dir=" + download_dir,  # Base location of which all files are downloaded
                "--template=" + BANDCAMP_TEMPLATE,  # Output filename template
                "--overwrite",  # Overwrite tracks that already exist
                "--group",  # Use album/track Label as iTunes grouping
                "--embed-art",  # Embed album art (if available)
                "--no-slugify",  # Disable slugification of track, album, and artist names
                url.to_text(full_quote=True)  # URL of album/track
            )
    elif (patterns["youtube"] in url.host and ("watch" in url.path or "playlist" in url.path)) or \
        (patterns["youtu.be"] in url.host) or \
        (patterns["mixcloud"] in url.host and 2 <= url_parts_len <= 2):
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
def send_audio(bot, chat_id, reply_to_message_id, file):
    sent_audio_ids = []
    file_root, file_ext = os.path.splitext(file)
    file_format = file_ext.replace(".", "")
    if file_format == "mp3" or file_format == "m4a" or file_format == "mp4":
        file_parts = []
        file_size = os.path.getsize(file)
        parts_number = 1
        if file_size > MAX_TG_FILE_SIZE:
            id3 = mutagen.id3.ID3(file, translate=False)
            parts_number = file_size // MAX_TG_FILE_SIZE + 1
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
            if file_size > MAX_TG_FILE_SIZE:
                caption = " ".join(["Part", str(index + 1), "of", str(parts_number)])
            audio_msg = bot.send_audio(chat_id=chat_id, reply_to_message_id=reply_to_message_id,
                                       audio=open(file, 'rb'), caption=caption)
            sent_audio_ids.append(audio_msg.audio.file_id)
    return sent_audio_ids


def download_and_send_audio(bot, urls, chat_id=STORE_CHAT_ID, reply_to_message_id=None,
                            wait_message_id=None, inline_query_id=None):
    download_dir = os.path.join(DL_DIR, str(uuid4()))
    shutil.rmtree(download_dir, ignore_errors=True)
    os.makedirs(download_dir)

    for url in urls:
        bot.send_chat_action(chat_id=chat_id, action=ChatAction.RECORD_AUDIO)
        download_audio(url, download_dir)
        # if status != "success":
        #     bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id,
        #                      parse_mode='Markdown', text="`" + status + "`")

    file_list = []
    for d, dirs, files in os.walk(download_dir):
        for f in files:
            file_list.append(os.path.join(d, f))
    file_list = sorted(file_list)

    sent_audio_ids = []
    for file in file_list:
        sent_audio_ids.extend(send_audio(bot, chat_id, reply_to_message_id, file))

    shutil.rmtree(download_dir, ignore_errors=True)
    if wait_message_id:
        bot.delete_message(chat_id=chat_id, message_id=wait_message_id)

    if not sent_audio_ids:
        bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id,
                         parse_mode='Markdown', text=NO_AUDIO_TEXT_MD)
        return

    if inline_query_id:
        results = []
        for audio_id in sent_audio_ids:
            if audio_id:
                results.append(InlineQueryResultCachedAudio(id=str(uuid4()), audio_file_id=audio_id))
        bot.answer_inline_query(inline_query_id, results)



def main():
    configure_scdl()
    updater = Updater(token=TG_BOT_TOKEN)
    dispatcher = updater.dispatcher

    start_command_handler = CommandHandler('start', start_command_callback)
    dispatcher.add_handler(start_command_handler)
    help_command_handler = CommandHandler('help', help_command_callback)
    dispatcher.add_handler(help_command_handler)

    dl_command_handler = CommandHandler('dl', dl_command_callback, pass_args=True)
    dispatcher.add_handler(dl_command_handler)

    message_with_links_handler = MessageHandler(Filters.text & (Filters.entity(MessageEntity.URL) |
                                                                Filters.entity(MessageEntity.TEXT_LINK)),
                                                message_callback)
    dispatcher.add_handler(message_with_links_handler)

    callback_query_handler = CallbackQueryHandler(callback_query_callback)
    dispatcher.add_handler(callback_query_handler)

    inline_query_handler = InlineQueryHandler(inline_query_callback)
    dispatcher.add_handler(inline_query_handler)

    if USE_WEBHOOK:
        url_path = TG_BOT_TOKEN.replace(":", "")
        updater.start_webhook(listen="0.0.0.0",
                              port=PORT,
                              url_path=url_path)
        updater.bot.set_webhook(urljoin(APP_URL, url_path))
        updater.idle()
    else:
        updater.start_polling()


if __name__ == '__main__':
    main()
