import os
import re
import shutil
import subprocess

import requests
from transliterate import translit

__author__ = 'gpchelkin'

token = os.getenv('TG_BOT_TOKEN')
apiurl = 'https://api.telegram.org/bot' + token + '/'
offset = 0

homedir = os.path.expanduser('~')

scdlconfdir = os.path.join(homedir, '.config/scdl')
if os.path.exists(scdlconfdir):
    shutil.rmtree(scdlconfdir)
os.makedirs(scdlconfdir)
shutil.copy('scdl.cfg', scdlconfdir)

scdldir = os.path.join(homedir, 'scdldir')
scdlbin = os.getenv('SCDL_BIN_PATH', '') + 'scdl -l '
print(scdlbin)
scdlopts = ' -c --path ' + scdldir + ' --onlymp3 --addtofile'


def sendaudio(scdlurl, chat_id):
    rmsg = requests.post(apiurl + 'sendMessage', json=dict(chat_id=chat_id, parse_mode='Markdown',
                                                           text='_Wait a bit, downloading and sending..._'))
    if os.path.exists(scdldir):
        shutil.rmtree(scdldir)
    print("0")
    os.makedirs(scdldir)
    print("1")
    subprocess.call(scdlbin + scdlurl + scdlopts, shell=True)
    print("2")
    scdlfile = os.listdir(scdldir)[0]
    print("3")
    scdlfullpath = os.path.join(scdldir, scdlfile)
    print("4")
    trackopen = open(scdlfullpath, 'rb')
    print("5")
    track = trackopen.read()
    print("6")
    scdlfile_translit = translit(scdlfile, 'ru', reversed=True)
    raudio = requests.post(apiurl + 'sendAudio',
                           files=dict(audio=(scdlfile_translit, track, 'audio/mpeg')),
                           data=dict(chat_id=chat_id))
    print('audio ' + scdlfile + ' sent')
    print(raudio.json()['result'])


while 1:
    r = requests.post(apiurl + 'getUpdates', json=dict(offset=offset, timeout=60))
    try:
        updates = r.json()['result']
        for i in range(len(updates)):
            offset = updates[i]['update_id']
            message = updates[i].get('message')
            if message:
                chat_id = message['chat']['id']
                gottext = message.get('text')
                if gottext:
                    print('got text ' + gottext)  # TODO log time and all to file
                    if re.compile('^/start(@scdlbot)?(\s+.*)?$').match(gottext.strip()) or \
                            re.compile('^/help(@scdlbot)?(\s+.*)?$').match(gottext.strip()):
                        text = open('help.md', 'r').read()
                        rmsg = requests.post(apiurl + 'sendMessage',
                                             json=dict(chat_id=chat_id, text=text, parse_mode='Markdown',
                                                       disable_web_page_preview=True))
                    elif re.compile('^/dl(@scdlbot)?(\s+.*)?$').match(gottext.strip()):
                        sendaudio(gottext.split()[1], chat_id)
                    elif re.compile('^http(s)?://(m.)?soundcloud.com/([\w\d_-]+)/([\w\d_-]+)$').match(gottext.strip()):
                        sendaudio(gottext, chat_id)
                else:
                    print('got idkwhat')
                msgfrom = message.get('from')
                print('  from ' + msgfrom['first_name'] + ' ' + msgfrom.get('last_name', '') +
                      ' @' + msgfrom.get('username', ''))


    except (KeyboardInterrupt, SystemExit):
        raise

    except:
        print('smth went wrong')
        # no key 'result' in dictionary -> very bad
        # if r.status_code == requests.codes.ok:
        # if r.json():
        # if r.json()['ok']:
        # if len(updates) != 0:
        # if msgfrom

    finally:
        offset += 1
