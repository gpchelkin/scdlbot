"""Find links in Telegram messages and decide what (and whether) to download."""

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import httpx
from telegram import Message, MessageEntity

from scdlbot.config import Config

log = logging.getLogger(__name__)

AUDIO, VIDEO, PHOTO = "audio", "video", "photo"


@dataclass(frozen=True)
class Author:
    """Which yt-dlp fields hold the author of a post on a site, for "Author: Name (@handle)" in captions."""

    name: str  # field with the display name
    handle: str  # field with the @handle
    url: str  # profile URL, {} is replaced by the handle


@dataclass(frozen=True)
class Site:
    name: str
    domains: tuple[str, ...]
    kind: str = AUDIO
    path_ok: Callable[[list[str]], bool] = lambda parts: True
    shortener: bool = False  # resolve redirects before downloading, then classify again
    pool: str = "main"  # worker pool, see jobs.JobQueue
    author: Author | None = None
    photos: bool = False  # posts may have photos too (yt-dlp only downloads videos by default)
    # The post's link without share tracking, from yt-dlp's webpage_url and the author's @handle (NORMALIZE_URLS).
    # None: yt-dlp's webpage_url is clean already (YouTube, SoundCloud) or we can't tell what's junk (unknown sites).
    clean_url: Callable[[str, str | None], str] | None = None


def _sc_path(parts: list[str]) -> bool:
    # Tracks, sets, private links and user pages (tracks, reposts, likes: capped by MAX_PLAYLIST_ITEMS like any playlist),
    # but not "/you/..." (the logged-in user's own pages) or site sections:
    return 2 <= len(parts) <= 5 and not {"you", "discover", "search", "stream"} & set(parts)


def _yt_path(parts: list[str]) -> bool:
    return bool(parts) and parts[0] in ("watch", "playlist")


def _yt_shorts_path(parts: list[str]) -> bool:
    return len(parts) >= 2 and parts[0] == "shorts"


def _twitter_path(parts: list[str]) -> bool:
    # /user/status/123, also with /video/1 or /photo/1 after it (that's where t.co links lead):
    return len(parts) >= 3 and parts[1] == "status"


def _ig_path(parts: list[str]) -> bool:
    # Posts, reels and stories, but not pages of a reel's audio track:
    return len(parts) >= 2 and parts[:2] != ["reels", "audio"]


def _without_query(url: str, handle: str | None = None) -> str:
    """Posts on these sites are identified by the path; the query is share tracking (?igsh=..., ?s=20&t=..., ?_r=1&_t=...)."""
    return urlunsplit(urlsplit(url)._replace(query="", fragment=""))


def _twitter_url(url: str, handle: str | None = None) -> str:
    return re.sub(r"/(?:photo|video)/\d+/?$", "", _without_query(url))  # the tweet, not its media #N


def _tiktok_url(url: str, handle: str | None = None) -> str:
    # Short links (vt.tiktok.com/...) lead to "/@/video/ID?u_code=...&share_item_id=...": no user, and the sharer's codes
    if handle and (match := re.match(r"/@[^/]*/(video|photo)/(\d+)", urlsplit(url).path)):
        return f"https://www.tiktok.com/@{handle}/{match[1]}/{match[2]}"
    return _without_query(url)


# Known sites. For each link the first row whose domain and path match wins, so keep shorteners above their main domain.
# To support a new site, add a row; anything yt-dlp supports works with "Unknown sites" enabled anyway.
SITES = (
    Site("SoundCloud", ("on.soundcloud.com",), shortener=True),
    Site("SoundCloud", ("api.soundcloud.com",)),
    Site("SoundCloud", ("soundcloud.com",), path_ok=_sc_path),
    Site("Bandcamp", ("bandcamp.com",), path_ok=lambda p: len(p) == 2),
    Site("YouTube", ("youtu.be",), pool="youtube"),
    # Shorts are videos, like reels (shorts behind other YouTube links are re-downloaded as videos, see jobs):
    Site("YouTube", ("youtube.com",), kind=VIDEO, path_ok=_yt_shorts_path, pool="youtube"),
    Site("YouTube", ("youtube.com",), path_ok=_yt_path, pool="youtube"),
    Site("Yandex Music", ("music.yandex.ru", "music.yandex.com")),
    Site("TikTok", ("tiktok.com",), kind=VIDEO, author=Author("channel", "uploader", "https://www.tiktok.com/@{}"), clean_url=_tiktok_url),
    Site(
        "Instagram",
        ("instagram.com",),
        kind=VIDEO,
        path_ok=_ig_path,
        photos=True,
        author=Author("uploader", "channel", "https://www.instagram.com/{}/"),
        clean_url=_without_query,
    ),
    Site(
        "Twitter",
        ("twitter.com", "x.com"),
        kind=VIDEO,
        path_ok=_twitter_path,
        author=Author("uploader", "uploader_id", "https://twitter.com/{}"),
        clean_url=_twitter_url,
    ),
    Site("Twitter", ("t.co",), kind=VIDEO, shortener=True),  # video if it can't be resolved: that's what t.co links in posts mostly are
)

TELEGRAM_DOMAINS = frozenset(
    ["telegram.org", "telegram.me", "t.me", "telegram.dog", "telegra.ph", "te.legra.ph", "graph.org", "tdesktop.com", "telesco.pe", "contest.com", "contest.dev"]
)
IGNORED_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".bmp", ".pdf", ".txt", ".html")


def site_by_name(name: str) -> Site | None:
    """The main row of a known site (not its shortener), None for unknown sites."""
    return next((site for site in SITES if site.name == name and not site.shortener), None)


def clean_url(url: str, site: str, handle: str | None = None) -> str:
    """A post's link for captions, without share tracking that identifies who shared it (see Site.clean_url)."""
    known = site_by_name(site)
    return known.clean_url(url, handle) if known and known.clean_url else url


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
    sites = [site for site in SITES if any(_host_matches(host, d) for d in site.domains)]
    if site := next((site for site in sites if site.path_ok(path_parts)), None):
        return Link(url, site.name, site.kind, site.shortener)
    if unknown_sites and not sites:  # a known site's rules apply even when unknown sites are allowed
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


# t.co answers browsers with a JavaScript page instead of a redirect, but gives a plain command-line tool a real one.
# Other short links redirect fine with the usual browser user agent of our HTTP client.
RESOLVER_USER_AGENTS = {"t.co": "curl/8.5.0"}


async def resolve(link: Link, cfg: Config, client: httpx.AsyncClient, unknown_sites: bool) -> Link | None:
    """Follow redirects of a short link (t.co, on.soundcloud.com) and classify where it leads."""
    try:
        user_agent = RESOLVER_USER_AGENTS.get(urlsplit(link.url).hostname or "")
        response = await client.head(link.url, follow_redirects=True, timeout=5, headers={"User-Agent": user_agent} if user_agent else None)
    except httpx.HTTPError as exc:
        log.info("Could not resolve %s: %s", link.url, exc)
        return link  # yt-dlp may still manage it
    resolved = classify(str(response.url), cfg, unknown_sites)
    return link if resolved and resolved.shortener else resolved
