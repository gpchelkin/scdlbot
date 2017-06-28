#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os

from scdlbot.scdlbot import SCDLBot


def main():
    tg_bot_token = os.environ['TG_BOT_TOKEN']
    botan_token = os.getenv('BOTAN_TOKEN', '')
    sc_auth_token = os.environ['SC_AUTH_TOKEN']
    store_chat_id = os.environ['STORE_CHAT_ID']
    no_clutter_chat_ids = list(map(int, os.getenv('NO_CLUTTER_CHAT_IDS', '').split(',')))
    dl_dir = os.path.expanduser(os.getenv('DL_DIR', '~'))
    use_webhook = int(os.getenv('USE_WEBHOOK', '0'))
    app_port = int(os.getenv('PORT', '5000'))
    app_url = os.getenv('APP_URL', '')
    bin_path = os.getenv('BIN_PATH', '')
    scdlbot = SCDLBot(tg_bot_token, botan_token, use_webhook,
                      app_url, app_port, bin_path,
                      sc_auth_token, store_chat_id, no_clutter_chat_ids, dl_dir)


if __name__ == '__main__':
    main()
