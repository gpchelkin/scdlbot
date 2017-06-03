import configparser
import os
import re
import shutil

import requests
from plumbum import local
from transliterate import translit

# import telegram

__author__ = 'gpchelkin'

auth_token = os.getenv('SC_AUTH_TOKEN')
tg_bot_token = os.getenv('TG_BOT_TOKEN')
api_url = 'https://api.telegram.org/bot' + tg_bot_token + '/'
offset = 0

config_scdl = configparser.ConfigParser()
config_scdl['scdl'] = {
    'auth_token': auth_token,  # https://flyingrub.github.io/scdl/
    'path': '.',
}

home_dir = os.path.expanduser('~')
scdl_cfgdir = os.path.join(home_dir, '.config/scdl')
config_file = 'scdl.cfg'

if os.path.exists(scdl_cfgdir):
    shutil.rmtree(scdl_cfgdir)
os.makedirs(scdl_cfgdir)
with open(os.path.join(scdl_cfgdir, config_file), 'w') as f:
    config_scdl.write(f)

dl_dir = os.path.join(home_dir, 'scdl_dir')
scdl_bin = os.getenv('BIN_PATH', '') + 'scdl'
bcdl_bin = os.getenv('BIN_PATH', '') + 'bandcamp-dl'

l_scdl = local[scdl_bin]
l_bcdl = local[bcdl_bin]

bcdl_template = "%{artist} %{album} %{track} - %{title}"


# l_bcdl(
#     "--base-dir=" + dl_dir,  # Base location of which all files are downloaded
#     "--template=" + bcdl_template,  # Output filename template
#     "--overwrite",  # Overwrite tracks that already exist
#     "--embed-lyrics",  # Embed track lyrics (If available)
#     "--group",  # Use album/track Label as iTunes grouping
#     "--embed-art",  # Embed album art (If available)
#     "--no-slugify",  # Disable slugification of track, album, and artist names
#     bcdl_url  # Bandcamp album/track URL
# )


def send_audio(scdl_url, chat_id):
    r_msg = requests.post(api_url + 'sendMessage',
                          json=dict(chat_id=chat_id,
                                    parse_mode='Markdown',
                                    text='_Wait a bit, downloading and sending..._'))
    if os.path.exists(dl_dir):
        shutil.rmtree(dl_dir)
    os.makedirs(dl_dir)
    print("1")
    l_scdl(
        "-l", scdl_url,  # URL can be track/playlist/user
        "-c",  # Continue if a music already exist
        "--path", dl_dir,  # Download the music to a custom path
        "--onlymp3",  # TODO Download only the mp3 file even if the track is Downloadable
        "--addtofile",  # Add the artist name to the filename if it isn't in the filename already
    )
    print("2")
    scdl_file = os.listdir(dl_dir)[0]
    print("3")
    scdl_fullpath = os.path.join(dl_dir, scdl_file)
    print("4")
    track_open = open(scdl_fullpath, 'rb')
    print("5")
    track_read = track_open.read()
    print("6")
    scdlfile_translit = translit(scdl_file, 'ru', reversed=True)
    r_audio = requests.post(api_url + 'sendAudio',
                            files=dict(audio=(scdlfile_translit, track_read, 'audio/mpeg')),
                            data=dict(chat_id=chat_id))
    print('audio ' + scdl_file + ' sent')
    print(r_audio.json()['result'])


while True:
    r = requests.post(api_url + 'getUpdates', json=dict(offset=offset, timeout=60))
    try:
        updates = r.json()['result']
        for i in range(len(updates)):
            offset = updates[i]['update_id']
            message = updates[i].get('message')
            if message:
                chat_id = message['chat']['id']
                text_receive = message.get('text', '').strip()
                if text_receive:
                    print('got text ' + text_receive)  # TODO log time and all to file
                    if re.match('^/start(@scdlbot)?(\s+.*)?$', text_receive) or \
                            re.match('^/help(@scdlbot)?(\s+.*)?$', text_receive):
                        text_send = open('help.md', 'r').read()
                        r_msg = requests.post(api_url + 'sendMessage',
                                              json=dict(chat_id=chat_id, text=text_send,
                                                        parse_mode='Markdown',
                                                        disable_web_page_preview=True))
                    elif re.match('^/dl(@scdlbot)?(\s+.*)?$', text_receive):
                        send_audio(text_receive.split()[1], chat_id)
                    elif re.match('^http(s)?://(m.)?soundcloud.com/([\w\d_-]+)/([\w\d_-]+)$', text_receive):
                        send_audio(text_receive, chat_id)
                else:
                    print('got dunno what')
                msg_from = message.get('from')
                print('  from',
                      msg_from['first_name'],
                      msg_from.get('last_name', ''),
                      '@' + msg_from.get('username', ''))

    except (KeyboardInterrupt, SystemExit):
        raise

    except:
        print('something went wrong')
        # no key 'result' in dictionary -> very bad
        # if r.status_code == requests.codes.ok:
        # if r.json():
        # if r.json()['ok']:
        # if len(updates) != 0:
        # if msgfrom

    finally:
        offset += 1
