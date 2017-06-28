=============================
Music Downloader Telegram Bot
=============================

| |PyPI version| |Updates| |GitHub license| |Telegram Bot|


.. contents:: :depth: 2

Bot Usage
---------

Send ``/start`` or ``/help`` command to bot or refer directly to the `help message <scdlbot/messages/help.tg.md>`__.

Please report all bugs and issues and suggest your improvements to `issues <https://github.com/gpchelkin/scdlbot/issues>`__.

Supported sites and used packages
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

-  `Telegram Bot API <https://core.telegram.org/bots/api>`__:
   `python-telegram-bot <https://github.com/python-telegram-bot/python-telegram-bot>`__
-  `SoundCloud <https://soundcloud.com>`__:
   `scdl <https://github.com/flyingrub/scdl>`__
-  `Bandcamp <https://bandcamp.com>`__:
   `bandcamp-dl <https://github.com/iheanyi/bandcamp-dl>`__
-  `YouTube <https://www.youtube.com/>`__,
   `Mixcloud <https://www.mixcloud.com/>`__, etc.:
   `youtube-dl <https://rg3.github.io/youtube-dl>`__

Development
-----------

Installation
~~~~~~~~~~~~

Requirements
^^^^^^^^^^^^

Those should be available in your ``PATH``:

-  `Python 3.4+ <https://www.python.org/>`__
   (`pyenv <https://github.com/pyenv/pyenv>`__ recommended)
-  `FFmpeg <https://ffmpeg.org/download.html>`__ for running locally
   (fresh builds for `Windows <https://ffmpeg.zeranoe.com/builds/>`__
   and `Linux <https://johnvansickle.com/ffmpeg/>`__ recommended)
-  `Heroku CLI <https://cli.heroku.com/>`__ is recommended

Install from `PyPI <https://pypi.python.org/pypi/scdlbot>`__ (preferred)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

::

    pip3 install scdlbot

Install from `Git source <https://github.com/gpchelkin/scdlbot>`__
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

::

    git clone https://github.com/gpchelkin/scdlbot.git
    cd scdlbot
    pip3 install --requirement requirements.txt

    # If you want to install system-wide, not recommended:
    python3 setup.py install
    # Install only a link to current project sources:
    python3 setup.py develop

Configuration
~~~~~~~~~~~~~

Copy config file sample and set up config environment variables in it:

::

    # if you installed from PyPI, download sample:
    wget https://raw.githubusercontent.com/gpchelkin/scdlbot/master/.env.sample

    cp .env.sample .env
    nano .env

Required
^^^^^^^^

-  ``TG_BOT_TOKEN``: Telegram Bot API Token, `obtain
   here <https://t.me/BotFather>`__, also diable privacy mode if you
   want
-  ``STORE_CHAT_ID``: Chat ID for storing audios for inline mode
-  ``SC_AUTH_TOKEN``: SoundCloud Auth Token, `obtain
   here <https://flyingrub.github.io/scdl/>`__

Optional
^^^^^^^^

-  ``USE_WEBHOOK``: use webhook for bot updates: ``1``, use polling
   (default): ``0``, `more
   info <https://core.telegram.org/bots/api#getting-updates>`__
-  ``PORT``: Heroku sets this automatically for web dynos if you are
   using webhook
-  ``APP_URL``: Heroku App URL like
   ``https://<appname>.herokuapp.com/``, required for webhook
-  ``BOTAN_TOKEN``: `Botan.io <http://botan.io/>`__
   `token <http://appmetrica.yandex.com/>`__
-  ``NO_CLUTTER_CHAT_IDS``: Comma-separated chat IDs with no replying
   and caption hashtags
-  ``BIN_PATH``: Custom directory where ``scdl`` and ``bandcamp-dl``
   binaries are available, e.g. ``~/.pyenv/shims/`` if you use pyenv,
   default: empty
-  ``DL_DIR``: Parent directory for MP3 download directory, default: ~
   (user's home directory)
-  ``SYSLOG_ADDRESS``: Syslog server, for example ``logsX.papertrailapp.com:ABCDE``
-  ``HOSTNAME``: Hostname to show up in Syslog messages

Running Locally
~~~~~~~~~~~~~~~

Using `Heroku Local <https://devcenter.heroku.com/articles/heroku-local#run-your-app-locally-using-the-heroku-local-command-line-tool>`__ (preferred)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

You will need `Heroku CLI <https://cli.heroku.com/>`__ installed.

::

    # if you installed from PyPI, download Procfile:
    wget https://raw.githubusercontent.com/gpchelkin/scdlbot/master/Procfile

    # for long polling:
    heroku local worker
    # for webhooks (you will also need to set up some NGINX with SSL):
    heroku local web

Using just Python
^^^^^^^^^^^^^^^^^

::

    # if you installed from PyPI or system-wide:
    export $(cat .env | xargs)
    scdlbot
    # or just:
    env $(cat .env | xargs) scdlbot

    # if you haven't installed from PyPI or system-wide:
    export $(cat .env | xargs)
    python -m scdlbot
    # or just:
    env $(cat .env | xargs) python -m scdlbot


Deploying to `Heroku <https://heroku.com/>`__
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

|Deploy|

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

Use Dokku and their docs on your own server. App is tested and fully
ready for deployment with polling (no webhook yet).

::

    export DOKKU=<your_dokku_server>
    scp .env $DOKKU:~
    ssh $DOKKU
    dokku apps:create scdlbot
    dokku config:set scdlbot $(cat .env | xargs)
    # Ctrl+D
    git remote add dokku dokku@$DOKKU:scdlbot
    git push dokku master
    ssh $DOKKU
    dokku ps:scale scdlbot worker=1 web=0
    dokku ps:restart

.. |PyPI version| image:: https://badge.fury.io/py/scdlbot.svg
    :target: https://pypi.org/project/scdlbot
.. |Updates| image:: https://pyup.io/repos/github/gpchelkin/scdlbot/shield.svg?token=376ffde2-5188-4912-bf3c-5f316e52d43f
    :target: https://pyup.io/repos/github/gpchelkin/scdlbot/
.. |GitHub license| image:: https://img.shields.io/badge/license-GPLv3-green.svg
    :target: https://raw.githubusercontent.com/gpchelkin/scdlbot/master/LICENSE.txt
.. |Telegram Bot| image:: https://img.shields.io/badge/telegram-bot-blue.svg
    :target: https://t.me/scdlbot
.. |Deploy| image:: https://www.herokucdn.com/deploy/button.svg
    :target: https://heroku.com/deploy
