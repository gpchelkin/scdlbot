#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import logging
import os
from logging.handlers import SysLogHandler

from scdlbot.scdlbot import SCDLBot

console_handler = logging.StreamHandler()
logging_handlers = [console_handler]

SYSLOG_ADDRESS = os.getenv('SYSLOG_ADDRESS', '')
if SYSLOG_ADDRESS:
    syslog_hostname, syslog_udp_port = SYSLOG_ADDRESS.split(":")
    syslog_handler = SysLogHandler(address=(syslog_hostname, int(syslog_udp_port)))
    logging_handlers.append(syslog_handler)

logging.basicConfig(format='%(asctime)s {} %(name)s: %(message)s'.format(os.getenv("HOSTNAME", "test-host")),
                    datefmt='%b %d %H:%M:%S',
                    level=logging.DEBUG,
                    handlers=logging_handlers)

logger = logging.getLogger(__name__)


def main():
    tg_bot_token = os.environ['TG_BOT_TOKEN']
    botan_token = os.getenv('BOTAN_TOKEN', '')
    sc_auth_token = os.environ['SC_AUTH_TOKEN']
    store_chat_id = os.environ['STORE_CHAT_ID']
    no_clutter_chat_ids = list(map(int, os.getenv('NO_CLUTTER_CHAT_IDS', '').split(',')))
    dl_dir = os.path.expanduser(os.getenv('DL_DIR', '~'))
    use_webhook = bool(int(os.getenv('USE_WEBHOOK', '0')))
    app_url = os.getenv('APP_URL', '')
    webhook_port = int(os.getenv('PORT', '5000'))
    bin_path = os.getenv('BIN_PATH', '')
    cert_file = os.getenv('CERT_FILE', '')
    google_shortener_api_key = os.getenv('GOOGL_API_KEY', '')
    scdlbot = SCDLBot(tg_bot_token, botan_token, google_shortener_api_key, bin_path,
                      sc_auth_token, store_chat_id,
                      no_clutter_chat_ids, dl_dir)
    scdlbot.run(use_webhook, app_url, webhook_port, cert_file)


if __name__ == '__main__':
    main()
