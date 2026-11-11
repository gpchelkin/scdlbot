# Music Downloader Telegram Bot aka scdlbot

[![Telegram Bot](https://img.shields.io/badge/telegram-@scdlbot-blue.svg)](https://t.me/scdlbot)
[![MIT License](https://img.shields.io/github/license/gpchelkin/scdlbot.svg)](https://github.com/gpchelkin/scdlbot/blob/master/LICENSE)
[![PyPI Version](https://img.shields.io/pypi/v/scdlbot.svg)](https://pypi.org/project/scdlbot)
[![build](https://github.com/gpchelkin/scdlbot/actions/workflows/build.yml/badge.svg?branch=master)](https://github.com/gpchelkin/scdlbot/actions/workflows/build.yml)
[![Docs](https://readthedocs.org/projects/scdlbot/badge/?version=latest)](https://scdlbot.readthedocs.io)

Telegram bot that downloads audio of tracks, sets, albums, videos and playlists from SoundCloud, Bandcamp, YouTube
and [anything yt-dlp supports](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md), with tags and artwork.
Videos from TikTok, Instagram, Twitter and YouTube Shorts are sent as videos (other YouTube videos too, at 480p, with the 🎬 Video button or `/video`),
Instagram and Twitter photos as photos and albums,
text-only tweets as text,
with the author (`Author: Name (@handle)`, linked to the profile) and the post's text in the caption.
Links to sites that fix their previews in Telegram (fxtwitter.com, fixvx.com, oginstagram.com, kkinstagram.com, tnktok.com
and [others](https://github.com/gpchelkin/scdlbot/blob/master/scdlbot/links.py)) work as links to the original site.

Send `/help` to [@scdlbot](https://t.me/scdlbot) to see how to use it, or read [the help text](https://github.com/gpchelkin/scdlbot/blob/master/scdlbot/texts/help.html).
Please report bugs and ideas in [issues](https://github.com/gpchelkin/scdlbot/issues).

## How it works

One Python process with one asyncio loop ([python-telegram-bot](https://python-telegram-bot.org)):

```
update → handlers.py (parse links, save a job row, enqueue, reply "wait a bit")
           → JobQueue: WORKERS workers
               → yt-dlp subprocess: prints a JSON line for every finished file
               → ffmpeg: re-encode video to h264 if needed, split files bigger than MAX_TG_FILE_SIZE
               → Telegram: send_audio / send_video / albums of up to 10, then delete the file
```

- Handlers never wait for downloads, so the bot stays responsive under any load.
- Downloads run as `python -m yt_dlp …` processes: each gets killed with all its children on timeout,
  and the exact command is logged, so you can reproduce any failure in a shell.
- SQLite ([`db.py`](https://github.com/gpchelkin/scdlbot/blob/master/scdlbot/db.py)) keeps chat settings, the job queue (jobs resume after a restart) and stats.
- Audio stays in its original m4a or mp3 when possible (no re-encoding); anything else becomes mp3.
- Video is h264 in mp4: up to 720p for short videos (TikTok, reels, Shorts), 480p for the rest (YouTube, ~4 MB a minute).
  Videos without h264 are re-encoded up to 30 minutes long (`MAX_REENCODE_DURATION`); longer ones are sent as they are.
- YouTube downloads have their own small worker pool (`YOUTUBE_WORKERS`): each runs deno (~250 MB) to solve
  YouTube's JS challenges, so their number is limited without making other sites wait behind them.

| Module | What it does |
|---|---|
| [`config.py`](https://github.com/gpchelkin/scdlbot/blob/master/scdlbot/config.py) | all settings, from environment variables |
| [`links.py`](https://github.com/gpchelkin/scdlbot/blob/master/scdlbot/links.py) | finds links in messages; `SITES` table of known sites |
| [`handlers.py`](https://github.com/gpchelkin/scdlbot/blob/master/scdlbot/handlers.py) | Telegram commands, messages and buttons |
| [`jobs.py`](https://github.com/gpchelkin/scdlbot/blob/master/scdlbot/jobs.py) | job queue, workers and the download → send pipeline |
| [`ytdlp.py`](https://github.com/gpchelkin/scdlbot/blob/master/scdlbot/ytdlp.py) | yt-dlp command line, output parsing, error codes, cookies |
| [`routes.py`](https://github.com/gpchelkin/scdlbot/blob/master/scdlbot/routes.py) | routes (proxies, source IPs, cookies): which job goes where, retries, leaving out blocked routes |
| [`media.py`](https://github.com/gpchelkin/scdlbot/blob/master/scdlbot/media.py) | ffmpeg / ffprobe |
| [`sender.py`](https://github.com/gpchelkin/scdlbot/blob/master/scdlbot/sender.py) | uploads to Telegram, captions, albums |
| [`db.py`](https://github.com/gpchelkin/scdlbot/blob/master/scdlbot/db.py) | SQLite schema and queries |
| [`observe.py`](https://github.com/gpchelkin/scdlbot/blob/master/scdlbot/observe.py) | Prometheus metrics, systemd notify, owner alerts, Sentry |

To support a new site, add a row to `links.SITES`; for a new command, add a handler in `handlers.py` and register it in `app.py`.

## Run locally

Requirements: Python 3.14+ (e.g. via [pyenv](https://github.com/pyenv/pyenv)), [poetry](https://python-poetry.org),
[mise](https://mise.jdx.dev) (installs [deno](https://deno.com), which yt-dlp needs for YouTube) and ffmpeg.

```bash
mise install           # deno
mise run install       # .venv with pyenv's Python and all dependencies
cp .env.sample .env-dev && nano .env-dev   # at least TG_BOT_TOKEN
mise run dev           # run the bot with .env-dev
```

Other tasks (`mise tasks`):

```bash
mise run fetch https://soundcloud.com/artist/track   # download one link into ./downloads exactly like the bot, without Telegram
mise run fetch --route fin1 https://soundcloud.com/artist/track   # the same through one route (see Routes below)
mise run test
mise run lint
mise run fmt
mise run update        # update dependencies, including yt-dlp's nightly build
```

## Configuration

Environment variables, all documented in [`.env.sample`](https://github.com/gpchelkin/scdlbot/blob/master/.env.sample). Only `TG_BOT_TOKEN` is required.

### Routes

Downloads go out through *routes*: a proxy or a source IP address of the server, each with its own cookies.
They are set in a TOML file (`ROUTES_FILE`, see [`deploy/routes.sample.toml`](https://github.com/gpchelkin/scdlbot/blob/master/deploy/routes.sample.toml)), which also says
which sites use which routes, e.g. YouTube only through the route with its logged-in account.
`systemctl reload scdlbot` applies changes to the file without a restart.
Without the file, `PROXIES`, `SOURCE_IPS` and `COOKIES_FILE` make the routes, like in earlier versions.

- Each site takes its routes in turn, and one download makes all its requests through one route.
- If a site blocks a download (bot check, HTTP 403/429) or the route can't connect, it's tried once more through another route.
- A route that a site keeps blocking is left out for that site for `ROUTE_EXCLUSION` seconds (2 hours), and the owner is told;
  if all routes of a site are left out, all of them are used anyway.
- Downloads that a site gives only to logged-in users (Instagram's age-restricted reels, stories, TikTok's posts that
  "may not be comfortable for some audiences") go once more through the site's login routes (`[login]` in the routes file):
  routes with an account's cookies that anonymous downloads never use, so the account only sees the few downloads that need it.
  A site with login routes gets no cookies through its other routes, so a browser profile logged in to several sites
  (YouTube's route) logs in to such a site only on its login routes. Instagram stories, which Instagram never shows anonymously,
  go straight to the login routes. When the last 8 downloads through a login route all failed
  that way too, its account is probably logged out: the route is left out for a day (or until a reload) and the owner is told.
  [`tools/instagram_cookies.py`](https://github.com/gpchelkin/scdlbot/blob/master/tools/instagram_cookies.py) turns Instagram accounts bought "with cookies" into cookies.txt files.
  [`tools/serve_firefox_cookies.py`](https://github.com/gpchelkin/scdlbot/blob/master/tools/serve_firefox_cookies.py) serves the cookies of a Firefox
  that runs on another server, for `cookies = "firefox:PROFILE_DIR:URL"`.
- Metrics: `scdlbot_route_attempts_total{site,route,result}`, `scdlbot_route_retries_total`, `scdlbot_route_up{site,route}`,
  and a `route` label on `scdlbot_jobs_total` and `scdlbot_download_errors_total`.
- `scdlbot fetch --route NAME URL` downloads through one route, to check it.

Source links: every sent file's caption starts with an invisible link to where it came from (`HIDDEN_SOURCE_LINKS`),
in the format Telegram asked big downloader bots to use in February 2026
([leak](https://t.me/adurovleaks/978)); Telegram then shows those files in link previews of the same URL
([leak](https://t.me/adurovleaks/990)). Links in captions are cleaned up (`NORMALIZE_URLS`): yt-dlp's canonical link
of the post or track, without share tracking (`?si=`, `?igsh=`, TikTok's `u_code`…) and with short links expanded.

The bot's Telegram profile is kept in [`telegram_settings/`](https://github.com/gpchelkin/scdlbot/tree/master/telegram_settings):
name, about, description and commands. `scdlbot profile` shows what differs on Telegram, `scdlbot profile --apply` sets
it, and `--apply --userpic userpic.jpg` also uploads that file as the userpic (on a server: `deploy/cli.sh profile`, below).
In @BotFather: disable privacy mode if the bot should react to plain messages with links in groups (otherwise it only sees
commands); the inline placeholder and whether it may join groups are set there too.

## Deploy

### Server

What [@scdlbot](https://t.me/scdlbot) runs on, as a reference (November 2026):

| | |
|---|---|
| Load | ~16,000 downloads a day for ~49,000 monthly users; busiest in the evening (UTC) |
| Server | Hetzner CPX22: 2 vCPU (AMD EPYC), 4 GB RAM, no swap, Ubuntu 22.04, plus a 50 GB volume for `DL_DIR` |
| Settings | `WORKERS=8`, `YOUTUBE_WORKERS=4`, a local Bot API server, systemd limits of 1.8 CPU and 2.3 GB RAM |
| CPU | ~3 CPU-seconds per download: 0.7 cores on average, up to 1.3 in the busiest hour. YouTube (deno) and video re-encodes cost the most |
| RAM | the bot with its downloads: ~0.5 GB usually, up to 2.2 GB at peaks (each YouTube download runs deno, ~250 MB); the Bot API server: up to 0.4 GB |
| Disk | the database: ~100 MB with 90 days of downloads. `DL_DIR` holds only files being downloaded and sent: usually a few GB, but leave room for `WORKERS` files of `MAX_CONVERT_FILE_SIZE` |
| Traffic | every file is downloaded once and uploaded once: ~240 GB a day each way |

For a personal bot or a few chats, 1 vCPU and 1 GB of RAM (2 GB with a local Bot API server) should do with `WORKERS=2` and `YOUTUBE_WORKERS=1`.

The server needs:

- Linux. With systemd, the bot uses its watchdog (see below); anywhere else, use Docker.
- Python 3.14 or 3.15, e.g. from the [deadsnakes PPA](https://launchpad.net/~deadsnakes/+archive/ubuntu/ppa) on Ubuntu
  (that's what @scdlbot uses) or [pyenv](https://github.com/pyenv/pyenv).
- ffmpeg and ffprobe, and [deno](https://deno.com), the JS runtime yt-dlp needs for YouTube (or node with `YTDLP_ARGS="--js-runtimes node"`),
  in the `PATH` of the service. `deploy/upgrade.sh --tools` (below) puts the latest builds into `/usr/local/bin`, like this:

```bash
tmp=$(mktemp -d) && cd "$tmp"
arch=$(uname -m)   # x86_64, or aarch64 on ARM servers
# ffmpeg and ffprobe: yt-dlp's builds, with its patches
[ "$arch" = aarch64 ] && ff=linuxarm64 || ff=linux64
curl -fsSL "https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-$ff-gpl.tar.xz" \
  | tar -xJ --strip-components=2 --wildcards '*/bin/ffmpeg' '*/bin/ffprobe'
# deno
curl -fsSLo deno.zip "https://github.com/denoland/deno/releases/latest/download/deno-$arch-unknown-linux-gnu.zip" && unzip -q deno.zip
sudo install -m 755 ffmpeg ffprobe deno /usr/local/bin/   # owned by root, replacing the old files
cd && rm -r "$tmp"
ffmpeg -version | head -n 1 && ffprobe -version | head -n 1 && deno --version   # check
```

Your distribution's ffmpeg (`sudo apt install ffmpeg`) works too. Run the same commands again to update them, with no need
to stop the bot: downloads already running keep the old files, and the next ones use the new ones.

### systemd

```bash
sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt install python3.15-venv   # on Ubuntu; or PYTHON=/path/to/python3.14 below
sudo git clone https://github.com/gpchelkin/scdlbot /opt/scdlbot
sudo install -m 600 /opt/scdlbot/.env.sample /etc/default/scdlbot && sudo nano /etc/default/scdlbot
sudo /opt/scdlbot/deploy/upgrade.sh --tools   # without --tools, it keeps the ffmpeg and deno you have
```

[`deploy/upgrade.sh`](https://github.com/gpchelkin/scdlbot/blob/master/deploy/upgrade.sh) installs the bot from the
checkout (master) and yt-dlp from its master branch into a new venv in `/opt/scdlbot/venvs`, installs
[`deploy/scdlbot.service`](https://github.com/gpchelkin/scdlbot/blob/master/deploy/scdlbot.service) and starts the bot.

The service uses `Type=notify` with a watchdog: the bot pings it only while its workers make progress,
so systemd restarts it if it ever gets stuck. Restarts are safe: unfinished downloads start again after them.
Its resource limits (`CPUQuota`, `MemoryMax`) are for 3 vCPUs and 4 GB of RAM. Change them for your server with
`sudo systemctl edit scdlbot`: `upgrade.sh` replaces the service file every time, but not the drop-in this creates.

For big files (up to 2000 MB) and faster uploads run a [local Bot API server](https://github.com/tdlib/telegram-bot-api)
on the same machine ([`deploy/telegram-bot-api.service`](https://github.com/gpchelkin/scdlbot/blob/master/deploy/telegram-bot-api.service)) and set `TG_BOT_API="http://127.0.0.1:8081"`.

Updates:

```bash
sudo /opt/scdlbot/deploy/upgrade.sh   # add --tools for new ffmpeg, ffprobe and deno builds too
```

It pulls, builds a new venv while the bot keeps running and switches to it with one restart. If anything fails before
that, the bot stays as it was, and the last 3 venvs stay for going back (the script says how). yt-dlp comes from its
master branch because sites change all the time, and fixes land there days or weeks before a release.

The command line tools run on the server the way the service does (its user, settings and state), with
[`deploy/cli.sh`](https://github.com/gpchelkin/scdlbot/blob/master/deploy/cli.sh):

```bash
sudo /opt/scdlbot/deploy/cli.sh profile           # the bot's Telegram profile compared with telegram_settings/
sudo /opt/scdlbot/deploy/cli.sh profile --apply   # set what differs; add --userpic userpic.jpg for the userpic
sudo /opt/scdlbot/deploy/cli.sh fetch --route fin1 URL   # one download like the bot's, into /var/lib/scdlbot/downloads
```

### Docker

```bash
docker build -t scdlbot .
docker run -d --name scdlbot --restart unless-stopped --env-file .env -v scdlbot-data:/data -p 127.0.0.1:8000:8000 scdlbot
```

The image has ffmpeg, deno and the yt-dlp nightly build from `poetry.lock`.

## Monitoring and stats

- Prometheus metrics at `http://METRICS_HOST:METRICS_PORT/metrics`: `scdlbot_jobs_total{site,kind,status}`,
  `scdlbot_job_duration_seconds`, `scdlbot_queue_size`, `scdlbot_workers_busy`, `scdlbot_download_errors_total{code}`,
  `scdlbot_rejected_total{reason}`, `scdlbot_active_users{period}`, `scdlbot_active_users_by_language{lang}` and more,
  plus CPU/memory of the bot's main process.
- A Grafana dashboard for them: [`deploy/grafana-dashboard.json`](https://github.com/gpchelkin/scdlbot/blob/master/deploy/grafana-dashboard.json)
  (Dashboards → New → Import, then pick your Prometheus). Its last row also needs cAdvisor and node_exporter on the bot's host,
  with the same `instance` label.
- `/stats` in Telegram (owner only): active users, jobs by status, top sites, app languages.
  Telegram doesn't tell bots users' countries; the language of their Telegram app is the closest thing.
- Errors (unexpected exceptions only) go to `TG_BOT_OWNER_CHAT_ID`, at most one message per minute, and to `SENTRY_DSN` if set.
- When a site starts failing (e.g. YouTube logged out, SoundCloud blocked the IP), `TG_BOT_OWNER_CHAT_ID` gets a message,
  and another one when it recovers (`ALERT_*` settings in `.env.sample`).
- Logs go to stdout (`journalctl -u scdlbot`). Use `LOGLEVEL=DEBUG` to see yt-dlp output.
- Or just ask SQLite:

```bash
sqlite3 scdlbot.sqlite3 "SELECT day, sum(jobs) FROM daily GROUP BY day ORDER BY day DESC LIMIT 14"
sqlite3 scdlbot.sqlite3 "SELECT site, status, sum(jobs) FROM daily WHERE day >= date('now', '-7 day') GROUP BY 1, 2 ORDER BY 3 DESC"
sqlite3 scdlbot.sqlite3 "SELECT count(DISTINCT user_id) FROM user_days WHERE day > date('now', '-30 day')"   # MAU
sqlite3 scdlbot.sqlite3 "SELECT error, count(*) FROM jobs WHERE created_at > strftime('%s', 'now', '-1 day') GROUP BY 1"
```

The database stores Telegram user and chat ids and downloaded URLs; per-download rows are deleted after `JOBS_RETENTION_DAYS`.
[PRIVACY.md](https://github.com/gpchelkin/scdlbot/blob/master/PRIVACY.md) is the privacy policy of [@scdlbot](https://t.me/scdlbot) only. If you run your own bot, write your own
(Telegram requires one, see [§4 of its terms for bot developers](https://telegram.org/tos/bot-developers)) and set its URL in
`PRIVACY_POLICY_URL` (for `/privacy` and `/help`) and in @BotFather. Until you do, Telegram's
[Standard Privacy Policy for bots](https://telegram.org/privacy-tpa) applies to your bot, and `/privacy` links to it.

## History

The name tells where it started: **scdl**bot was a [SoundCloud](https://soundcloud.com) downloader bot built on
[scdl](https://github.com/scdl-org/scdl). Its first version, from January 2016, was a 70-line script that ran
`scdl --onlymp3` for every link it got and sent the mp3 back. In 2017 it learned Bandcamp with
[bandcamp-dl](https://github.com/Evolution0/bandcamp-dl), and YouTube and many more sites with
[youtube-dl](https://github.com/ytdl-org/youtube-dl), replaced by its fork [yt-dlp](https://github.com/yt-dlp/yt-dlp) in 2021.
In 2026 the bot was rewritten from scratch, and yt-dlp now does all the downloading.

Many thanks to the authors of scdl, bandcamp-dl, youtube-dl and yt-dlp: this bot wouldn't exist without them.

## Disclaimer

**The software.** scdlbot is open-source software under the [MIT License](https://github.com/gpchelkin/scdlbot/blob/master/LICENSE), provided "as is", without warranty of any kind.
Like [yt-dlp](https://github.com/yt-dlp/yt-dlp) that it's built on, it's a tool:

- It only processes links that its users send it: it doesn't search, suggest or host anything.
- It doesn't circumvent DRM: DRM-protected tracks (e.g. SoundCloud Go+) are refused. Like yt-dlp, it doesn't support sites that serve only
  DRM-protected content or exist for copyright infringement ([yt-dlp's policy](https://github.com/yt-dlp/yt-dlp/blob/master/CONTRIBUTING.md#is-the-website-primarily-used-for-piracy)).
- It doesn't keep files: the server deletes each file right after sending it to Telegram, so nothing is stored long-term.

**Instances.** Anyone can run their own bot with this code. Whoever runs a bot is its operator and is solely responsible for it:
its settings and changes to the code, how it's used, following the law, [Telegram's terms](https://telegram.org/tos/bot-developers)
and the terms of the sites it downloads from, and its privacy policy (see [Monitoring and stats](#monitoring-and-stats)).
The creator of scdlbot doesn't run, control or answer for any bot other than @scdlbot.

**@scdlbot.** [@scdlbot](https://t.me/scdlbot) is the bot run by its creator, George Pchelkin ([@gpchelkin](https://t.me/gpchelkin)),
under its own [Privacy Policy](https://github.com/gpchelkin/scdlbot/blob/master/PRIVACY.md):

- It only downloads what anyone can stream for free on the site, in a web browser: no paid subscriptions, paywalls or premium content.
- It's meant for personal use. Users are responsible for respecting the rights of creators and the terms of the sites they download from.
- **Rights holders:** write to [@gpchelkin](https://t.me/gpchelkin). Downloads from specific sites or in specific chats can be blocked
  right away, and blocking specific links, channels or accounts can easily be added on request.
