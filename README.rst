=============================
Music Downloader Telegram Bot
=============================


.. image:: https://img.shields.io/badge/Telegram-@scdlbot-blue.svg
        :target: https://t.me/scdlbot
        :alt: Telegram Bot

.. image:: https://readthedocs.org/projects/scdlbot/badge/?version=latest
        :target: https://scdlbot.readthedocs.io/en/latest/?badge=latest
        :alt: Documentation Status

.. image:: https://img.shields.io/github/license/gpchelkin/scdlbot.svg
        :target: https://raw.githubusercontent.com/gpchelkin/scdlbot/master/LICENSE.txt
        :alt: GitHub License

.. image:: https://img.shields.io/pypi/v/scdlbot.svg
        :target: https://pypi.org/project/scdlbot
        :alt: PyPI Version

.. image:: https://pyup.io/repos/github/gpchelkin/scdlbot/shield.svg?token=376ffde2-5188-4912-bf3c-5f316e52d43f
        :target: https://pyup.io/repos/github/gpchelkin/scdlbot/
        :alt: PyUp Updates

.. image:: https://img.shields.io/travis/gpchelkin/scdlbot.svg
        :target: https://travis-ci.org/gpchelkin/scdlbot
        :alt: Travis CI Build Status

.. image:: https://codeclimate.com/github/gpchelkin/scdlbot/badges/gpa.svg
        :target: https://codeclimate.com/github/gpchelkin/scdlbot
        :alt: Code Climate GPA

.. image:: https://codeclimate.com/github/gpchelkin/scdlbot/badges/issue_count.svg
        :target: https://codeclimate.com/github/gpchelkin/scdlbot
        :alt: Code Climate Issue Count

.. image:: https://codeclimate.com/github/gpchelkin/scdlbot/badges/coverage.svg
        :target: https://codeclimate.com/github/gpchelkin/scdlbot/coverage
        :alt: Code Climate Test Coverage

.. image:: https://api.codacy.com/project/badge/Grade/7dfb6d8e7a094987b303e9283fc7368c
        :target: https://www.codacy.com/app/gpchelkin/scdlbot
        :alt: Codacy Build Status

.. image:: https://scrutinizer-ci.com/g/gpchelkin/scdlbot/badges/quality-score.png?b=master
        :target: https://scrutinizer-ci.com/g/gpchelkin/scdlbot/?branch=master
        :alt: Scrutinizer Code Quality

.. image:: https://bettercodehub.com/edge/badge/gpchelkin/scdlbot?branch=master
        :target: https://bettercodehub.com
        :alt: Better Code Hub Compliance

.. image:: https://codebeat.co/badges/102be98c-56c1-46af-895d-f1f15b2f2520
        :target: https://codebeat.co/projects/github-com-gpchelkin-scdlbot-master
        :alt: Codebeat Quality


Telegram Bot for downloading MP3 rips of tracks/sets from SoundCloud, Bandcamp, YouTube with tags and artwork.


* Free software: MIT license
* Documentation: https://scdlbot.readthedocs.io


.. contents:: :depth: 2


Bot Usage
---------

Send ``/start`` or ``/help`` command to bot or refer directly to the `help message <scdlbot/messages/help.tg.md>`__.

Please report all bugs and issues and suggest your improvements to `issues <https://github.com/gpchelkin/scdlbot/issues>`__.

Supported sites and mainly used packages
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

-  `Telegram Bot API <https://core.telegram.org/bots/api>`__:
   `python-telegram-bot <https://github.com/python-telegram-bot/python-telegram-bot>`__
-  `SoundCloud <https://soundcloud.com>`__:
   `scdl <https://github.com/flyingrub/scdl>`__
-  `Bandcamp <https://bandcamp.com>`__:
   `bandcamp-dl <https://github.com/iheanyi/bandcamp-dl>`__
-  `YouTube <https://www.youtube.com/>`__,
   `Mixcloud <https://www.mixcloud.com/>`__, everything else from the `list <https://rg3.github.io/youtube-dl/supportedsites.html>`__:
   `youtube-dl <https://rg3.github.io/youtube-dl>`__

Development
-----------

Installation
~~~~~~~~~~~~

Requirements
^^^^^^^^^^^^

Those should be available in your ``PATH``:

-  `Python 3.5+ <https://www.python.org/>`__
   (`pyenv <https://github.com/pyenv/pyenv>`__ recommended)
-  `FFmpeg 3.4 <https://ffmpeg.org/download.html>`__ if not running on Heroku
   (fresh builds for `Windows <https://ffmpeg.zeranoe.com/builds/>`__
   and `Linux <https://johnvansickle.com/ffmpeg/>`__ are recommended)
-  `Heroku CLI <https://cli.heroku.com/>`__ is recommended

Install / Update stable from `PyPI <https://pypi.python.org/pypi/scdlbot>`__ (recommended)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

::

    pip3 install scdlbot

Install / Update unstable from `Git source <https://github.com/gpchelkin/scdlbot>`__
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

::

    git clone https://github.com/gpchelkin/scdlbot.git
    cd scdlbot
    pip3 install --requirement requirements.txt

    # Update:
    git pull
    pip3 install --requirement requirements.txt

    # System-wide install link to current sources, recommended:
    python3 setup.py develop

    # System-wide install copy of current sources, not recommended:
    python3 setup.py install

Configuration
~~~~~~~~~~~~~

Download or copy config file sample and set up config environment variables in it:

::

    # PyPI-installed: download sample config:
    curl -o .env https://raw.githubusercontent.com/gpchelkin/scdlbot/master/.env.sample

    # Git source-installed: copy sample config:
    cp .env.sample .env

    # Use your favourite editor:
    nano .env

Required
^^^^^^^^

-  ``TG_BOT_TOKEN``: Telegram Bot API Token, `obtain
   here <https://t.me/BotFather>`__

Optional
^^^^^^^^

-  ``SC_AUTH_TOKEN``: SoundCloud Auth Token, `obtain
   here <https://flyingrub.github.io/scdl/>`__
-  ``STORE_CHAT_ID``: Chat ID for storing audios of inline mode
-  ``USE_WEBHOOK``: use webhook for bot updates: ``1``, use polling
   (default): ``0``, `more info <https://core.telegram.org/bots/api#getting-updates>`__
-  ``APP_URL``: app URL like
   ``https://<appname>.herokuapp.com/``, required for webhook
-  ``PORT``: port for webhook to listen to; Heroku sets this automatically
   for web dynos
-  ``BOTAN_TOKEN``: `Botan.io <http://botan.io/>`__
   `token <http://appmetrica.yandex.com/>`__
-  ``NO_FLOOD_CHAT_IDS``: Comma-separated chat IDs with no replying
   and caption spam
-  ``BIN_PATH``: Custom directory where ``scdl`` and ``bandcamp-dl``
   binaries are available, e.g. ``~/.pyenv/shims/`` if you use pyenv,
   default: empty (binaries are availaible in PATH)
-  ``DL_DIR``: Parent directory for downloads directories, default: /tmp/scdlbot
-  ``DL_TIMEOUT``: Download timeout in seconds, stop downloading if it takes longer than allowed, default: 300
-  ``MAX_CONVERT_FILE_SIZE``: Don't try to split and send files over this number of bytes, default: 80000000
-  ``SYSLOG_ADDRESS``: Syslog server, for example ``logsX.papertrailapp.com:ABCDE``
-  ``SYSLOG_DEBUG``: Enable verbose debug logging: 1
-  ``HOSTNAME``: Hostname to show up in Syslog messages
-  ``GOOGL_API_KEY``: `Goo.gl URL shortener <https://goo.gl>`__
   `API key <https://developers.google.com/url-shortener/v1/getting_started#APIKey>`__

Webhooks: These three links should help. In NGINX use TOKEN1 as TG_BOT_TOKEN without ":" symbol, and port in proxy_pass according to PORT environment variable.

- https://nginx.org/en/linux_packages.html#mainline
- https://github.com/python-telegram-bot/python-telegram-bot/wiki/Webhooks#using-nginx-with-one-domainport-for-all-bots
- https://certbot.eff.org/#ubuntuxenial-nginx

Telegram Bot Settings
^^^^^^^^^^^^^^^^^

Send the commands from respective filenames in ``telegram_settings`` dir to `@BotFather <https://t.me/BotFather>`__, choose your bot and copy corresponding values in order to use the bot conveniently. Also disable privacy mode if you want to.


Running Locally or at Dedicated Server
~~~~~~~~~~~~~~~

Using `Heroku Local <https://devcenter.heroku.com/articles/heroku-local#run-your-app-locally-using-the-heroku-local-command-line-tool>`__ (preferred)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

You will need `Heroku CLI <https://cli.heroku.com/>`__ installed.

::

    # PyPI-installed: download Procfile:
    curl -O https://raw.githubusercontent.com/gpchelkin/scdlbot/master/Procfile

    # For long polling:
    heroku local worker
    # For webhook:
    heroku local web

Using just Python
^^^^^^^^^^^^^^^^^

::

    # PyPI or Git source system-wide installs:
    export $(cat .env | xargs)
    scdlbot
    # or just:
    env $(cat .env | xargs) scdlbot

    # Non-installed Git source repository directory:
    export $(cat .env | xargs)
    python -m scdlbot
    # or just:
    env $(cat .env | xargs) python -m scdlbot


Deploying to `Heroku <https://heroku.com/>`__
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

|Deploy|

Register on Heroku, press the button above and configure variables for deploying.
When app is deployed you **must** set only one dyno working on
"Resources" tab in your app settings depending on `which way of getting
updates <https://core.telegram.org/bots/api#getting-updates>`__ you have
chosen and set in config variables: ``worker`` for polling or ``web``
for webhook.

Manually
^^^^^^^^

You can do the same as the button above but using `Heroku
CLI <https://cli.heroku.com/>`__, not much of a fun. Assuming you are in
``scdbot`` repository directory:

::

    heroku login
    # Create app with Python3 buildpack and set it for upcoming builds:
    heroku create --buildpack heroku/python
    heroku buildpacks:set heroku/python
    # Add FFmpeg buildpack needed for youtube-dl:
    heroku buildpacks:add --index 1 https://github.com/laddhadhiraj/heroku-buildpack-ffmpeg.git --app scdlbot
    # Deploy app to Heroku:
    git push heroku master
    # Set config vars automatically from your .env file
    heroku plugins:install heroku-config
    heroku config:push
    # Or set them one by one:
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
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Use Dokku (your own Heroku) installed on your own server. App is tested and fully
ready for deployment with polling (no webhook yet).
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
