# Music Downloader Telegram Bot aka scdlbot

[![Telegram Bot](https://img.shields.io/badge/telegram-@scdlbot-blue.svg)](https://t.me/scdlbot)
[![MIT License](https://img.shields.io/github/license/gpchelkin/scdlbot.svg)](LICENSE)
[![PyPI Version](https://img.shields.io/pypi/v/scdlbot.svg)](https://pypi.org/project/scdlbot)
[![build](https://github.com/gpchelkin/scdlbot/actions/workflows/build.yml/badge.svg?branch=master)](https://github.com/gpchelkin/scdlbot/actions/workflows/build.yml)

Telegram bot that downloads audio of tracks, sets, albums, videos and playlists from SoundCloud, Bandcamp, YouTube
and [anything yt-dlp supports](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md), with tags and artwork.
Videos from TikTok, Instagram and X are sent as videos.

Send `/help` to [@scdlbot](https://t.me/scdlbot) to see how to use it, or read [the help text](scdlbot/texts/help.html).
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
- SQLite ([`db.py`](scdlbot/db.py)) keeps chat settings, the job queue (jobs resume after a restart) and stats.
- Audio stays in its original m4a or mp3 when possible (no re-encoding); anything else becomes mp3.
- YouTube downloads have their own small worker pool (`YOUTUBE_WORKERS`): each runs deno (~250 MB) to solve
  YouTube's JS challenges, so their number is limited without making other sites wait behind them.

| Module | What it does |
|---|---|
| [`config.py`](scdlbot/config.py) | all settings, from environment variables |
| [`links.py`](scdlbot/links.py) | finds links in messages; `SITES` table of known sites |
| [`handlers.py`](scdlbot/handlers.py) | Telegram commands, messages and buttons |
| [`jobs.py`](scdlbot/jobs.py) | job queue, workers and the download → send pipeline |
| [`ytdlp.py`](scdlbot/ytdlp.py) | yt-dlp command line, output parsing, error codes, cookies |
| [`media.py`](scdlbot/media.py) | ffmpeg / ffprobe |
| [`sender.py`](scdlbot/sender.py) | uploads to Telegram, captions, albums |
| [`db.py`](scdlbot/db.py) | SQLite schema and queries |
| [`observe.py`](scdlbot/observe.py) | Prometheus metrics, systemd notify, owner alerts, Sentry |

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
mise run test
mise run lint
mise run fmt
mise run update        # update dependencies, including yt-dlp from its master branch
```

## Configuration

Environment variables, all documented in [`.env.sample`](.env.sample). Only `TG_BOT_TOKEN` is required.

In @BotFather: set the commands from [`telegram_settings/commands.txt`](telegram_settings/commands.txt),
and disable privacy mode if the bot should react to plain messages with links in groups (otherwise it only sees commands).

## Deploy

### systemd

```bash
sudo git clone https://github.com/gpchelkin/scdlbot /opt/scdlbot && cd /opt/scdlbot
sudo poetry install --only main            # creates /opt/scdlbot/.venv
sudo cp .env.sample /etc/default/scdlbot && sudo nano /etc/default/scdlbot
sudo cp deploy/scdlbot.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now scdlbot
```

ffmpeg and deno (or node with `YTDLP_ARGS="--js-runtimes node"`) must be in `PATH`.
The service uses `Type=notify` with a watchdog: the bot pings it only while its workers make progress,
so systemd restarts it if it ever gets stuck.

For big files (up to 2000 MB) and faster uploads run a [local Bot API server](https://github.com/tdlib/telegram-bot-api)
on the same machine ([`deploy/telegram-bot-api.service`](deploy/telegram-bot-api.service)) and set `TG_BOT_API="http://127.0.0.1:8081"`.

### Docker

```bash
docker build -t scdlbot .
docker run -d --name scdlbot --restart unless-stopped --env-file .env -v scdlbot-data:/data -p 127.0.0.1:8000:8000 scdlbot
```

## Monitoring and stats

- Prometheus metrics at `http://METRICS_HOST:METRICS_PORT/metrics`: `scdlbot_jobs_total{site,kind,status}`,
  `scdlbot_job_duration_seconds`, `scdlbot_queue_size`, `scdlbot_workers_busy`, `scdlbot_download_errors_total{code}`,
  `scdlbot_rejected_total{reason}`, `scdlbot_active_users{period}`, `scdlbot_active_users_by_language{lang}` and more,
  plus CPU/memory of the bot's main process.
- `/stats` in Telegram (owner only): active users, jobs by status, top sites, app languages.
  Telegram doesn't tell bots users' countries; the language of their Telegram app is the closest thing.
- Errors (unexpected exceptions only) go to `TG_BOT_OWNER_CHAT_ID`, at most one message per minute, and to `SENTRY_DSN` if set.
- Logs go to stdout (`journalctl -u scdlbot`). Use `LOGLEVEL=DEBUG` to see yt-dlp output.
- Or just ask SQLite:

```bash
sqlite3 scdlbot.sqlite3 "SELECT day, sum(jobs) FROM daily GROUP BY day ORDER BY day DESC LIMIT 14"
sqlite3 scdlbot.sqlite3 "SELECT site, status, sum(jobs) FROM daily WHERE day >= date('now', '-7 day') GROUP BY 1, 2 ORDER BY 3 DESC"
sqlite3 scdlbot.sqlite3 "SELECT count(DISTINCT user_id) FROM user_days WHERE day > date('now', '-30 day')"   # MAU
sqlite3 scdlbot.sqlite3 "SELECT error, count(*) FROM jobs WHERE created_at > strftime('%s', 'now', '-1 day') GROUP BY 1"
```

The database stores Telegram user and chat ids and downloaded URLs; per-download rows are deleted after `JOBS_RETENTION_DAYS`.
