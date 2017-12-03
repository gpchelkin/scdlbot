import logging
# from botanio import botan
# import json
import os

import pkg_resources
import requests
import youtube_dl
from plumbum import local
from requests.exceptions import Timeout, RequestException, SSLError

bin_path = os.getenv('BIN_PATH', '')
scdl_bin = local[os.path.join(bin_path, 'scdl')]
bandcamp_dl_bin = local[os.path.join(bin_path, 'bandcamp-dl')]
youtube_dl_bin = local[os.path.join(bin_path, 'youtube-dl')]

BOTAN_TRACK_URL = 'https://api.botan.io/track'
BOTAN_SHORTENER_URL = 'https://api.botan.io/s/'

logger = logging.getLogger(__name__)

def get_response_text(file_name):
    # https://stackoverflow.com/a/20885799/2490759
    path = '/'.join(('texts', file_name))
    return pkg_resources.resource_string(__name__, path).decode("UTF-8")

def md_italic(text):
    return "".join(["_", text, "_"])

def youtube_dl_download(url, ydl_opts, queue=None):
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

def botan_track(token, message, event_name):
    try:
        # uid = message.chat_id
        uid = message.from_user.id
    except AttributeError:
        logger.warning('No chat_id in message')
        return False
    num_retries = 3
    ssl_verify = False  # TODO: re-enable
    for i in range(num_retries):
        try:
            r = requests.post(
                BOTAN_TRACK_URL,
                params={"token": token, "uid": uid, "name": event_name},
                json=message.to_dict(),
                verify=ssl_verify,
            )
            return r.json()
        except Timeout:
            # set up for a retry, or continue in a retry loop
            logger.exception("botan_track")
        except (RequestException, ValueError):
            # catastrophic error
            logger.exception("botan_track")
        except SSLError:
            ssl_verify = False
    return False
