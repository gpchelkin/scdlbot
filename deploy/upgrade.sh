#!/bin/bash
# Install or update scdlbot on a systemd server from this checkout (README: Deploy), as root:
#   /opt/scdlbot/deploy/upgrade.sh          the newest commit of the checkout's branch, with yt-dlp from its master
#   /opt/scdlbot/deploy/upgrade.sh --tools  the same, plus the newest ffmpeg, ffprobe and deno in /usr/local/bin
# It pulls, builds a new venv in venvs/ while the bot keeps running, points .venv at it and restarts the bot, which
# puts unfinished downloads back in the queue. If anything fails before that, the bot is left as it was.
# The last 3 venvs stay, for going back: ln -sfn /opt/scdlbot/venvs/<older> /opt/scdlbot/.venv && systemctl restart scdlbot
# Every run installs deploy/scdlbot.service to /usr/local/lib/systemd/system, so change the service with a drop-in
# (systemctl edit scdlbot), not by editing it.
# PYTHON (default python3.15) and YTDLP (pip requirement) change what is installed, e.g. a yt-dlp fix not merged yet:
#   YTDLP="yt-dlp[default,curl-cffi] @ git+https://github.com/someone/yt-dlp@some-fix" /opt/scdlbot/deploy/upgrade.sh
set -Eeuo pipefail

case "${1-}" in
  "" | --tools) ;;
  *) sed -n '2,4s/^# //p' "$0" >&2; exit 2 ;;
esac
[ "$(id -u)" = 0 ] || { echo "Run it as root: sudo $0 $*" >&2; exit 1; }
cd "$(dirname "$(readlink -f "$0")")/.."
if [ -z "${UPGRADE_PULLED-}" ]; then
  git pull --ff-only
  UPGRADE_PULLED=1 exec deploy/upgrade.sh "$@"  # go on with the script as the pull left it
fi

python=${PYTHON:-python3.15}
ytdlp=${YTDLP:-"yt-dlp[default,curl-cffi] @ git+https://github.com/yt-dlp/yt-dlp@master"}
unit=/usr/local/lib/systemd/system/scdlbot.service
venv=$PWD/venvs/$(date +%Y%m%d-%H%M%S)-$(git rev-parse --short HEAD)
export PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_ROOT_USER_ACTION=ignore

trap 'rm -rf "$venv"; echo "Failed, so the bot was left as it was" >&2' ERR
echo "Building $venv with $("$python" --version)"
"$python" -m venv "$venv"
"$venv/bin/pip" install -q . "$ytdlp"
"$venv/bin/python" -I -c "import scdlbot.app"  # everything imports (-I: not from the checkout's own scdlbot/)
echo "yt-dlp $("$venv/bin/python" -I -m yt_dlp --version)"

if [ "${1-}" = --tools ]; then
  tmp=$(mktemp -d)
  arch=$(uname -m)  # x86_64, or aarch64 on ARM servers
  if [ "$arch" = aarch64 ]; then ff=linuxarm64; else ff=linux64; fi
  # ffmpeg and ffprobe: yt-dlp's builds, with its patches
  curl -fsSL "https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-$ff-gpl.tar.xz" \
    | tar -xJ -C "$tmp" --strip-components=2 --wildcards '*/bin/ffmpeg' '*/bin/ffprobe'
  curl -fsSLo "$tmp/deno.zip" "https://github.com/denoland/deno/releases/latest/download/deno-$arch-unknown-linux-gnu.zip"
  unzip -q -d "$tmp" "$tmp/deno.zip"
  install -m 755 "$tmp/ffmpeg" "$tmp/ffprobe" "$tmp/deno" /usr/local/bin/  # downloads already running keep the old files
  rm -r "$tmp"
  /usr/local/bin/ffmpeg -version | sed -n 1p
  /usr/local/bin/deno --version | sed -n 1p
fi

install -D -m 644 deploy/scdlbot.service "$unit"
if [ -e /etc/systemd/system/scdlbot.service ]; then
  echo "Warning: /etc/systemd/system/scdlbot.service is used instead of $unit; move your changes to a drop-in (systemctl edit scdlbot) and delete it" >&2
fi
systemctl daemon-reload
systemctl enable -q scdlbot

prev=$(readlink .venv || true)  # what the bot runs now
if [ -d .venv ] && [ ! -L .venv ]; then mv .venv venvs/0-made-by-hand; fi  # a venv from before this script
ln -sfn "$venv" .venv.new
mv -T .venv.new .venv
trap - ERR
if ! systemctl restart scdlbot; then
  journalctl -u scdlbot -n 20 --no-pager
  [ -z "$prev" ] || echo "The new version didn't start. To go back: ln -sfn $prev $PWD/.venv && systemctl restart scdlbot" >&2
  exit 1
fi
journalctl -u scdlbot -n 5 --no-pager -o cat
echo "The bot runs $venv"
ls -1d "$PWD"/venvs/*/ | head -n -3 | xargs -r rm -r  # keep the last 3
