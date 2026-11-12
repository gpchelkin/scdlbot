#!/bin/bash
# Build the local Bot API server (https://github.com/tdlib/telegram-bot-api) from source and switch to it, as root
# (README: local Bot API server; first install deploy/telegram-bot-api.service as it says):
#   /opt/scdlbot/deploy/upgrade-telegram-bot-api.sh          the newest commit of its master branch
#   /opt/scdlbot/deploy/upgrade-telegram-bot-api.sh COMMIT   that commit instead, e.g. an older one to go back
# On Ubuntu or Debian, with clang and libc++ as https://tdlib.github.io/telegram-bot-api/build.html says (CLANG=14 is
# Ubuntu 22.04's). The source and the build tree stay in /usr/local/src/telegram-bot-api, so later builds compile only
# what changed (the first one takes an hour or so on 2 cores). Each build goes to /usr/local/lib/telegram-bot-api/
# VERSION-COMMIT, /usr/local/bin/telegram-bot-api points at the one in use, and the last 3 stay, for going back:
#   ln -sfn /usr/local/lib/telegram-bot-api/<older>/bin/telegram-bot-api /usr/local/bin/telegram-bot-api && systemctl restart telegram-bot-api
# The compiler runs with low priority and at most BUILD_MEMORY (default 2G), so that a bot on the same server keeps its
# memory: if td's biggest files need more, the build fails and nothing changes (run it again when the bots are quiet,
# with more BUILD_MEMORY). Only the restart interrupts anything: STOP_FIRST (default scdlbot) is stopped before it, so
# that its running downloads go back into its queue, and started again after.
set -Eeuo pipefail

[ "$(id -u)" = 0 ] || { echo "Run it as root: sudo $0 $*" >&2; exit 1; }
ref=${1:-origin/master}
clang=${CLANG:-14}
src=/usr/local/src/telegram-bot-api
builds=/usr/local/lib/telegram-bot-api
bin=/usr/local/bin/telegram-bot-api
trap 'echo "Failed, so the Bot API server was left as it was" >&2' ERR

apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -q make git zlib1g-dev libssl-dev gperf cmake "clang-$clang" "libc++-$clang-dev" "libc++abi-$clang-dev" >/dev/null
[ -d "$src/.git" ] || git clone -q --recursive https://github.com/tdlib/telegram-bot-api.git "$src"
cd "$src"
git fetch -q origin
git checkout -q --detach "$ref"
git submodule update -q --init --recursive
version=$(sed -nE 's/^project\(TelegramBotApi VERSION ([0-9.]+).*/\1/p' CMakeLists.txt)
commit=$(git rev-parse --short HEAD)
dest=$builds/$version-$commit
echo "Bot API server now: $("$bin" --version 2>/dev/null || echo none); building $version ($commit) into $dest"

if [ ! -x "$dest/bin/telegram-bot-api" ]; then
  mkdir -p build
  cd build
  # Low priority, and in a scope with a memory limit: the kernel kills a compiler there, not a bot
  systemd-run --quiet --scope -p MemoryMax="${BUILD_MEMORY:-2G}" -p MemorySwapMax=0 nice -n 19 ionice -c 3 env \
    CXXFLAGS="-stdlib=libc++" CC="/usr/bin/clang-$clang" CXX="/usr/bin/clang++-$clang" \
    sh -c "cmake -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX:PATH=$dest .. >/dev/null && cmake --build . --target install -j ${JOBS:-1}" \
    || { echo "The build failed: with 'Killed' above, the compiler needed more than BUILD_MEMORY=${BUILD_MEMORY:-2G}" >&2; false; }
fi
"$dest/bin/telegram-bot-api" --version

# The binary from before this script (a plain file) stays as a build too, for going back
if [ -f "$bin" ] && [ ! -L "$bin" ]; then
  old=$builds/$("$bin" --version | awk '{print $NF}')-before-script
  install -D -m 755 "$bin" "$old/bin/telegram-bot-api"
  ln -sfn "$old/bin/telegram-bot-api" "$bin.new" && mv -T "$bin.new" "$bin"
fi
prev=$(readlink -f "$bin" 2>/dev/null || true)
trap - ERR
if [ "$prev" = "$dest/bin/telegram-bot-api" ]; then
  echo "That's the build in use already"
  exit 0
fi

stopped=()
for unit in ${STOP_FIRST-scdlbot}; do
  if systemctl is-active -q "$unit"; then systemctl stop "$unit" && stopped+=("$unit"); fi
done
ln -sfn "$dest/bin/telegram-bot-api" "$bin.new" && mv -T "$bin.new" "$bin"
systemctl restart telegram-bot-api || true  # whether it works shows below
answers() { for _ in $(seq 1 30); do curl -s -m 2 -o /dev/null http://127.0.0.1:8081/ && return 0; sleep 1; done; return 1; }
if answers; then
  echo "The Bot API server runs $version ($commit)"
  status=0
else
  journalctl -u telegram-bot-api -n 20 --no-pager
  echo "The new server didn't answer on 127.0.0.1:8081: going back to $prev" >&2
  { [ -n "$prev" ] && ln -sfn "$prev" "$bin.new" && mv -T "$bin.new" "$bin" && systemctl restart telegram-bot-api && answers; } \
    || echo "The old one doesn't answer either: see journalctl -u telegram-bot-api" >&2
  status=1
fi
for unit in "${stopped[@]}"; do systemctl start "$unit"; done
ls -1dt "$builds"/*/ | tail -n +4 | xargs -r rm -r  # keep the last 3 builds
exit $status
