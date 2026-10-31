# Privacy Policy of @scdlbot

Last updated: 2026-10-30

This policy covers only the Telegram bot [@scdlbot](https://t.me/scdlbot), run by George Pchelkin ([@gpchelkin](https://t.me/gpchelkin)).
It replaces Telegram's [Standard Privacy Policy for bots](https://telegram.org/privacy-tpa).

Other bots built from [scdlbot's open-source code](https://github.com/gpchelkin/scdlbot) are run by other people, with their own settings.
They are responsible for those bots and their privacy policies; this policy doesn't apply to them.

## What the bot stores

The bot keeps only what it needs to download links and to count its users:

| Data | Why | How long |
|---|---|---|
| Your Telegram user ID and the language of your Telegram app (e.g. `en`), for each day you use the bot | Number of daily, weekly and monthly users, and their app languages | Indefinitely |
| Each supported link you send, with your user ID, the chat ID, the message ID and the result of the download | The download queue (so downloads survive a restart), limits per user, finding problems with sites | 90 days |
| Chat ID and type (private, group, channel) and the chat's /settings | Remembering your settings | Indefinitely |
| Server logs, including links and chat IDs | Finding and fixing errors | About one month, then deleted automatically |

The bot does **not** store:

- The text of your messages. In groups the bot sees every message, but only looks for links in them.
- Your name, username or phone number.
- The files it downloads: each file is deleted from the server right after it's sent to Telegram.
  Files sent to you stay in your Telegram chat, like any other message.

Statistics derived from this data (e.g. number of downloads per site and day) contain no user or chat IDs and are kept indefinitely.

## Who gets the data

- **Nobody else.** The bot doesn't use analytics, advertising or tracking services and doesn't sell or share data.
- **Websites you link to** receive a request for that link from the bot's server, directly or through a proxy server.
  They get only the link, never anything about you.
- **Telegram** delivers all messages and files, under [Telegram's Privacy Policy](https://telegram.org/privacy).
- Error reports, which may include a link, go to the creator's own Telegram account.

## Your rights

You can ask for a copy of your data, or for its deletion, by writing to [@gpchelkin](https://t.me/gpchelkin) from your
Telegram account. Requests are answered within 30 days. You can stop using the bot at any time by blocking it.

## Changes

Changes to this policy are published in this file; [its history](https://github.com/gpchelkin/scdlbot/commits/master/PRIVACY.md) shows every change.
