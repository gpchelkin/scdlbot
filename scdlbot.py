from __future__ import unicode_literals

import configparser
import logging
import os
import shutil
from uuid import uuid4

import youtube_dl
from boltons.urlutils import find_all_links
from plumbum import local
from telegram import MessageEntity, InlineQueryResultCachedAudio
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, InlineQueryHandler

# from transliterate import translit

SC_AUTH_TOKEN = os.environ['SC_AUTH_TOKEN']
TG_BOT_TOKEN = os.environ['TG_BOT_TOKEN']
STORE_CHAT_ID = os.environ['STORE_CHAT_ID']
DL_DIR = os.getenv('DL_DIR', os.path.join(os.path.expanduser('~'), 'dl_dir'))

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


def show_help(bot, update):
    text_send = open('help.md', 'r').read()
    bot.send_message(chat_id=update.message.chat_id, text=text_send,
                     parse_mode='Markdown', disable_web_page_preview=True)


def initialize():
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


def download_and_send_audio(bot, urls, chat_id=STORE_CHAT_ID):
    shutil.rmtree(DL_DIR, ignore_errors=True)
    os.makedirs(DL_DIR)

    for url in urls:
        url_parts_len = len([part for part in url.path_parts if part])

        if patterns["soundcloud"] in url.host:
            if 2 <= url_parts_len <= 3:
                scdl(
                    "-l", url.to_text(full_quote=True),  # URL of track/playlist/user
                    "-c",  # Continue if a music already exist
                    "--path", DL_DIR,  # Download the music to a custom path
                    "--onlymp3",  # TODO Download only the mp3 file even if the track is Downloadable
                    "--addtofile",  # Add the artist name to the filename if it isn't in the filename already
                )
        elif patterns["bandcamp"] in url.host:
            if 2 <= url_parts_len <= 2:
                bcdl(
                    "--base-dir=" + DL_DIR,  # Base location of which all files are downloaded
                    "--template=" + bcdl_template,  # Output filename template
                    "--overwrite",  # Overwrite tracks that already exist
                    "--group",  # Use album/track Label as iTunes grouping
                    "--embed-art",  # Embed album art (If available)
                    "--no-slugify",  # Disable slugification of track, album, and artist names
                    url.to_text(full_quote=True)  # URL of album/track
                )
        elif (patterns["youtube"] in url.host and "watch" in url.path) or \
                (patterns["youtu.be"] in url.host) or \
                (patterns["mixcloud"] in url.host and 2 <= url_parts_len <= 2):
            os.chdir(DL_DIR)
            ydl_opts = {
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
            }
            with youtube_dl.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url.to_text(full_quote=True)])
    file_list = []
    for d, dirs, files in os.walk(DL_DIR):
        for f in files:
            path = os.path.join(d, f)
            file_list.append(path)
    file_list = sorted(file_list)
    sent_audio = []
    for file in file_list:
        if ".mp3" in file:
            # file_translit = translit(file, 'ru', reversed=True)
            audio_msg = bot.send_audio(chat_id=chat_id, audio=open(file, 'rb'))
            sent_audio.append(audio_msg)
    shutil.rmtree(DL_DIR, ignore_errors=True)
    return sent_audio


def download(bot, update, args=None):
    print("1")
    if args:
        text = " ".join(args)
        chat_id = update.message.chat_id
    elif update.inline_query:
        text = update.inline_query.query
        chat_id = STORE_CHAT_ID
    else:
        text = update.message.text
        chat_id = update.message.chat_id
    urls = find_all_links(text, default_scheme="http")
    str_urls = " ".join([url.to_text() for url in urls])  # TODO
    if any((pattern in str_urls for pattern in patterns.values())):
        bot.send_message(chat_id=chat_id, parse_mode='Markdown', text='_Wait a bit..._')
        sent_audio = download_and_send_audio(bot, urls, chat_id=chat_id)
        if update.inline_query:
            results = []
            for audio_msg in sent_audio:
                results.append(
                    InlineQueryResultCachedAudio(
                        id=str(uuid4()),
                        audio_file_id=audio_msg.audio.file_id,
                    )
                )
            bot.answer_inline_query(update.inline_query.id, results)


def main():
    initialize()

    updater = Updater(token=TG_BOT_TOKEN)
    dispatcher = updater.dispatcher
    logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.DEBUG)
    start_handler = CommandHandler('start', show_help)
    dispatcher.add_handler(start_handler)
    help_handler = CommandHandler('help', show_help)
    dispatcher.add_handler(help_handler)
    dl_handler = CommandHandler(
        'dl',
        download,
        pass_args=True)
    dispatcher.add_handler(dl_handler)
    link_handler = MessageHandler(
        Filters.text & (Filters.entity(MessageEntity.URL) |
                        Filters.entity(MessageEntity.TEXT_LINK)),
        download)
    dispatcher.add_handler(link_handler)
    inline_download_handler = InlineQueryHandler(download)
    dispatcher.add_handler(inline_download_handler)

    updater.start_polling()


if __name__ == '__main__':
    main()

__author__ = "George Pchelkin"
__copyright__ = "Copyright 2017, George Pchelkin"
__credits__ = ["George Pchelkin"]
__license__ = "GPL"
__version__ = "0.0.1"
__maintainer__ = "George Pchelkin"
__email__ = "george@pchelk.in"
__status__ = "Development"
