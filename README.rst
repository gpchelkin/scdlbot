Music Downloader Telegram Bot aka scdlbot
=========================================


.. image:: https://img.shields.io/badge/telegram-@scdlbot-blue.svg
        :target: https://t.me/scdlbot
        :alt: Telegram Bot

.. image:: https://img.shields.io/github/license/gpchelkin/scdlbot.svg
        :target: https://github.com/gpchelkin/scdlbot/blob/master/LICENSE
        :alt: MIT License

.. image:: https://readthedocs.org/projects/scdlbot/badge/?version=latest
        :target: https://scdlbot.readthedocs.io/
        :alt: Documentation Status

.. image:: https://img.shields.io/pypi/v/scdlbot.svg
        :target: https://pypi.org/project/scdlbot
        :alt: PyPI Version

.. image:: https://img.shields.io/endpoint?url=https://python-poetry.org/badge/v0.json
        :target: https://python-poetry.org/
        :alt: Poetry

.. image:: https://github.com/gpchelkin/scdlbot/workflows/build/badge.svg?branch=master&event=push
        :target: https://github.com/gpchelkin/scdlbot/actions/workflows/build.yml
        :alt: GitHub Actions Build Status

.. image:: https://github.com/gpchelkin/scdlbot/actions/workflows/codeql-analysis.yml/badge.svg?branch=master&event=push
        :target: https://github.com/gpchelkin/scdlbot/actions/workflows/codeql-analysis.yml
        :alt: GitHub Actions CodeQL Status

.. image:: https://deepsource.io/gh/gpchelkin/scdlbot.svg/?label=active+issues&show_trend=true
        :target: https://deepsource.io/gh/gpchelkin/scdlbot/?ref=repository-badge
        :alt: DeepSource Active Issues

.. image:: https://deepsource.io/gh/gpchelkin/scdlbot.svg/?label=resolved+issues&show_trend=true
        :target: https://deepsource.io/gh/gpchelkin/scdlbot/?ref=repository-badge
        :alt: DeepSource Resolved Issues

.. image:: https://codeclimate.com/github/gpchelkin/scdlbot/badges/issue_count.svg
        :target: https://codeclimate.com/github/gpchelkin/scdlbot
        :alt: Code Climate Issue Count

.. image:: https://api.codacy.com/project/badge/Grade/7dfb6d8e7a094987b303e9283fc7368c
        :target: https://app.codacy.com/gh/gpchelkin/scdlbot
        :alt: Codacy Build Status

.. image:: https://codebeat.co/badges/57243b9d-2269-4f31-a35b-6aedd11626d2
        :target: https://codebeat.co/projects/github-com-gpchelkin-scdlbot-master
        :alt: Codebeat Quality

.. image:: https://www.codefactor.io/repository/github/gpchelkin/scdlbot/badge
        :target: https://www.codefactor.io/repository/github/gpchelkin/scdlbot
        :alt: CodeFactor

Telegram Bot for downloading MP3 rips of tracks/sets from
SoundCloud, Bandcamp, YouTube with tags and artwork.


* Free software: `MIT License <https://github.com/gpchelkin/scdlbot/blob/master/LICENSE>`__
* Documentation: https://scdlbot.readthedocs.io


.. contents:: :depth: 2


scdlbot Usage in Telegram
-------------------------

Send ``/start`` or ``/help`` command to bot
or refer directly to the `help message <scdlbot/texts/help.tg.md>`__.

Please report all bugs and issues and suggest your improvements
to `issues <https://github.com/gpchelkin/scdlbot/issues>`__.

Supported sites and mainly used packages
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

scdlbot is standing on the shoulders of giants:

-  `Telegram Bot API <https://core.telegram.org/bots/api>`__:
   `python-telegram-bot <https://github.com/python-telegram-bot/python-telegram-bot>`__
-  `SoundCloud <https://soundcloud.com>`__:
   `scdl <https://github.com/flyingrub/scdl>`__
-  `Bandcamp <https://bandcamp.com>`__:
   `bandcamp-dl <https://github.com/iheanyi/bandcamp-dl>`__
-  `YouTube <https://www.youtube.com/>`__, `Yandex.Music <https://music.yandex.com/>`__,
   `Mixcloud <https://www.mixcloud.com/>`__, and almost `everything from this list <https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md>`__:
   `yt-dlp <https://github.com/yt-dlp/yt-dlp>`__

Run your own scdlbot
--------------------

Installation & Configuration
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Requirements
""""""""""""

Those should be available in your ``PATH``:

-  `Python 3.9+ <https://www.python.org>`__
   (`pyenv <https://github.com/pyenv/pyenv>`__ and `poetry <https://python-poetry.org/>`__ are recommended)
-  `yt-dlp strongly recommended dependencies <https://github.com/yt-dlp/yt-dlp#strongly-recommended>`__, including:
-  `FFmpeg 6.1+ <https://ffmpeg.org/download.html>`__ (in your `$PATH`) (if not running on Heroku/Render)

   -  `yt-dlp patched builds are highly recommended <https://github.com/yt-dlp/FFmpeg-Builds>`__
   -  otherwise these fresh builds are recommended:
      `Linux <https://johnvansickle.com/ffmpeg/>`__,
      `macOS <https://evermeet.cx/ffmpeg/>`__,
      `Windows <https://www.gyan.dev/ffmpeg/builds/#release-builds>`__.
-  `Heroku CLI <https://cli.heroku.com>`__ is recommended if you want to deploy to Heroku

Install / Update stable build from `PyPI <https://pypi.org/project/scdlbot>`__ (recommended)
""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""

::

    pip install --upgrade scdlbot

...or get latest unstable build from `Git source repository <https://github.com/gpchelkin/scdlbot>`__
"""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""

::

    git clone https://github.com/gpchelkin/scdlbot.git
    cd scdlbot
    pip install --editable "./"

    # or just install directly from Git with pip:
    pip install "scdlbot @ git+https://github.com/gpchelkin/scdlbot.git@master"


Configure Bot
"""""""""""""

Download or copy configuration file sample and set up
config environment variables in it:

::

    # Skip if you've got Git source:
    curl -o .env.sample https://raw.githubusercontent.com/gpchelkin/scdlbot/master/.env.sample

    cp .env.sample .env
    # Use your favourite editor. Sample config is self-documented:
    nano .env

Telegram Bot Settings
"""""""""""""""""""""

Send the commands from respective filenames in ``telegram_settings`` dir to `@BotFather <https://t.me/BotFather>`__, choose your bot and copy corresponding values in order to use the bot conveniently.
Disable privacy mode if you want bot to read and check every message in group for links.
Otherwise, it would work only for commands.

Running Locally or on Dedicated Server
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Using `Heroku Local <https://devcenter.heroku.com/articles/heroku-local#run-your-app-locally-using-the-heroku-local-command-line-tool>`__
"""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""

You will need `Heroku CLI <https://cli.heroku.com/>`__ installed.

::

    # Skip if you've got Git source:
    curl -O https://raw.githubusercontent.com/gpchelkin/scdlbot/master/Procfile

    # For long polling mode (if WEBHOOK_ENABLE="0"):
    heroku local -e .env worker
    # For webhook mode (if WEBHOOK_ENABLE="1"):
    heroku local -e .env web

Using only Bash & Python
""""""""""""""""""""""""

::

    export $(grep -v '^#' .env | xargs)
    python -m scdlbot
    # or in one line:
    env $(grep -v '^#' .env | xargs) python -m scdlbot

    # If you've installed package into your system Python,
    # you can also replace 'python -m scdlbot' with just 'scdlbot'
    # If you use Windows, you _have_ to replace 'python -m scdlbot' with just 'scdlbot'.

Deploying to `Heroku <https://www.heroku.com>`__
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

|Deploy|

Register on Heroku, press the Deploy button above and
configure variables for deploying.
When app is deployed you **must** set only one dyno working on
"Resources" tab in your app settings depending on `which way of getting
updates <https://core.telegram.org/bots/api#getting-updates>`__ you have
chosen and set in config variables: ``worker`` for polling or ``web``
for webhook.

Manually
""""""""

You can do the same as the button above but using `Heroku
CLI <https://cli.heroku.com/>`__. Assuming you are in
``scdlbot`` Git repository directory:

::

    # Log into Heroku:
    heroku login
    # Create app with Python 3 buildpack and set it for upcoming builds:
    heroku create --buildpack heroku/python myscdlbot
    #heroku buildpacks:set heroku/python --app=myscdlbot
    # Add FFmpeg buildpack needed for youtube-dl & scdl:
    heroku buildpacks:add --index 1 https://github.com/gpchelkin/heroku-buildpack-ffmpeg-latest.git --app=myscdlbot
    # Set config vars automatically from your local .env file:
    heroku plugins:install heroku-config
    heroku config:push --file=.env --app=myscdlbot
    # or set them manually like this:
    heroku config:set TG_BOT_TOKEN="<TG_BOT_TOKEN>" TG_BOT_OWNER_CHAT_ID="<TG_BOT_OWNER_CHAT_ID>" ...
    # Deploy app to Heroku:
    #heroku git:remote --app=myscdlbot
    git push heroku master

Then, if you want to use webhook, start web dyno and stop worker dyno:

::

    heroku ps:scale web=1 worker=0
    heroku ps:stop worker

If you want to use polling, start worker dyno and stop web dyno:

::

    heroku ps:scale worker=1 web=0
    heroku ps:stop web

Some useful commands:

::

    # Attach to logs:
    heroku logs -t
    # Test run ffprobe
    heroku run "ffprobe -version"

Deploying to `Dokku <https://github.com/dokku/dokku>`__
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Use Dokku (your own Heroku) installed on your own server.
App is tested and fully ready for deployment with polling
(no webhook yet).
https://github.com/dokku/dokku-letsencrypt

::

    export DOKKU=<your_dokku_server>
    scp .env $DOKKU:~
    ssh $DOKKU
        export DOKKU=<your_dokku_server>
        dokku apps:create scdlbot
        dokku certs:generate scdlbot scdlbot.$DOKKU
        dokku config:set scdlbot $(cat .env | xargs)
        logout
    git remote add dokku dokku@$DOKKU:scdlbot
    git push dokku master
    ssh $DOKKU
        dokku ps:scale scdlbot worker=1 web=0
        dokku ps:restart scdlbot

.. |Deploy| image:: https://www.herokucdn.com/deploy/button.svg
    :target: https://heroku.com/deploy
