"""URL extraction and validation utilities."""

from __future__ import annotations

import logging
import os
import pathlib
import re
import tempfile
from typing import Any, Dict, List, TypedDict

import httpx
import requests
from boltons.urlutils import URL
from fake_useragent import UserAgent

from scdlbot.download_execution import ydl

# Set up module logger
logger = logging.getLogger(__name__)

# Use the same UserAgent configuration as __main__
UA = UserAgent(browsers=["Google", "Chrome", "Firefox", "Edge"], platforms=["desktop"], os=["Windows", "Linux", "Ubuntu"])


class MessageData(TypedDict):
    """Type definition for extracted message data."""

    url_entities: list[str]
    text_link_entities: list[str]


# Import domain constants from __main__ - these will be passed as parameters
DOMAIN_SC = "soundcloud.com"
DOMAIN_SC_ON = "on.soundcloud.com"
DOMAIN_SC_API = "api.soundcloud.com"
DOMAIN_SC_GOOGL = "soundcloud.app.goo.gl"
DOMAIN_BC = "bandcamp.com"
DOMAIN_YT = "youtube.com"
DOMAIN_YT_BE = "youtu.be"
DOMAIN_YMR = "music.yandex.ru"
DOMAIN_YMC = "music.yandex.com"
DOMAIN_TT = "tiktok.com"
DOMAIN_IG = "instagram.com"
DOMAIN_TW = "twitter.com"
DOMAIN_TWX = "x.com"
DOMAINS_STRINGS = [DOMAIN_SC, DOMAIN_SC_ON, DOMAIN_SC_API, DOMAIN_SC_GOOGL, DOMAIN_BC, DOMAIN_YT, DOMAIN_YT_BE, DOMAIN_YMR, DOMAIN_YMC, DOMAIN_TT, DOMAIN_IG, DOMAIN_TW, DOMAIN_TWX]
DOMAINS = [rf"^(?:[^\s]+\.)?{re.escape(domain_string)}$" for domain_string in DOMAINS_STRINGS]

BLACKLIST_TELEGRAM_DOMAINS = [
    "telegram.org",
    "telegram.me",
    "t.me",
    "telegram.dog",
    "telegra.ph",
    "te.legra.ph",
    "graph.org",
    "tdesktop.com",
    "desktop.telegram.org",
    "telesco.pe",
    "contest.com",
    "contest.dev",
]


def url_valid_and_allowed(url, allow_unknown_sites=False, whitelist_domains=None, blacklist_domains=None):
    host = url.host
    if host in BLACKLIST_TELEGRAM_DOMAINS:
        return False
    if whitelist_domains:
        if host not in whitelist_domains:
            return False
    if blacklist_domains:
        if host in blacklist_domains:
            return False
    if allow_unknown_sites:
        return True
    if any((re.match(domain, host) for domain in DOMAINS)):
        return True
    else:
        return False


async def get_direct_urls_dict(
    message_data: MessageData | Any,
    mode: str,
    proxy: str | None,
    source_ip: str | None,
    allow_unknown_sites: bool = False,
    whitelist_domains: set[str] | None = None,
    blacklist_domains: set[str] | None = None,
) -> dict[str, str]:
    # Log function entry for debugging
    logger.debug(
        "get_direct_urls_dict called with: mode=%s, proxy=%s, source_ip=%s, allow_unknown_sites=%s, message_data type=%s",
        mode,
        proxy,
        source_ip,
        allow_unknown_sites,
        type(message_data).__name__,
    )

    # IMPORTANT: Handle case where message_data might be incorrectly serialized
    if not isinstance(message_data, dict):
        logger.error("message_data is not a dict but %s: %s", type(message_data).__name__, message_data)
        return {}

    # Extract URLs from the pre-parsed message data
    urls = []

    # Process regular URL entities
    for url_str in message_data.get("url_entities", []):
        if "://" not in url_str:
            url_str = "http://" + url_str
        try:
            url = URL(url_str)
            if url_valid_and_allowed(url, allow_unknown_sites=allow_unknown_sites, whitelist_domains=whitelist_domains, blacklist_domains=blacklist_domains):
                logger.info("Entity URL parsed: %s", url)
                urls.append(url)
            else:
                logger.info("Entity URL is not valid or blacklisted: %s", url_str)
        except:
            logger.info("Entity URL is not valid: %s", url_str)

    # Process text link entities
    for entity_url in message_data.get("text_link_entities", []):
        try:
            url = URL(entity_url)
            if url_valid_and_allowed(url, allow_unknown_sites=allow_unknown_sites, whitelist_domains=whitelist_domains, blacklist_domains=blacklist_domains):
                logger.info("Entity Text Link parsed: %s", url)
                urls.append(url)
            else:
                logger.info("Entity Text Link is not valid or blacklisted: %s", url)
        except:
            logger.info("Entity Text Link is not valid: %s", entity_url)
    # If message just some text passed (not isinstance(message, Message)):
    # all_links = find_all_links(message, default_scheme="http")
    # urls = [link for link in all_links if url_valid_and_allowed(link)]
    logger.info(f"prepare_urls: urls list: {urls}")

    urls_dict = {}
    for url_item in urls:
        unknown_site = not any((re.match(domain, url_item.host) for domain in DOMAINS))
        # Unshorten soundcloud.app.goo.gl and unknown sites links. Example: https://soundcloud.app.goo.gl/mBMvG
        # FIXME spotdl to transform spotify link to youtube music link?
        # TODO Unshorten unknown sites links again? Because yt-dlp may only support unshortened?
        # if unknown_site or DOMAIN_SC_GOOGL in url_item.host:
        if DOMAIN_SC_GOOGL in url_item.host or DOMAIN_SC_ON in url_item.host:
            async with httpx.AsyncClient(proxy=proxy if proxy else None, timeout=2.0, follow_redirects=True) as client:
                try:
                    response = await client.head(
                        url_item.to_text(full_quote=True),
                        headers={"User-Agent": UA.random},
                    )
                    url = URL(str(response.url))
                except:
                    url = url_item
        else:
            url = url_item
        unknown_site = not any((re.match(domain, url.host) for domain in DOMAINS))
        url_text = url.to_text(full_quote=True)
        logger.debug(f"Unshortened link: {url_text}")
        # url_text = url_text.replace("m.soundcloud.com", "soundcloud.com")
        url_parts_num = len([part for part in url.path_parts if part])
        if unknown_site or mode == "link":
            # We run it if it was explicitly requested as per "link" mode.
            # We run it for links from unknown sites (if they were allowed).
            # FIXME For now we avoid extra requests on asking just to improve responsiveness. We are okay with useless asking (for unknown sites). Link mode might be removed.
            # If it's a known site, we check it more thoroughly below.
            # urls_dict[url_text] = ydl_get_direct_urls(url_text, COOKIES_FILE, source_ip, proxy)
            urls_dict[url_text] = "http"
        elif (
            ((DOMAIN_SC in url.host) and (2 <= url_parts_num <= 4) and (not "you" in url.path_parts) and (not "likes" in url.path_parts))
            or (DOMAIN_SC_GOOGL in url.host)
            or (DOMAIN_SC_API in url.host)
        ):
            # SoundCloud: tracks, sets and widget pages, no /you/ pages
            # TODO support private sets URLs that have 5 parts
            # We know for sure these links can be downloaded, so we just skip running ydl_get_direct_urls
            urls_dict[url_text] = "http"
        elif DOMAIN_BC in url.host and (2 <= url_parts_num <= 2):
            # Bandcamp: tracks and albums
            # We know for sure these links can be downloaded, so we just skip running ydl_get_direct_urls
            urls_dict[url_text] = "http"
        elif ((DOMAIN_YT in url.host) and ("watch" in url.path or "playlist" in url.path)) or (DOMAIN_YT_BE in url.host):
            # YouTube: videos and playlists
            # We still run it for checking YouTube region restriction to avoid useless asking.
            # FIXME For now we avoid extra requests on asking just to improve responsiveness. We are okay with useless asking (for youtube).
            # urls_dict[url_text] = ydl_get_direct_urls(url_text, COOKIES_FILE, source_ip, proxy)
            urls_dict[url_text] = "http"
        elif DOMAIN_YMR in url.host or DOMAIN_YMC in url.host:
            # YM: tracks. Note that the domain includes x.com..
            # We know for sure these links can be downloaded, so we just skip running ydl_get_direct_urls
            urls_dict[url_text] = "http"
        elif DOMAIN_TT in url.host:
            # TikTok: videos
            # We know for sure these links can be downloaded, so we just skip running ydl_get_direct_urls
            urls_dict[url_text] = "http"
        elif DOMAIN_IG in url.host and (2 <= url_parts_num):
            # Instagram: videos, reels
            # We run it for checking Instagram ban to avoid useless asking.
            # FIXME For now we avoid extra requests on asking just to improve responsiveness. We are okay with useless asking (for instagram).
            # urls_dict[url_text] = ydl_get_direct_urls(url_text, COOKIES_FILE, source_ip, proxy)
            urls_dict[url_text] = "http"
        elif (DOMAIN_TW in url.host or DOMAIN_TWX in url.host) and (DOMAIN_YMC not in url.host) and (3 <= url_parts_num <= 3):
            # Twitter: videos
            # We know for sure these links can be downloaded, so we just skip running ydl_get_direct_urls
            urls_dict[url_text] = "http"
    return urls_dict


def ydl_get_direct_urls(url, cookies_file=None, source_ip=None, proxy=None):
    # TODO transform into unified ydl function and deduplicate
    logger.debug("Entering: ydl_get_direct_urls: %s", url)

    # Import DL_DIR from environment or use default
    DL_DIR = os.path.expanduser(os.getenv("DL_DIR", "/tmp/scdlbot"))

    status = ""
    cmd_name = "ydl_get_direct_urls"
    ydl_opts = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "skip_download": True,
        # IMPORTANT: Set cache directory to a writable location
        "cachedir": os.path.join(DL_DIR, ".cache"),
        # "forceprint": {"before_dl":}
    }
    if proxy:
        ydl_opts["proxy"] = proxy
    if source_ip:
        ydl_opts["source_address"] = source_ip
    cookies_download_file = None
    if cookies_file:
        cookies_download_file = tempfile.NamedTemporaryFile(mode="wb", delete=False)
        cookies_download_file_path = pathlib.Path(cookies_download_file.name)
        if cookies_file.startswith("http"):
            # URL for downloading cookie file:
            try:
                r = requests.get(cookies_file, allow_redirects=True, timeout=5)
                cookies_download_file.write(r.content)
                cookies_download_file.close()
                ydl_opts["cookiefile"] = str(cookies_download_file_path)
            except:
                logger.debug("download_url_and_send could not download cookies file")
                pass
        elif cookies_file.startswith("firefox:"):
            # TODO handle env var better
            cookies_file_components = cookies_file.split(":", maxsplit=2)
            if len(cookies_file_components) == 3:
                cookies_sqlite_file = cookies_file_components[2]
                cookies_download_sqlite_path = pathlib.Path.home() / ".mozilla" / "firefox" / cookies_file_components[1] / "cookies.sqlite"
                # URL for downloading cookie sqlite file:
                try:
                    r = requests.get(cookies_sqlite_file, allow_redirects=True, timeout=5)
                    with open(cookies_download_sqlite_path, "wb") as cfile:
                        cfile.write(r.content)
                    ydl_opts["cookiesfrombrowser"] = ("firefox", cookies_file_components[1], None, None)
                    logger.debug("download_url_and_send downloaded cookies.sqlite file")
                except:
                    logger.debug("download_url_and_send could not download cookies.sqlite file")
                    pass
            else:
                ydl_opts["cookiesfrombrowser"] = ("firefox", cookies_file_components[1], None, None)
        else:
            # cookie file local path:
            cookies_download_file.write(open(cookies_file, "rb").read())
            cookies_download_file.close()
            ydl_opts["cookiefile"] = str(cookies_download_file_path)

    logger.debug("%s starts: %s", cmd_name, url)
    try:
        # https://github.com/yt-dlp/yt-dlp/blob/master/README.md#embedding-examples
        unsanitized_info_dict = ydl.YoutubeDL(ydl_opts).extract_info(url, download=False)
        info_dict = ydl.YoutubeDL(ydl_opts).sanitize_info(unsanitized_info_dict)
        # TODO actualize checks, fix for youtube playlists
        if "url" in info_dict:
            direct_url = info_dict["url"]
        elif "entries" in info_dict:
            direct_url = "\n".join([x["url"] for x in info_dict["entries"] if "url" in x])
        else:
            raise Exception()
        if "yt_live_broadcast" in direct_url:
            status = "restrict_live"
        elif "returning it as such" in direct_url:
            status = "restrict_direct"
        elif "proxy server" in direct_url:
            status = "restrict_region"
        # end actualize checks
        else:
            status = direct_url
            logger.debug("%s succeeded: %s", cmd_name, url)
    except Exception:
        import traceback
        logger.debug("%s failed: %s", cmd_name, url)
        logger.debug(traceback.format_exc())
        status = "failed"
    if cookies_file and cookies_download_file is not None:
        cookies_download_file.close()
        os.unlink(cookies_download_file.name)

    return status