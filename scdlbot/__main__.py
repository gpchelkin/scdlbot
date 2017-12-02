#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import logging
import os
from logging.handlers import SysLogHandler

from logentries import LogentriesHandler

from scdlbot.scdlbot import SCDLBot

# import loggly.handlers


SYSLOG_DEBUG = bool(int(os.getenv('SYSLOG_DEBUG', '0')))
if SYSLOG_DEBUG:
    logging_level = logging.DEBUG
else:
    logging_level = logging.INFO

logging_handlers = []

console_formatter = logging.Formatter('[%(name)s] %(levelname)s: %(message)s')
console_handler = logging.StreamHandler()
console_handler.setFormatter(console_formatter)
console_handler.setLevel(logging_level)
logging_handlers.append(console_handler)

syslog_formatter = logging.Formatter('%(asctime)s ' + os.getenv("HOSTNAME", "test-host") + ' %(name)s: %(message)s',
                                     datefmt='%b %d %H:%M:%S')

SYSLOG_ADDRESS = os.getenv('SYSLOG_ADDRESS', '')
if SYSLOG_ADDRESS:
    syslog_hostname, syslog_udp_port = SYSLOG_ADDRESS.split(":")
    syslog_handler = SysLogHandler(address=(syslog_hostname, int(syslog_udp_port)))
    syslog_handler.setFormatter(syslog_formatter)
    syslog_handler.setLevel(logging_level)
    logging_handlers.append(syslog_handler)

LOGENTRIES_TOKEN = os.getenv('LOGENTRIES_TOKEN', '')
if LOGENTRIES_TOKEN:
    logentries_handler = LogentriesHandler(LOGENTRIES_TOKEN)
    logentries_handler.setFormatter(syslog_formatter)
    logentries_handler.setLevel(logging_level)
    logging_handlers.append(logentries_handler)

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    datefmt='%b %d %H:%M:%S',
                    level=logging_level,
                    handlers=logging_handlers)


def main():
    tg_bot_token = os.environ['TG_BOT_TOKEN']
    botan_token = os.getenv('BOTAN_TOKEN', '')
    sc_auth_token = os.environ['SC_AUTH_TOKEN']
    store_chat_id = int(os.getenv('STORE_CHAT_ID', '0'))
    no_flood_chat_ids = list(map(int, os.getenv('NO_FLOOD_CHAT_IDS', '0').split(',')))
    alert_chat_ids = list(map(int, os.getenv('ALERT_CHAT_IDS', '0').split(',')))
    dl_timeout = int(os.getenv('DL_TIMEOUT', '300'))
    dl_dir = os.path.expanduser(os.getenv('DL_DIR', '/tmp/scdl'))
    chat_storage_file = os.path.expanduser(os.getenv('CHAT_STORAGE', '/tmp/scdlbotdata'))
    use_webhook = bool(int(os.getenv('USE_WEBHOOK', '0')))
    app_url = os.getenv('APP_URL', '')
    webhook_port = int(os.getenv('PORT', '5000'))
    max_convert_file_size = int(os.getenv('MAX_CONVERT_FILE_SIZE', '80000000'))
    bin_path = os.getenv('BIN_PATH', '')
    cert_file = os.getenv('CERT_FILE', '')
    cert_key_file = os.getenv('CERT_KEY_FILE', '')
    google_shortener_api_key = os.getenv('GOOGL_API_KEY', '')
    scdlbot = SCDLBot(tg_bot_token, botan_token, google_shortener_api_key, bin_path,
                      sc_auth_token, store_chat_id, no_flood_chat_ids, alert_chat_ids,
                      dl_dir, dl_timeout, max_convert_file_size, chat_storage_file)
    scdlbot.start(use_webhook, app_url, webhook_port, cert_file, cert_key_file, url_path=tg_bot_token)


if __name__ == '__main__':
    main()
