"""Find links in Telegram messages and decide what (and whether) to download."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import httpx
from telegram import Message, MessageEntity

from scdlbot.config import Config

log = logging.getLogger(__name__)

AUDIO, VIDEO = "audio", "video"


@dataclass(frozen=True)
class Site:
    name: str
    domains: tuple[str, ...]
    kind: str = AUDIO
    path_ok: Callable[[list[str]], bool] = lambda parts: True
    shortener: bool = False  # resolve redirects before downloading, then classify again
    pool: str = "main"  # worker pool, see jobs.JobQueue


def _sc_path(parts: list[str]) -> bool:
    # Tracks, sets, private links and user pages, but not "/you/..." or likes:
    return 2 <= len(parts) <= 5 and not {"you", "likes", "discover", "search", "stream"} & set(parts)


def _yt_path(parts: list[str]) -> bool:
    return bool(parts) and parts[0] in ("watch", "playlist", "shorts")


# Known sites. The first matching row wins, so keep shorteners above their main domain.
# To support a new site, add a row; anything yt-dlp supports works with "Unknown sites" enabled anyway.
SITES = (
    Site("SoundCloud", ("on.soundcloud.com",), shortener=True),
    Site("SoundCloud", ("api.soundcloud.com",)),
    Site("SoundCloud", ("soundcloud.com",), path_ok=_sc_path),
    Site("Bandcamp", ("bandcamp.com",), path_ok=lambda p: len(p) == 2),
    Site("YouTube", ("youtu.be",), pool="youtube"),
    Site("YouTube", ("youtube.com",), path_ok=_yt_path, pool="youtube"),
    Site("Yandex Music", ("music.yandex.ru", "music.yandex.com")),
    Site("TikTok", ("tiktok.com",), kind=VIDEO),
    Site("Instagram", ("instagram.com",), kind=VIDEO, path_ok=lambda p: len(p) >= 2),
    Site("X", ("x.com", "twitter.com"), kind=VIDEO, path_ok=lambda p: len(p) == 3),
    Site("X", ("t.co",), shortener=True),
)

TELEGRAM_DOMAINS = frozenset(
    ["telegram.org", "telegram.me", "t.me", "telegram.dog", "telegra.ph", "te.legra.ph", "graph.org", "tdesktop.com", "telesco.pe", "contest.com", "contest.dev"]
)
IGNORED_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".bmp", ".pdf", ".txt", ".html")


@dataclass(frozen=True)
class Link:
    url: str
    site: str
    kind: str
    shortener: bool = False


def _host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def classify(url: str, cfg: Config, unknown_sites: bool) -> Link | None:
    """Return a Link if we should try to download this URL, None otherwise."""
    if "://" not in url:
        url = "https://" + url
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    host = (parts.hostname or "").lower().removeprefix("www.")
    if parts.scheme not in ("http", "https") or not host or "." not in host:
        return None
    if any(_host_matches(host, d) for d in TELEGRAM_DOMAINS):
        return None
    if cfg.whitelist_domains and host not in cfg.whitelist_domains:
        return None
    if host in cfg.blacklist_domains:
        return None
    if parts.path.lower().endswith(IGNORED_EXTENSIONS):
        return None
    url = urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, parts.query, ""))  # drop #fragment
    path_parts = [p for p in parts.path.split("/") if p]
    for site in SITES:
        if any(_host_matches(host, d) for d in site.domains):
            if site.path_ok(path_parts):
                return Link(url, site.name, site.kind, site.shortener)
            break
    if unknown_sites:
        return Link(url, host, AUDIO)
    return None


def find_links(message: Message, cfg: Config, unknown_sites: bool) -> list[Link]:
    """All downloadable links from message text and caption, deduplicated, in order."""
    urls = []
    types = [MessageEntity.URL, MessageEntity.TEXT_LINK]
    for entities in (message.parse_entities(types), message.parse_caption_entities(types)):
        for entity, text in entities.items():
            urls.append(entity.url if entity.type == MessageEntity.TEXT_LINK else text)
    links, seen = [], set()
    for url in urls:
        link = classify(url, cfg, unknown_sites)
        if link and link.url not in seen:
            seen.add(link.url)
            links.append(link)
    return links


async def resolve(link: Link, cfg: Config, client: httpx.AsyncClient, unknown_sites: bool) -> Link | None:
    """Follow redirects of a short link (t.co, on.soundcloud.com) and classify where it leads."""
    try:
        response = await client.head(link.url, follow_redirects=True, timeout=5)
    except httpx.HTTPError as exc:
        log.info("Could not resolve %s: %s", link.url, exc)
        return link  # yt-dlp may still manage it
    resolved = classify(str(response.url), cfg, unknown_sites)
    return link if resolved and resolved.shortener else resolved
