#!/bin/bash
# Run scdlbot's command line tools on the server the way the service runs (its user, settings and state), as root:
#   /opt/scdlbot/deploy/cli.sh profile                       the bot's Telegram profile compared with telegram_settings/
#   /opt/scdlbot/deploy/cli.sh profile --apply [--userpic userpic.jpg]   set what differs (and upload the userpic)
#   /opt/scdlbot/deploy/cli.sh fetch [--route NAME] URL      download one link like the bot, into /var/lib/scdlbot/downloads
set -euo pipefail

[ "$(id -u)" = 0 ] || { echo "Run it as root: sudo $0 $*" >&2; exit 1; }
checkout=$(dirname "$(dirname "$(readlink -f "$0")")")
exec systemd-run --quiet --pipe --wait --collect \
  -p User=www-data -p Group=www-data -p EnvironmentFile=/etc/default/scdlbot \
  -p Environment=PATH=/usr/local/bin:/usr/bin:/bin -p "Environment=TELEGRAM_SETTINGS=$checkout/telegram_settings" \
  -p StateDirectory=scdlbot -p WorkingDirectory=/var/lib/scdlbot \
  "$checkout/.venv/bin/scdlbot" "$@"
