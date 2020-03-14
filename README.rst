Music Downloader Telegram Bot aka scdlbot
=========================================


.. image:: https://img.shields.io/badge/Telegram-@scdlbot-blue.svg
        :target: https://t.me/scdlbot
        :alt: Telegram Bot

.. image:: https://travis-ci.com/gpchelkin/scdlbot.svg?branch=master
        :target: https://travis-ci.com/gpchelkin/scdlbot
        :alt: Travis CI Build Status

.. image:: https://github.com/gpchelkin/scdlbot/workflows/Python%20package/badge.svg
        :target: https://github.com/gpchelkin/scdlbot/actions
        :alt: GitHub Actions Status

.. image:: https://img.shields.io/github/license/gpchelkin/scdlbot.svg
        :target: https://raw.githubusercontent.com/gpchelkin/scdlbot/master/LICENSE
        :alt: GitHub License

.. image:: https://readthedocs.org/projects/scdlbot/badge/?version=latest
        :target: https://scdlbot.readthedocs.io/
        :alt: Documentation Status

.. image:: https://img.shields.io/pypi/v/scdlbot.svg
        :target: https://pypi.org/project/scdlbot
        :alt: PyPI Version

.. image:: https://codeclimate.com/github/gpchelkin/scdlbot/badges/issue_count.svg
        :target: https://codeclimate.com/github/gpchelkin/scdlbot
        :alt: Code Climate Issue Count

.. image:: https://api.codacy.com/project/badge/Grade/7dfb6d8e7a094987b303e9283fc7368c
        :target: https://www.codacy.com/app/gpchelkin/scdlbot
        :alt: Codacy Build Status

.. image:: https://codebeat.co/badges/57243b9d-2269-4f31-a35b-6aedd11626d2
        :target: https://codebeat.co/projects/github-com-gpchelkin-scdlbot-master
        :alt: Codebeat Quality

.. image:: https://bettercodehub.com/edge/badge/gpchelkin/scdlbot?branch=master
        :target: https://bettercodehub.com/results/gpchelkin/scdlbot
        :alt: Better Code Hub Compliance

.. image:: https://www.codefactor.io/repository/github/gpchelkin/scdlbot/badge
        :target: https://www.codefactor.io/repository/github/gpchelkin/scdlbot
        :alt: CodeFactor

.. image:: https://static.deepsource.io/deepsource-badge-light-mini.svg
        :target: https://deepsource.io/gh/gpchelkin/scdlbot/
        :alt: DeepSource

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

-  `Telegram Bot API <https://core.telegram.org/bots/api>`__:
   `python-telegram-bot <https://github.com/python-telegram-bot/python-telegram-bot>`__
-  `SoundCloud <https://soundcloud.com>`__:
   `scdl <https://github.com/flyingrub/scdl>`__
-  `Bandcamp <https://bandcamp.com>`__:
   `bandcamp-dl <https://github.com/iheanyi/bandcamp-dl>`__
-  `YouTube <https://www.youtube.com/>`__, `Yandex.Music <https://music.yandex.com/>`__,
   `Mixcloud <https://www.mixcloud.com/>`__, and almost everything from this `list <https://ytdl-org.github.io/youtube-dl/supportedsites.html>`__:
   `youtube-dl <https://ytdl-org.github.io/youtube-dl>`__

Run your own scdlbot
--------------------

Installation & Configuration
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Requirements
""""""""""""

Those should be available in your ``PATH``:

-  `Python 3.6+ <https://www.python.org/>`__
   (`pyenv <https://github.com/pyenv/pyenv>`__ and `poetry <https://python-poetry.org/>`__ are recommended)
-  `FFmpeg 4.0+ <https://ffmpeg.org/download.html>`__ if not running on Heroku
   (fresh builds for `Windows, macOS <https://ffmpeg.zeranoe.com/builds/>`__
   and `Linux <https://johnvansickle.com/ffmpeg/>`__ are recommended)
-  `Heroku CLI <https://cli.heroku.com/>`__ is recommended if you want to deploy to Heroku

Install / Update stable from `PyPI <https://pypi.org/project/scdlbot>`__ (recommended)
""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""

::

    pip3 install scdlbot --upgrade

...or get unstable from `Git source repository <https://github.com/gpchelkin/scdlbot>`__
""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""

::

    git clone https://github.com/gpchelkin/scdlbot.git
    cd scdlbot
    pip3 install --requirement requirements.txt

    # Update:
    git pull
    pip3 install --requirement requirements.txt


Configure Bot
"""""""""""""

Download or copy configuration file sample and set up
config environment variables in it:

::

    # If you've installed from PyPI - download sample config somewhere:
    curl -o .env https://raw.githubusercontent.com/gpchelkin/scdlbot/master/.env.sample

    # If you've got Git source - just copy sample config:
    cp .env.sample .env

    # Use your favourite editor. Sample config is self-documented:
    nano .env

Telegram Bot Settings
"""""""""""""""""""""

Send the commands from respective filenames in ``telegram_settings`` dir to `@BotFather <https://t.me/BotFather>`__, choose your bot and copy corresponding values in order to use the bot conveniently.
Disable privacy mode if you want bot to read and check every message in group for links.
Otherwise, it would work only for commands.

Running Locally or at Dedicated Server
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Using `Heroku Local <https://devcenter.heroku.com/articles/heroku-local#run-your-app-locally-using-the-heroku-local-command-line-tool>`__ (preferred)
"""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""

You will need `Heroku CLI <https://cli.heroku.com/>`__ installed.

::

    # If you've installed from PyPI - download Procfile first (otherwise already present in Git repository):
    curl -O https://raw.githubusercontent.com/gpchelkin/scdlbot/master/Procfile

    # For long polling mode (USE_WEBHOOK=0):
    heroku local worker
    # For webhook mode (USE_WEBHOOK=1):
    heroku local web

Using only Python
"""""""""""""""""

::

    export $(cat .env | egrep -v '^#' | xargs)
    python3 -m scdlbot
    # or in one line:
    env $(cat .env | egrep -v '^#' | xargs) python3 -m scdlbot

    # If you've installed package from PyPI into the system,
    # you can also replace 'python3 -m scdlbot' with pure 'scdlbot'

Deploying to `Heroku <https://heroku.com/>`__
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

|Deploy|

Register on Heroku, press the button above and
configure variables for deploying.
When app is deployed you **must** set only one dyno working on
"Resources" tab in your app settings depending on `which way of getting
updates <https://core.telegram.org/bots/api#getting-updates>`__ you have
chosen and set in config variables: ``worker`` for polling or ``web``
for webhook.

Manually
""""""""

You can do the same as the button above but using `Heroku
CLI <https://cli.heroku.com/>`__, not much of a fun. Assuming you are in
``scdlbot`` repository directory:

::

    heroku login
    # Create app with Python 3 buildpack and set it for upcoming builds:
    heroku create --buildpack heroku/python
    heroku buildpacks:set heroku/python
    # Add FFmpeg buildpack needed for youtube-dl & scdl:
    heroku buildpacks:add --index 1 https://github.com/laddhadhiraj/heroku-buildpack-ffmpeg.git --app scdlbot
    # Deploy app to Heroku:
    git push heroku master
    # Set config vars automatically from your local .env file
    heroku plugins:install heroku-config
    heroku config:push
    # Or set them manually:
    heroku config:set TG_BOT_TOKEN="<TG_BOT_TOKEN>" STORE_CHAT_ID="<STORE_CHAT_ID>" ...

If you use webhook, start web dyno and stop worker dyno:

::

    heroku ps:scale web=1 worker=0
    heroku ps:stop worker

If you use polling, start worker dyno and stop web dyno:

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
