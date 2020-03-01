# -*- coding: utf-8 -*-

import logging
import os
from logging.handlers import SysLogHandler

from prometheus_client import start_http_server
from telegram_handler import TelegramHandler

from scdlbot.scdlbot import ScdlBot

logging_handlers = []

console_formatter = logging.Formatter('[%(name)s] %(levelname)s: %(message)s')
console_handler = logging.StreamHandler()
console_handler.setFormatter(console_formatter)
console_handler.setLevel(logging.DEBUG)
logging_handlers.append(console_handler)

tg_bot_token = os.environ['TG_BOT_TOKEN']
alert_chat_ids = list(map(int, os.getenv('ALERT_CHAT_IDS', '0').split(',')))
telegram_handler = TelegramHandler(token=tg_bot_token, chat_id=str(alert_chat_ids[0]))
telegram_handler.setLevel(logging.WARNING)
logging_handlers.append(telegram_handler)

syslog_debug = bool(int(os.getenv('SYSLOG_DEBUG', '0')))
syslog_logging_level = logging.DEBUG if syslog_debug else logging.INFO
syslog_hostname = os.getenv("HOSTNAME", "test-host")
syslog_formatter = logging.Formatter('%(asctime)s ' + syslog_hostname + ' %(name)s: %(message)s',
                                     datefmt='%b %d %H:%M:%S')

syslog_address = os.getenv('SYSLOG_ADDRESS', '')
if syslog_address:
    syslog_host, syslog_udp_port = syslog_address.split(":")
    syslog_handler = SysLogHandler(address=(syslog_host, int(syslog_udp_port)))
    syslog_handler.setFormatter(syslog_formatter)
    syslog_handler.setLevel(syslog_logging_level)
    logging_handlers.append(syslog_handler)

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S',
                    level=logging.DEBUG,
                    handlers=logging_handlers)


def main():
    # expose prometheus/openmetrics metrics:
    metrics_host = os.getenv('METRICS_HOST', '127.0.0.1')
    metrics_port = int(os.getenv('METRICS_PORT', '8000'))
    start_http_server(metrics_port, addr=metrics_host)

    store_chat_id = int(os.getenv('STORE_CHAT_ID', '0'))
    no_flood_chat_ids = list(map(int, os.getenv('NO_FLOOD_CHAT_IDS', '0').split(',')))
    dl_timeout = int(os.getenv('DL_TIMEOUT', '300'))
    dl_dir = os.path.expanduser(os.getenv('DL_DIR', '/tmp/scdlbot'))
    chat_storage_file = os.path.expanduser(os.getenv('CHAT_STORAGE', '/tmp/scdlbotdata'))
    serve_audio = bool(int(os.getenv('SERVE_AUDIO', '0')))
    app_url = os.getenv('APP_URL', '')
    max_convert_file_size = int(os.getenv('MAX_CONVERT_FILE_SIZE', '80_000_000'))
    proxies = os.getenv('PROXIES', None)
    if proxies:
        proxies = proxies.split(',')
    source_ips = os.getenv('SOURCE_IPS', None)
    if source_ips:
        source_ips = source_ips.split(',')
    cookies_file = os.getenv('COOKIES_FILE', '')

    scdlbot = ScdlBot(tg_bot_token, proxies,
                      store_chat_id, no_flood_chat_ids, alert_chat_ids,
                      dl_dir, dl_timeout, max_convert_file_size,
                      chat_storage_file, app_url,
                      serve_audio, cookies_file, source_ips)

    use_webhook = bool(int(os.getenv('USE_WEBHOOK', '0')))
    webhook_host = os.getenv('HOST', '127.0.0.1')
    webhook_port = int(os.getenv('PORT', '5000'))
    cert_file = os.getenv('CERT_FILE', '')
    cert_key_file = os.getenv('CERT_KEY_FILE', '')
    url_path = os.getenv('URL_PATH', tg_bot_token.replace(":", ""))
    scdlbot.start(use_webhook, webhook_host, webhook_port, cert_file, cert_key_file, url_path)


if __name__ == '__main__':
    main()
