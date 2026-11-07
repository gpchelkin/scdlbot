"""Tweets through Twitter's embed endpoint, the one embedded tweets on websites use: no login needed.

yt-dlp only downloads videos from tweets; this gives the photos and the text of the others.
"""

import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from scdlbot.links import PHOTO
from scdlbot.media import Item
from scdlbot.ytdlp import DownloadError

log = logging.getLogger(__name__)

EMBED_API = "https://cdn.syndication.twimg.com/tweet-result"
PROFILE_URL = "https://twitter.com/{}"


@dataclass
class Tweet:
    text: str
    author_name: str | None
    handle: str | None
    photos: list[str] = field(default_factory=list)
    has_video: bool = False  # videos and GIFs: yt-dlp does those


def tweet_id(url: str) -> str | None:
    match = re.search(r"/status(?:es)?/(\d+)", url)
    return match.group(1) if match else None


def for_ytdlp(url: str) -> str:
    """yt-dlp reads ".../photo/1" as "media #1 must be a video" and fails, e.g. for GIFs shared from the photo viewer."""
    return re.sub(r"/photo/\d+/?(?=$|\?)", "", url)


def _base36(number: float, digits: int = 12) -> str:
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    fraction, integer = math.modf(number)
    whole = ""
    integer = int(integer)
    while integer:
        integer, digit = divmod(integer, 36)
        whole = alphabet[digit] + whole
    tail = ""
    while fraction and len(tail) < digits:
        fraction, digit = math.modf(fraction * 36)
        tail += alphabet[int(digit)]
    return (whole or "0") + ("." + tail if tail else "")


def _token(twid: str) -> str:
    # What the embed widget sends: ((id / 1e15) * Math.PI).toString(36) without zeros and dots
    return re.sub(r"[0.]", "", _base36(int(twid) / 1e15 * math.pi))


def _text(status: dict) -> str:
    text = status.get("text") or ""
    start, end = (status.get("display_text_range") or [0, len(text)])[:2]
    text = text[start:end]  # drops the trailing t.co link to the photos
    for url in (status.get("entities") or {}).get("urls") or []:
        if url.get("url") and url.get("expanded_url"):
            text = text.replace(url["url"], url["expanded_url"])
    return text.strip()


async def fetch(url: str, client: httpx.AsyncClient) -> Tweet | None:
    """The tweet behind a link; None if Twitter can't be asked right now (let yt-dlp try instead).

    Raises DownloadError for deleted, protected or restricted tweets.
    """
    if not (twid := tweet_id(url)):
        return None
    try:
        response = await client.get(EMBED_API, params={"id": twid, "token": _token(twid)}, headers={"User-Agent": "Googlebot"}, timeout=15)
        status = response.json() if response.content else {}
    except (httpx.HTTPError, ValueError) as exc:
        log.info("Twitter embed endpoint failed for %s: %s", twid, exc)
        return None
    if response.status_code == 404 or status.get("__typename") == "TweetTombstone":
        reason = str(((status.get("tombstone") or {}).get("text") or {}).get("text") or "")
        raise DownloadError("restricted" if "age-restricted" in reason.lower() or "adult" in reason.lower() else "unavailable", reason)
    if response.status_code != 200 or not status.get("user"):
        log.info("Twitter embed endpoint gave HTTP %s for %s", response.status_code, twid)
        return None
    media = status.get("mediaDetails") or (status.get("quoted_tweet") or {}).get("mediaDetails") or []
    user = status["user"]
    return Tweet(
        text=_text(status),
        author_name=user.get("name"),
        handle=user.get("screen_name"),
        photos=[m["media_url_https"] for m in media if m.get("type") == "photo" and m.get("media_url_https")],
        has_video=any(m.get("type") in ("video", "animated_gif") for m in media),
    )


def text_item(tweet: Tweet, job_dir: Path) -> Item:
    """A text-only tweet, as an item without a file."""
    return Item(job_dir / "tweet.txt", "text", **_author(tweet), description=tweet.text)


async def photo_items(tweet: Tweet, job_dir: Path, client: httpx.AsyncClient) -> list[Item]:
    """Download the photos of a tweet, in their order. Several of them go out as an album."""
    items = []
    for number, url in enumerate(tweet.photos, start=1):
        path = job_dir / f"{number:02d}{Path(urlsplit(url).path).suffix or '.jpg'}"
        try:
            response = await client.get(url, params={"name": "large"}, timeout=30)  # up to 2048 px, plenty for Telegram
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log.info("Could not download tweet photo %s: %s", url, exc)
            continue
        path.write_bytes(response.content)
        index = number if len(tweet.photos) > 1 else None
        items.append(Item(path, PHOTO, **_author(tweet), description=tweet.text, playlist_index=index, playlist_count=len(tweet.photos)))
    if not items:
        raise DownloadError("failed", "could not download the photos of the tweet")
    return items


def _author(tweet: Tweet) -> dict:
    return {"author_name": tweet.author_name, "author_handle": tweet.handle, "author_url": PROFILE_URL.format(tweet.handle) if tweet.handle else None}
