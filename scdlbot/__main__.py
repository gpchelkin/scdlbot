#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import unicode_literals

import configparser
import logging
import os
import shutil
from urllib.parse import urljoin
from uuid import uuid4

import pkg_resources
import youtube_dl
from boltons.urlutils import find_all_links
from plumbum import local
from pydub import AudioSegment
from telegram import MessageEntity, InlineQueryResultCachedAudio, ChatAction, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.contrib.botan import Botan
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, InlineQueryHandler, \
    ChosenInlineResultHandler, CallbackQueryHandler

# from transliterate import translit

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.DEBUG)
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

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
texts = {}

scdl = local[os.path.join(os.getenv('BIN_PATH', ''), 'scdl')]
bcdl = local[os.path.join(os.getenv('BIN_PATH', ''), 'bandcamp-dl')]
bcdl_template = "%{artist} - %{track} - %{title} [%{album}]"

patterns = {
    "soundcloud": "soundcloud.com",
    "bandcamp": "bandcamp.com",
    "youtube": "youtube.com",
    "youtu.be": "youtu.be",
    "mixcloud": "mixcloud.com"
}

botan = Botan(BOTAN_TOKEN) if BOTAN_TOKEN else None
help_path = '/'.join(('messages', 'help.tg.md'))
help_message = pkg_resources.resource_string(__name__, help_path)
help_message_decoded = help_message.decode("UTF-8")


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


def help_callback(bot, update):
    bot.send_message(chat_id=update.message.chat_id, text=help_message_decoded,
                     parse_mode='Markdown', disable_web_page_preview=True)


def download_callback(bot, update, args=None):
    global texts
    event_name = 'Download'
    if update.inline_query:
        chat_id = STORE_CHAT_ID
        text = update.inline_query.query
        # botan.track(update.inline_query, event_name=event_name + ' Inline Query') if botan else None
    elif update.callback_query:
        chat_id = update.callback_query.message.chat_id
        if str(update.callback_query.data) == "cancel":
            bot.delete_message(chat_id=chat_id, message_id=update.callback_query.message.message_id)
            return
        else:
            update.callback_query.answer(text="Wait a bit..")
            update.callback_query.edit_message_text(text="Wait a bit..")
            text = texts[str(update.callback_query.data).replace("dl_", "")]
    else:
        chat_id = update.message.chat_id
        botan.track(update.message, event_name=event_name) if botan else None
        if args:
            text = " ".join(args)
        else:
            text = update.message.text

    urls = find_all_links(text, default_scheme="http")

    str_urls = " ".join([url.to_text() for url in urls])  # TODO make it better
    if any((pattern in str_urls for pattern in patterns.values())):
        if args or update.inline_query or update.callback_query:
            reply_to_message_id = None
            if update.callback_query:
                wait_message_id = update.callback_query.message.message_id
            elif args or update.inline_query:
                if update.message and chat_id not in NO_CLUTTER_CHAT_IDS:
                    reply_to_message_id = update.message.message_id

                wait_message = bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id,
                                                parse_mode='Markdown', text='_Wait a bit_..')
                wait_message_id = wait_message.message_id

            download_dir = os.path.join(DL_DIR, str(uuid4()))
            shutil.rmtree(download_dir, ignore_errors=True)
            os.makedirs(download_dir)

            for url in urls:
                bot.send_chat_action(chat_id=chat_id, action=ChatAction.RECORD_AUDIO)
                download_audio(url, download_dir)

            file_list = []
            for d, dirs, files in os.walk(download_dir):
                for f in files:
                    path = os.path.join(d, f)
                    file_list.append(path)
            file_list = sorted(file_list)

            sent_audio_ids = []
            for file in file_list:
                sent_audio_ids_file = send_audio(bot, chat_id, reply_to_message_id, file)
                sent_audio_ids.extend(sent_audio_ids_file)

            shutil.rmtree(download_dir, ignore_errors=True)
            bot.delete_message(chat_id=chat_id, message_id=wait_message_id)

            if not sent_audio_ids:
                bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id, parse_mode='Markdown',
                                 text='_Sorry, something went wrong_')
            else:
                if update.inline_query:
                    results = []
                    for audio_id in sent_audio_ids:
                        if audio_id:
                            results.append(
                                InlineQueryResultCachedAudio(
                                    id=str(uuid4()),
                                    audio_file_id=audio_id,
                                )
                            )
                    bot.answer_inline_query(update.inline_query.id, results)
        else:
            reply_to_message_id = update.message.message_id
            texts[str(reply_to_message_id)] = update.message.text
            button_download = InlineKeyboardButton(text="Download", callback_data="dl_" + str(reply_to_message_id))
            button_cancel = InlineKeyboardButton(text="Cancel", callback_data="cancel")
            inline_keyboard = InlineKeyboardMarkup([[button_download, button_cancel]])
            bot.send_message(chat_id=chat_id, reply_to_message_id=reply_to_message_id,
                             reply_markup=inline_keyboard, text="Wanna download?")


def inline_chosen_callback(bot, update):
    pass
    # botan.track(update.chosen_inline_result, event_name='Download Inline Chosen Result') if botan else None


# @run_async
def download_audio(url, download_dir):
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
    elif patterns["bandcamp"] in url.host:
        if 2 <= url_parts_len <= 2:
            bcdl(
                "--base-dir=" + download_dir,  # Base location of which all files are downloaded
                "--template=" + bcdl_template,  # Output filename template
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


# @run_async
def send_audio(bot, chat_id, reply_to_message_id, file):
    sent_audio_ids = []
    if ".mp3" in file:
        file_parts = []
        file_size = os.path.getsize(file)
        if file_size > MAX_TG_FILE_SIZE:
            parts_number = file_size // MAX_TG_FILE_SIZE + 1
            sound = AudioSegment.from_mp3(file)
            part_size = len(sound) / parts_number
            for i in range(parts_number):
                file_part = file.replace(".mp3", ".part" + str(i + 1) + ".mp3")
                part = sound[part_size * i:part_size * (i + 1)]
                part.export(file_part, format="mp3")
                file_parts.append(file_part)
        else:
            file_parts.append(file)
        file_parts_len = len(file_parts)
        for index, file in enumerate(file_parts):
            logger.debug(file)
            bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_AUDIO)
            # file = translit(file, 'ru', reversed=True)
            # TODO add site hashtag
            caption = " ".join(["Part", str(index + 1), "of", str(file_parts_len)]) if file_parts_len > 1 else None
            audio_msg = bot.send_audio(chat_id=chat_id, reply_to_message_id=reply_to_message_id,
                                       audio=open(file, 'rb'), caption=caption)
            sent_audio_ids.append(audio_msg.audio.file_id)
    return sent_audio_ids


def main():
    configure_scdl()
    updater = Updater(token=TG_BOT_TOKEN)
    dispatcher = updater.dispatcher
    start_handler = CommandHandler('start', help_callback)
    dispatcher.add_handler(start_handler)
    help_handler = CommandHandler('help', help_callback)
    dispatcher.add_handler(help_handler)
    download_handler = CommandHandler('dl', download_callback, pass_args=True)
    dispatcher.add_handler(download_handler)
    message_with_links_handler = MessageHandler(Filters.text & (Filters.entity(MessageEntity.URL) |
                                                                Filters.entity(MessageEntity.TEXT_LINK)),
                                                download_callback)
    dispatcher.add_handler(message_with_links_handler)
    inline_keyboard_handler = CallbackQueryHandler(download_callback)
    dispatcher.add_handler(inline_keyboard_handler)
    inline_download_handler = InlineQueryHandler(download_callback)
    dispatcher.add_handler(inline_download_handler)
    inline_chosen_handler = ChosenInlineResultHandler(inline_chosen_callback)
    dispatcher.add_handler(inline_chosen_handler)

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
