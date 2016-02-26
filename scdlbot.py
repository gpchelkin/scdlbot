import requests
import regex
import subprocess
import os
import shutil

__author__ = 'gpchelkin'

token = os.getenv('TG_BOT_TOKEN')
apiurl = 'https://api.telegram.org/bot' + token + '/'
offset = 0

homedir = os.path.expanduser('~')
scdldir = os.path.join(homedir,'scdldir')
scdlconfdir = os.path.join(homedir,'.config/scdl')
if os.path.exists(scdldir):
    os.rmdir(scdldir)
os.makedirs(scdldir)
if os.path.exists(scdlconfdir):
    os.rmdir(scdlconfdir)
os.makedirs(scdlconfdir)
shutil.copy('scdl.cfg',scdlconfdir)
scdlbin = 'scdl -l '
scdlopts = ' -c --path ' + scdldir + ' --onlymp3 --addtofile --hide-progress --hidewarnings'


def sendaudio(scdlurl, chat_id):
    rmsg = requests.post(apiurl + 'sendMessage', json=dict(chat_id=chat_id, parse_mode='Markdown', text='_Wait a bit, downloading and sending..._'))
    subprocess.call(scdlbin + scdlurl + scdlopts, shell=True)
    scdlfile = os.listdir(scdldir)[0]
    scdlfullpath = os.path.join(scdldir,scdlfile)
    track = open(scdlfullpath, 'rb').read()
    raudio = requests.post(apiurl + 'sendAudio',
                           files=dict(audio=(scdlfile, track, 'audio/mpeg')),
                           data=dict(chat_id=chat_id))
    print('audio ' + scdlfile + ' sent')
    print(raudio.json()['result'])
    os.remove(scdlfullpath)

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
                    if regex.compile('^/start(@scdlbot)?(\s+.*)?$').match(gottext.strip()) or \
                            regex.compile('^/help(@scdlbot)?(\s+.*)?$').match(gottext.strip()):
                        text = open('help.md', 'r').read()
                        rmsg = requests.post(apiurl + 'sendMessage', json=dict(chat_id=chat_id, text=text, parse_mode='Markdown',
                                                                               disable_web_page_preview=True))
                    elif regex.compile('^/dl(@scdlbot)?(\s+.*)?$').match(gottext.strip()):
                        sendaudio(gottext.split()[1],chat_id)
                    elif regex.compile('^http(s)?://(m.)?soundcloud.com/([\w\d_-]+)/([\w\d_-]+)$').match(gottext.strip()):
                        sendaudio(gottext,chat_id)
                else:
                    print('got idkwhat')
                msgfrom = message.get('from')
                print('  from ' + msgfrom['first_name'] + ' ' + msgfrom.get('last_name', '') +
                          ' @' + msgfrom.get('username', ''))


    except (KeyboardInterrupt, SystemExit):
        raise

    except:
        print('smth went wrong')
        #no key 'result' in dictionary -> very bad
        #if r.status_code == requests.codes.ok:
        #if r.json():
        #if r.json()['ok']:
        #if len(updates) != 0:
        #if msgfrom

    finally:
        offset += 1
