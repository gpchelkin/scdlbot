from datetime import UTC, datetime

import pytest
from telegram import Chat, Message, MessageEntity

from scdlbot.config import Config
from scdlbot.links import AUDIO, VIDEO, classify, find_links, resolve

CFG = Config()


@pytest.mark.parametrize(
    ("url", "site", "kind"),
    [
        ("https://soundcloud.com/falling-ethics/jk-flesh-static-demon", "SoundCloud", AUDIO),
        ("m.soundcloud.com/artist/sets/album", "SoundCloud", AUDIO),
        ("https://soundcloud.com/artist/likes", "SoundCloud", AUDIO),
        ("https://soundcloud.com/artist/sets/album/s-PrIvAtE", "SoundCloud", AUDIO),
        ("https://on.soundcloud.com/AbCdE", "SoundCloud", AUDIO),
        ("https://shitmat.bandcamp.com/track/amen-babylon-2016-mix", "Bandcamp", AUDIO),
        ("youtu.be/Mdzy5RKn434", "YouTube", AUDIO),
        ("https://music.youtube.com/watch?v=abc", "YouTube", AUDIO),
        ("https://www.youtube.com/playlist?list=PL6D1DFED7726BF7AC", "YouTube", AUDIO),
        ("https://www.tiktok.com/@user/video/123", "TikTok", VIDEO),
        ("https://www.instagram.com/reel/CtZbNhtrJv3/", "Instagram", VIDEO),
        ("https://x.com/user/status/123", "Twitter", VIDEO),
        ("https://twitter.com/user/status/123", "Twitter", VIDEO),
        ("https://twitter.com/thecybergunny/status/1783930694407790959/video/1", "Twitter", VIDEO),
        ("https://x.com/i/status/123", "Twitter", VIDEO),
        ("https://t.co/NjDC0orve0", "Twitter", VIDEO),
        ("https://music.yandex.com/album/1/track/2", "Yandex Music", AUDIO),
    ],
)
def test_known_sites(url, site, kind):
    link = classify(url, CFG, unknown_sites=False)
    assert link is not None
    assert (link.site, link.kind) == (site, kind)


@pytest.mark.parametrize(
    "url",
    [
        "https://soundcloud.com/you/likes",
        "https://soundcloud.com/artist",  # profile page
        "https://www.youtube.com/@channel",
        "https://bandcamp.com",
        "https://x.com/user",
        "https://t.me/channel/123",
        "https://example.com/song.mp3",  # unknown site
        "ftp://soundcloud.com/a/b",
    ],
)
def test_rejected(url):
    assert classify(url, CFG, unknown_sites=False) is None


def test_known_site_rules_apply_with_unknown_sites_too():
    for url in ("https://soundcloud.com/you/likes", "https://www.youtube.com/@channel", "https://x.com/user", "https://www.instagram.com/reels/audio/123/"):
        assert classify(url, CFG, unknown_sites=True) is None, url


def test_unknown_sites():
    link = classify("https://www.mixcloud.com/dj/set/", CFG, unknown_sites=True)
    assert link is not None and link.site == "mixcloud.com" and link.kind == AUDIO
    assert classify("https://s.chollo.to/pLmcOG.png", CFG, unknown_sites=True) is None


def test_domain_lists():
    cfg = Config(blacklist_domains=frozenset({"soundcloud.com"}))
    assert classify("https://soundcloud.com/a/b", cfg, unknown_sites=True) is None
    cfg = Config(whitelist_domains=frozenset({"youtu.be"}))
    assert classify("https://soundcloud.com/a/b", cfg, unknown_sites=True) is None
    assert classify("https://youtu.be/x", cfg, unknown_sites=False) is not None


def test_fragment_dropped_and_host_lowercased():
    link = classify("https://SoundCloud.com/a/b#t=1:00", CFG, unknown_sites=False)
    assert link is not None and link.url == "https://soundcloud.com/a/b"


def test_find_links_from_text_and_caption():
    text = "hey 🎶 https://soundcloud.com/a/b and youtu.be/x and again https://soundcloud.com/a/b and a text link"
    entities = [
        MessageEntity(MessageEntity.URL, 7, 26),  # offsets are UTF-16 units: the emoji takes 2
        MessageEntity(MessageEntity.URL, 38, 10),
        MessageEntity(MessageEntity.URL, 59, 26),
        MessageEntity(MessageEntity.TEXT_LINK, 92, 9, url="https://x.com/u/status/1"),
    ]
    message = Message(1, datetime.now(UTC), Chat(1, "private"), text=text, entities=entities)
    links = find_links(message, CFG, unknown_sites=False)
    assert [link.url for link in links] == ["https://soundcloud.com/a/b", "https://youtu.be/x", "https://x.com/u/status/1"]


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("TG_BOT_API", "http://127.0.0.1:8081")
    monkeypatch.setenv("PROXIES", ",http://127.0.0.1:3187,direct")
    monkeypatch.setenv("NO_FLOOD_CHAT_IDS", "-100123, -100456")
    monkeypatch.setenv("MAX_CONVERT_FILE_SIZE", "350_000_000")
    monkeypatch.setenv("YTDLP_ARGS", "--js-runtimes node --impersonate 'chrome'")
    cfg = Config.from_env()
    assert cfg.local_mode
    assert cfg.max_tg_file_size == 1_900_000_000
    assert cfg.proxies == (None, "http://127.0.0.1:3187", None)
    assert cfg.no_flood_chat_ids == {-100123, -100456}
    assert cfg.max_convert_file_size == 350_000_000
    assert cfg.ytdlp_args == ("--js-runtimes", "node", "--impersonate", "chrome")


async def test_resolve_t_co_like_a_non_browser():
    import httpx

    seen_agents = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_agents.append(request.headers["user-agent"])
        if request.url.host == "t.co":  # real t.co: browsers get a 200 JavaScript page, anything else a redirect
            if "Firefox" in request.headers["user-agent"]:
                return httpx.Response(200, html="<meta http-equiv=refresh>")
            return httpx.Response(301, headers={"location": "https://twitter.com/thecybergunny/status/1783930694407790959/video/1"})
        if request.url.host == "on.soundcloud.com":
            return httpx.Response(302, headers={"location": "https://soundcloud.com/dynamq/squingylive"})
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), headers={"User-Agent": "Mozilla/5.0 Firefox/140.0"}) as client:
        link = await resolve(classify("https://t.co/NjDC0orve0", CFG, False), CFG, client, unknown_sites=False)
        assert link is not None and (link.site, link.kind, link.shortener) == ("Twitter", VIDEO, False)
        assert link.url == "https://twitter.com/thecybergunny/status/1783930694407790959/video/1"
        assert seen_agents[0] == "curl/8.5.0"  # a generic tool, nothing that identifies the bot

        seen_agents.clear()
        link = await resolve(classify("https://on.soundcloud.com/7Fw1d", CFG, False), CFG, client, unknown_sites=False)
        assert link is not None and link.url == "https://soundcloud.com/dynamq/squingylive"
        assert seen_agents[0] == "Mozilla/5.0 Firefox/140.0"  # other short links keep the usual browser user agent
