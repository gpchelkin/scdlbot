# Changelog

## 1.1.0

- Links to sites that fix link previews in Telegram work as links to the original site: fxtwitter.com, fixupx.com,
  twittpr.com, vxtwitter.com and fixvx.com for Twitter, oginstagram.com, uuinstagram.com, kkinstagram.com and
  ddinstagram.com for Instagram, tnktok.com, tiktxk.com, tfxktok.com, kktiktok.com, tiktokez.com and vxtiktok.com
  for TikTok, koutube.com for YouTube, rxddit.com, vxreddit.com and redditez.com for Reddit, fxbsky.app, bskyx.app and
  bsyy.app for Bluesky (Reddit and Bluesky with Unknown sites enabled).
- `scdlbot fetch` downloads the same link the bot would (the original site's for those links).
- `deploy/upgrade.sh` installs or updates the bot on a systemd server: it pulls the checkout, builds a new venv with yt-dlp
  from master while the bot keeps running, switches to it with one restart and keeps the last 3 venvs for going back.
  With `--tools` it also updates ffmpeg, ffprobe and deno.
- `deploy/scdlbot.service` is installed to `/usr/local/lib/systemd/system` by that script, so your own settings go in a
  drop-in (`systemctl edit scdlbot`). Its watchdog timeout is 180 seconds (was 120), as on @scdlbot's server.
- A Grafana dashboard, `deploy/grafana-dashboard.json`: downloads, sites, errors, queue, routes, users and resources.
- A site with login routes (`[login]`) logs in only through them: its other routes go to it without their cookies.
  A Firefox profile's cookies hold every site it logged in to, so a profile for the YouTube account that also logged in
  to TikTok no longer logs in to TikTok on every download through that route; TikTok's posts for logged-in users ("may not
  be comfortable for some audiences") go through TikTok's login route.
- A route whose cookies file doesn't exist (it downloads without cookies, so a login route can't log in) is an error
  for the owner at start and on every reload.
- Apple Music and Spotify's tracks, albums, playlists and artists aren't tried with "Unknown sites" on: they come only
  with DRM. Their podcasts still are (podcasts.apple.com, open.spotify.com/episode).
- Cookies downloaded from a URL are readable only by the bot (600, in a 700 directory): they are an account's session.
- `scdlbot profile` compares the bot's Telegram profile (name, about, description, commands) with `telegram_settings/`,
  and with `--apply` sets what differs (`--userpic FILE` uploads the userpic too). `deploy/cli.sh` runs it and
  `scdlbot fetch` on a server the way the service runs.
- `tools/serve_firefox_cookies.py` serves a Firefox profile's cookies (a consistent copy, with what's still in Firefox's
  write-ahead log) to the bot on another server, for routes with `cookies = "firefox:PROFILE_DIR:URL"`.
- `tools/instagram_cookies.py` also reads accounts sold with the website's cookies as base64-encoded JSON, and with
  `--first N` it numbers the files from N, so new accounts don't replace the files of older ones.

## 1.0.0 (2026-11-07)

scdlbot is rewritten from scratch. The new bot is one asyncio process with a download queue, yt-dlp downloads from every site, and SQLite keeps settings, the queue and stats.
It has been running [@scdlbot](https://t.me/scdlbot) since October 2026:
about 16,000 downloads a day for about 49,000 monthly users, on a server with 2 vCPUs and 4 GB of RAM.

### For users

- **No more hangs.** The bot answers right away however busy it is: downloads wait in a queue, and the ⏳ message shows your place in it.
  Downloads cut off by a restart start again by themselves.
- **Three modes** for messages without a command (/settings):
  - *Auto-download all* (was *Download*).
  - *Auto-download + ask about video*: new, and the default in private chats. Everything downloads right away,
    except YouTube links, which ask 🎵 Audio or 🎬 Video. Chats on *Auto-download all* were switched to it once;
    /settings switches them back.
  - *Ask before downloading* (was *Ask*): the default in groups. ⬇️ Download / ❌ for links, 🎵 / 🎬 / ❌ for YouTube.
- **YouTube videos as video** (480p) with the 🎬 button or `/video <links>`. YouTube Shorts always come as video (720p).
- **Instagram**: reels, posts with photos and videos (carousels as albums), and stories.
  **Twitter / X**: videos, GIFs, photos and text-only tweets. **TikTok**: videos.
  Captions name the author (with a link to the profile) and include the post's text.
- **Audio keeps its original format** (m4a or mp3, no re-encoding), with tags and cover art. Anything else becomes mp3,
  and the new *Always MP3* setting converts everything.
- **Albums and playlists** go out as Telegram albums of up to 10 tracks while the rest download, up to 50 tracks a link,
  with a note when there were more.
- **Bigger files**: up to 1000 MB. On a bot without a local Bot API server, files bigger than Telegram's limit are split into parts.
- **Videos play on iPhones**: VP9 and HEVC videos are re-encoded to h264 (up to 30 minutes long), and every video is sent
  with its size and duration, so iOS no longer squeezes Instagram videos into a square.
- **Videos without sound**: when a site mutes a video in the server's country (licensed music on Instagram), the bot gets it
  from another country. When it's muted everywhere, the caption says so.
- **`/dl` as a reply** to a message with links (or a reply that mentions the bot) downloads those links,
  e.g. links sent while the bot was offline. Short links like `t.co` are expanded.
- **Clearer errors**: private or deleted, not available in the bot's country, age-restricted, DRM-protected (SoundCloud Go+),
  live streams, too large, the site limiting downloads, and more.
- **Clean links in captions**, without share tracking (`?si=`, `?igsh=`, TikTok's `u_code`). Every file also carries
  an invisible link to its source, so Telegram can show it in link previews of the same URL.
- **/privacy**: the privacy policy and disclaimer.

### For people running their own bot

The configuration is mostly compatible, but read [`.env.sample`](https://github.com/gpchelkin/scdlbot/blob/master/.env.sample):

- Python 3.14 or 3.15, ffmpeg and deno (or node) in `PATH`. Install from PyPI (`pip install scdlbot`) or from git, then run it
  with the systemd unit ([`deploy/scdlbot.service`](https://github.com/gpchelkin/scdlbot/blob/master/deploy/scdlbot.service), `Type=notify` with a watchdog) or the `Dockerfile`.
  The [README](https://github.com/gpchelkin/scdlbot#deploy) explains the server it needs and how to install everything.
- Settings live in a SQLite database (`DB_PATH`). Settings from the old pickle file (`CHAT_STORAGE`) aren't carried over.
- **New settings**:
  - `DB_PATH`, `JOBS_RETENTION_DAYS`
  - `WORKERS` and `YOUTUBE_WORKERS` (a separate pool for YouTube, whose downloads run deno), `JOB_TIMEOUT`, `MAX_QUEUE`, `MAX_USER_JOBS`
  - `MAX_PLAYLIST_ITEMS`, `MAX_REENCODE_DURATION`, `FFMPEG_THREADS`, `YTDLP_ARGS`
  - `ROUTES_FILE`, `COOKIES_REFRESH`, `ROUTE_EXCLUSION`
  - `NORMALIZE_URLS`, `HIDDEN_SOURCE_LINKS`, `PRIVACY_POLICY_URL`
  - `DROP_PENDING_UPDATES`, `ALERT_*`, `SENTRY_DSN`
- **Changed defaults**:
  - `DL_TIMEOUT` is 35 minutes per file (was 5 minutes), and `JOB_TIMEOUT` is 40 minutes per link.
  - `MAX_CONVERT_FILE_SIZE` is 1000 MB with a local Bot API server and 350 MB without one. It now limits every download.
- **No longer used** (ignored): `BIN_PATH`, `CHECK_URL_TIMEOUT`, `COMMON_CONNECTION_TIMEOUT`, `SYSLOG_ADDRESS`, `HOSTNAME`,
  `SC_AUTH_TOKEN`, `CHAT_STORAGE`.
- **Routes** ([`deploy/routes.sample.toml`](https://github.com/gpchelkin/scdlbot/blob/master/deploy/routes.sample.toml)):
  - Each route is a proxy or a source IP, with its own cookies, and each site can have its own list of routes, taken in turn.
  - A download a site blocks is tried once more through another route.
  - A route a site keeps blocking is left out for a while, and the owner is told.
  - Login routes, with an account's cookies, take only the downloads that need an account (Instagram stories, age-restricted reels).
  - `systemctl reload scdlbot` reloads the file without a restart.
  - Without the file, `PROXIES`, `SOURCE_IPS` and `COOKIES_FILE` still work. Cookies from a URL or a Firefox profile are refreshed in the background.
- **For the owner** (`TG_BOT_OWNER_CHAT_ID`):
  - /stats shows active users, the queue, downloads and files by kind for the last 24 hours and 7 days, top sites and app languages.
  - A message when a site starts failing and when it recovers.
  - Unexpected errors, at most one message a minute, and to Sentry or GlitchTip if `SENTRY_DSN` is set.
- **Prometheus metrics**: jobs by site, kind, status and route; download errors; queue and workers; files and bytes sent
  by site and kind; route attempts and retries; active users by period and app language.
- **`scdlbot fetch [--route NAME] URL`** downloads one link exactly like the bot, without Telegram, to debug a site or a route.
- **Tools**: [`tools/instagram_cookies.py`](https://github.com/gpchelkin/scdlbot/blob/master/tools/instagram_cookies.py) makes cookies.txt files
  for Instagram accounts; [`tools/reachable.py`](https://github.com/gpchelkin/scdlbot/blob/master/tools/reachable.py) checks
  which users the bot can still message.

### Under the hood

- Telegram handlers never wait for downloads. The old bot froze when every update slot waited for a busy download process.
- Every download is a `python -m yt_dlp` subprocess in its own process group:
  - It's killed with its ffmpeg and deno children on timeout.
  - Its exact command is logged.
  - It prints a line for each finished file, so files go out while the rest download.
- One shared Telegram connection with a rate limiter, so floods no longer hit Telegram's limits. With a local Bot API server, files are sent straight from disk.
- SQLite (WAL) keeps the chat settings, the queue, pending questions and stats. Per-download rows are deleted after
  `JOBS_RETENTION_DAYS`; daily totals are kept.
- A systemd watchdog tied to download progress restarts the bot if it ever gets stuck. When memory runs out, the kernel kills a download, not the bot.
- Dependencies: python-telegram-bot 22, yt-dlp, aiosqlite, prometheus-client, sentry-sdk. scdl, bandcamp-dl, pebble, requests,
  plumbum, ffmpeg-python, mutagen, boltons and others are gone.
- poetry with standard `[project]` metadata, ruff, pytest (156 tests), mise tasks.
  CI publishes a release to PyPI and GitHub when its version tag is pushed.

### Removed

- The Link mode and `/link`. They had been broken for a while.
- scdl and bandcamp-dl: yt-dlp downloads SoundCloud and Bandcamp.
- Heroku, Render and Dokku files, syslog/Papertrail logging, the reStructuredText docs (the README is the documentation, also on
  [Read the Docs](https://scdlbot.readthedocs.io)), flake8/tox and other old tooling.

## 0.15.0 (2023-02-20)

- refactor and fix many things

## 0.14.2 (2022-12-19)

- merge #508:
  - support 320 Kbps
  - stop the bot from handling telegram links
  - added domain and id blacklisting and whitelisting
  - add metadata to downloaded songs
  - more described in PR #508
- support working in channels, close #176

## 0.14.1 (2022-12-18)

- fix youtube playlist and video in playlist download, close #439

## 0.14.0 (2022-12-18)

- many things

## 0.13.6 (2020-08-03)

- add and handle timeout for youtube-dl get-url and cookies file download
- update requirements

## 0.13.5 (2020-08-02)

- update requirements

## 0.13.4 (2020-05-10)

- support tiktok video download/upload
- update requirements

## 0.13.3 (2020-04-18)

- many cosmetic refinements
- update requirements

## 0.13.2 (2020-03-25)

- use scdl fork with soundcloud api v2 support
- add support for m.soundcloud links for new scdl fork
- bump requirements

## 0.13.0 (2020-02-28)

- upgrade to python-telegram-bot version 12
- support photo captions with links
- disable support for edited messages
- replace goo.gl with yandex' clck.ru, shortening works properly again
- refactor a little, remove deprecated configs, start fixing docs

## 0.12.2 (2020-01-14)

- refined source IP handling

## 0.12.1 (2020-01-13)

- hotfix for env parsing

## 0.12.0 (2020-01-13)

- add support for source IPs randomization for downloading from youtube

## 0.11.0 (2020-01-12)

- add support for cookie file for youtube-dl - it helps with downloading yandex.music from different countries

## 0.10.0 (2020-01-07)

- use ffmpeg-python for converting instead of RAM-heavy pydub, remove pydub requirement
- update docs and requirements
- many cosmetic and CI fixes

## 0.9.27 (2019-12-01)

- return timestamp to scdl using some hacks
- don't treat one track's fail as playlist's fail for scdl

## 0.9.26 (2019-11-30)

- upgrade requirements to use scdl from git
- various deployment and CI/CD improvements

## 0.9.21-25 (2019-11-04)

- catch scdl 'not streamable' error and add some explanation about youtube '429 too many requests' error
- upgrade requirements
- add python 3.8 support
- various deployment and CI/CD improvements

## 0.9.20 (2019-10-11)

- upgrade requirements
- add python 3.7 support
- remove pyup
- fix travis pypi access

## 0.9.19 (2019-02-09)

- edited help message
- upgrade requirements

## 0.9.18 (2018-09-01)

- upgrade requirements

## 0.9.17 (2018-05-12)

- upgrade requirements

## 0.9.16 (2018-03-08)

- markdown in audio captions HOTFIX

## 0.9.15 (2018-03-03)

- markdown in audio captions

## 0.9.14 (2018-02-11)

- source is now captions
- more precise help and settings

## 0.9.13 (2018-02-11)

- short original links in captions
- wait a bit added

## 0.9.12 (2018-02-11)

- flood is now source
- wait a beet/beat random
- provide youtube video titles in caption
- 200 char caption text wrap

## 0.9.11 (2018-02-10)

- send tags and time length to Telegram
- extract-artist feature
- wait a beet
- requirements updated

## 0.9.10 (2017-12-14)

- convert non-mp3 files to mp3
- don't apologize for pictures
- don't allow sc/you links
- some renaming, move get_direct_urls to utils

## 0.9.9 (2017-12-12)

- serve audios with nginx and send URLs instead of files https://core.telegram.org/bots/api#sending-files

## 0.9.8 (2017-12-05)

- b0tan logging improvement
- requirements updated

## 0.9.7.2 (2017-12-05)

- console debug logging

## 0.9.7.1 (2017-12-05)

- easter egg added

## 0.9.7 (2017-12-04)

- new telegram logging
- only admins can change settings
- close button in settings

## 0.9.6 (2017-12-03)

- separate file for utils, botan track refactored
- parse message for markdown text links too
- temporary disable SSL verification for botan (cert expired)

## 0.9.5.2 (2017-12-03)

- hot fix

## 0.9.5.1 (2017-12-02)

- fix logging

## 0.9.5 (2017-12-02)

- settings command
- persistent storage for settings
- refactoring

## 0.9.4 (2017-11-27)

- exception-driven behavour
- clutter is now flood
- better alerting

## 0.9.3 (2017-11-22)

- don't spam on second try
- better logging config for different destinations

## 0.9.2 (2017-11-21)

- more error checking and responding with errors
- much much much polishing and refactoring

## 0.9.1 (2017-11-20)

- more error checking and responding
- limit youtube-dl time to download
- avoid live downloads
- logging refactor and refinement
- help updates

## 0.9.0 (2017-11-20)

- return of inline mode as fast download (link is sent to telegram servers for download)
- refactor and refinement
- help updates
- add some spam captions :)

## 0.8.3 (2017-11-19)

- gc.collect() according to https://github.com/jiaaro/pydub/issues/89#issuecomment-75245610

## 0.8.2 (2017-11-19)

- cool refinements in logging
- store urls, so button response is faster now

## 0.8.1 (2017-11-19)

- some logging fixes

## 0.8.0 (2017-11-19)

- many fixes and workarounds
- alerting & logging

## 0.7.10 (2017-11-05)

- botanio fix - send user id, not chat id

## 0.7.9 (2017-11-05)

- botanio fix
- tmpreaper config sample
- clutter fix

## 0.7.8 (2017-11-04)

- botanio
- maintenance

## 0.7.7 (2017-09-11)

- maintenance

## 0.7.6 (2017-09-11)

- SYSLOG_DEBUG env var to disable logging of full messages
- maintenance
- Logentries support

## 0.7.5.1 (2017-09-03)

- YouTube number remove

## 0.7.5 (2017-09-03)

- maintenance

## 0.7.4 (2017-08-03)

- msg_store fixes

## 0.7.3 (2017-07-20)

- orig_msg_id hotfix and don't send chat action on every link

## 0.7.2 (2017-07-19)

- Updated requirements

## 0.7.1 (2017-07-05)

- Hotfix

## 0.7.0 (2017-07-05)

- Travis CI, tests and docs from cookiecutter

## 0.6.3 (2017-07-04)

- Back to bandcamp-dl and scdl and download timeouts

## 0.6.2 (2017-07-04)

- Help message in groups now redirects to PM

## 0.6.1 (2017-07-03)

- Async run of download/send command
- Link command

## 0.6.0 (2017-07-02)

- Added text files to sdist
- Bandcamp and SoundCloud-widgets is now downloaded with youtube-dl
- Supported parsing widgets from pages
- Refactor

## 0.5.1 (2017-07-02)

- New clutter command
- Help refinements
- Some fixes

## 0.5.0 (2017-06-28)

- Big refactor to class-based
- Syslog support
- Some fixes

## 0.4.0 (2017-06-15)

- Console script!
- Setup script version improvements
- Ask in groups only, download immediately in private
- Bandcamp: Download links without 'bandcamp' for /dl
- Move TODOs to issues
- Button to destroy music from the Internet

## 0.3.1 (2017-06-12)

- Markdown to reStructuredText
- Copy tags to parts

## 0.3.0 (2017-06-10)

- YouTube playlists support
- Split audio by 50 MB size for sending
- Disable privacy mode and ask for download

## 0.2.0 (2017-06-06)

- Webhooks and async

## 0.1.0 (2017-06-04)

- First usable and stable version.

## The beginning (2016-01-09)

The first commit: one 69-line `scdlbot.py` on Heroku that polled the Bot API with `requests`, downloaded SoundCloud
tracks with [scdl](https://github.com/flyingrub/scdl) (MP3 rips only), set their tags and artwork and sent them as
Telegram audio, with /start, /help and /dl.

- 2016-01-09: mobile links (`m.soundcloud.com`)
- 2016-01-15: the bot token moved from the code to an environment variable
- 2016-02-26: Python 3.5.1, `http://` links too
- 2016-05-14: Russian file names transliterated to Latin
