"""User-facing texts (HTML parse mode). Long ones live in texts/*.html."""

import random
from importlib import resources


def _load(name: str) -> str:
    return resources.files("scdlbot").joinpath("texts", name).read_text(encoding="utf-8").strip()


HELP = _load("help.html")
SETTINGS = _load("settings.html")
PRIVACY = _load("privacy.html")

WAIT = ("⏳ Wait a bit..", "⏳ Wait a beat..", "⏳ Wait a beet..")
ASK = "🎶 links found, what to do?"
NO_URLS = (
    "<b>Sorry</b>, no supported links were found in your request, or the website just didn't let me download from it..\n"
    "Just wait and try again later.\nFor more information see /help"
)
OLD_MESSAGE = "Sorry, very old original message with links, resend please."
NOT_ADMIN = "You're not an admin of this chat."
NOT_ALLOWED = "This command isn't allowed in this chat."
BUSY = "<b>Sorry</b>, I'm overloaded right now. Please try again in a few minutes."
USER_LIMIT = "<b>Sorry</b>, you already have {count} downloads in progress, please wait for them to finish."
CAPPED = "I sent only the first {sent} of {total} items from this link."
SETTINGS_CHANGED = "Settings changed"
SETTINGS_NOT_CHANGED = "Settings not changed"

# By ytdlp.ERRORS codes, plus "timeout", "partial" and "failed":
ERRORS = {
    "failed": (
        "<b>Sorry</b>, something went wrong. A team of highly trained 🐝🐝🐝 has been dispatched to deal with this situation, "
        "but I recommend you to double-check if your link is alive and supported, see /help"
    ),
    "partial": "<b>Sorry</b>, I couldn't download or send some of the items from this link.",
    "timeout": "<b>Sorry</b>, your download was taking too much time, so I needed to stop it.",
    "too_large": "<b>Sorry</b>, this file is too large for me (more than {max_mb} MB).",
    "live": "<b>Sorry</b>, I do not download live streams.",
    "drm": "<b>Sorry</b>, this track is DRM-protected (e.g. SoundCloud Go+ only), so I can't download it.",
    "no_media": "<b>Sorry</b>, there's no video in this post, and I couldn't get its photos or text right now. Please try again later.",
    "region": "<b>Sorry</b>, the uploader has not made this link available in my country.",
    "restricted": "<b>Sorry</b>, this is age-restricted or limited to certain audiences, so the website won't give it to me.",
    "login": "<b>Sorry</b>, the website wants me to log in to download this.",
    "blocked": "<b>Sorry</b>, the website is limiting my downloads right now. Please try again later.",
    "network": "<b>Sorry</b>, I couldn't connect to the website. Please try again later.",
    "unavailable": "<b>Sorry</b>, this link is unavailable: it may be private, deleted or never existed.",
    "unsupported": "<b>Sorry</b>, I can't download from this link, see /help for supported sites.",
}


def wait() -> str:
    return f"<i>{random.choice(WAIT)}</i>"
