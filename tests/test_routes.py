import re
import tomllib
from pathlib import Path

import pytest

from scdlbot import routes
from scdlbot.config import Config
from scdlbot.routes import Route, RoutePool, RoutesError, RouteWatch, from_env, parse
from scdlbot.ytdlp import Cookies

SAMPLE = Path(__file__).parent.parent / "deploy" / "routes.sample.toml"


def load(text: str):
    return parse(tomllib.loads(text), Path("/data"))


def test_sample_file_is_valid():
    found, sites, login = parse(tomllib.loads(SAMPLE.read_text()), Path("/data"))
    assert [route.name for route in found] == ["fin1", "fra1", "fin1-v6"]
    fra1 = found[1]
    assert fra1.ytdlp_args(Path("/job"))[:2] == ["--proxy", "http://127.0.0.1:3187"]
    assert fra1.cookies.browser == "firefox:/var/lib/scdlbot/firefox/abcd1234.default-release"  # a full path, no HOME needed
    assert sites["YouTube"] == ["fra1"] and "SoundCloud" not in sites and login == {}


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ('[[routes]]\nname = "a"\n[sites]\nYoutube = ["a"]', "did you mean 'YouTube'"),
        ('[[routes]]\nname = "a"\n[sites]\nYouTube = ["b"]', "no such route b"),
        ('[[routes]]\nname = "a"\n[[routes]]\nname = "a"', "used twice"),
        ('[[routes]]\nname = "a b"', "name 'a b'"),
        ('[[routes]]\nname = "a"\nsource_address = "1.2.3"', "not an IP address"),
        ('[[routes]]\nname = "a"\nproxy = "http://127.0.0.1:1"\nsource_address = "::"', "IPv6 source_address can't reach the IPv4 proxy"),
        ('[[routes]]\nname = "a"\nproxy = "127.0.0.1:1"', "proxy must look like"),
        ('[[routes]]\nname = "a"\nsource = "::"', "unknown keys source"),
        ("[sites]", "no [[routes]]"),
    ],
)
def test_mistakes_are_reported(text, error):
    with pytest.raises(RoutesError, match=re.escape(error)):
        load(text)


def test_domains_and_default_are_valid_sites():
    _, sites, _ = load('[[routes]]\nname = "a"\n[sites]\n"mixcloud.com" = ["a"]\ndefault = ["a"]')
    assert sites == {"mixcloud.com": ["a"], "default": ["a"]}


def test_routes_from_old_settings():
    cfg = Config(proxies=(None, "http://user:pw@10.0.0.1:3128"), source_ips=("0.0.0.0",), cookies_file="/c.txt")
    found, sites, login = from_env(cfg)
    assert [(route.name, route.proxy, route.source_address) for route in found] == [
        ("direct", None, "0.0.0.0"),
        ("10.0.0.1:3128", "http://user:pw@10.0.0.1:3128", "0.0.0.0"),
    ]
    assert found[1].describe() == "10.0.0.1:3128 (proxy http://10.0.0.1:3128, from 0.0.0.0, with cookies)"  # no password
    assert from_env(Config())[0] == [Route("direct")] and sites == login == {}


def test_round_robin_per_site_within_its_routes():
    pool = RoutePool(*load(SAMPLE.read_text()))
    assert [pool.pick("SoundCloud").name for _ in range(4)] == ["fin1", "fra1", "fin1", "fra1"]
    assert [pool.pick("Instagram").name for _ in range(3)] == ["fin1", "fra1", "fin1-v6"]
    assert {pool.pick("YouTube").name for _ in range(3)} == {"fra1"}
    assert pool.pick("YouTube", avoid="fra1").name == "fra1"  # nowhere else to go
    assert pool.pick("SoundCloud", avoid="fin1").name == "fra1"


def test_excluded_routes_are_skipped_until_all_are_excluded():
    pool = RoutePool(*load(SAMPLE.read_text()))
    pool.exclusions[("SoundCloud", "fra1")] = 4e9
    assert {pool.pick("SoundCloud").name for _ in range(4)} == {"fin1"}
    assert {pool.pick("Bandcamp").name for _ in range(4)} == {"fin1", "fra1"}  # only for SoundCloud
    pool.exclusions[(None, "fin1")] = 4e9  # fin1 for all sites
    assert {pool.pick("SoundCloud").name for _ in range(4)} == {"fin1", "fra1"}  # fail-open
    assert pool.pick("SoundCloud", avoid="fin1").name == "fra1"


def test_route_watch_leaves_out_the_route_a_site_blocks():
    pool = RoutePool(*load(SAMPLE.read_text()))
    watch = RouteWatch(pool, min_jobs=20, ratio=0.2, exclusion=7200)
    for _ in range(30):
        pool.record("SoundCloud", "fin1", None)
    for n in range(30):
        pool.record("SoundCloud", "fra1", "blocked" if n < 12 else None, "ERROR: [soundcloud] x: HTTP Error 403: Forbidden")
    now = pool.attempts[-1][0] + 1
    (message,) = watch.check(now)
    assert message.startswith("⛔ <b>SoundCloud</b> via <b>fra1</b>: 12 of 30 downloads failed (blocked 12) in the last 30 min")
    assert "HTTP Error 403" in message
    assert pool.excluded("SoundCloud", "fra1", now) and not pool.excluded("Bandcamp", "fra1", now)
    assert routes.observe.ROUTE_UP.labels("SoundCloud", "fra1")._value.get() == 0
    assert watch.check(now + 300) == []  # once
    (back,) = watch.check(now + 7201)
    assert back == "↩️ <b>fra1</b> is used again for SoundCloud."


def test_route_watch_blames_the_site_when_every_route_fails():
    pool = RoutePool(*load(SAMPLE.read_text()))
    watch = RouteWatch(pool, min_jobs=20, ratio=0.2, exclusion=7200)
    for name in ("fin1", "fra1"):
        for n in range(30):
            pool.record("SoundCloud", name, "blocked" if n < 15 else None)
    assert watch.check(pool.attempts[-1][0] + 1) == []
    assert not pool.exclusions


def test_route_watch_leaves_out_a_route_that_cant_connect_for_all_sites():
    pool = RoutePool(*load(SAMPLE.read_text()))
    watch = RouteWatch(pool, min_jobs=20, ratio=0.2, exclusion=7200)
    for site in ("SoundCloud", "Bandcamp"):
        for _ in range(15):
            pool.record(site, "fra1", "network")
            pool.record(site, "fin1", None)
    messages = watch.check(pool.attempts[-1][0] + 1)
    assert messages[0].startswith("⛔ <b>fra1</b>, all sites: 30 of 30 downloads failed (network 30)")
    assert pool.excluded("TikTok", "fra1")


def test_reload_keeps_exclusions_of_routes_that_stay():
    pool = RoutePool(*load(SAMPLE.read_text()))
    pool.exclusions[("SoundCloud", "fra1")] = 4e9
    pool.exclusions[("Instagram", "fin1-v6")] = 4e9
    pool.update(*load('[[routes]]\nname = "fin1"\n[[routes]]\nname = "fra1"\nproxy = "http://127.0.0.1:3187"'))
    assert set(pool.exclusions) == {("SoundCloud", "fra1")}
    assert [route.name for route in pool.allowed("Instagram")] == ["fin1", "fra1"]  # no [sites]: every route


def test_cookies_firefox_profile_by_path_or_name(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    by_path = Cookies("firefox:/srv/ff/abc.default", tmp_path / "c.txt")
    assert (by_path.browser, by_path.file) == ("firefox:/srv/ff/abc.default", Path("/srv/ff/abc.default/cookies.sqlite"))
    by_name = Cookies("firefox:abc.default:https://example.com/cookies.sqlite", tmp_path / "c.txt")
    assert by_name.file == tmp_path / ".mozilla" / "firefox" / "abc.default" / "cookies.sqlite"
    assert by_name.url == "https://example.com/cookies.sqlite"


async def test_reload_applies_a_good_file_and_keeps_routes_on_a_broken_one(tmp_path, caplog):
    from types import SimpleNamespace

    from scdlbot.app import reload_routes

    path = tmp_path / "routes.toml"
    path.write_text(SAMPLE.read_text())
    cfg = Config(routes_file=path, db_path=tmp_path / "db.sqlite3")
    pool = RoutePool(*routes.load(cfg))
    rt = SimpleNamespace(cfg=cfg, queue=SimpleNamespace(routes=pool), http=None)
    path.write_text('[[routes]]\nname = "fin2"\nsource_address = "0.0.0.0"\n')
    await reload_routes(rt)
    assert list(pool.routes) == ["fin2"] and pool.sites == {}
    path.write_text('[[routes]]\nname = "fin3"\n[sites]\nYoutube = ["fin3"]\n')
    await reload_routes(rt)
    assert list(pool.routes) == ["fin2"]  # the broken file changed nothing
    assert "Routes not reloaded, still using the old ones: [sites]: unknown site 'Youtube', did you mean 'YouTube'?" in caplog.text


LOGIN = (
    SAMPLE.read_text().replace("# [[routes]]", "[[routes]]").replace('# name = "fin1-login"', 'name = "fin1-login"')
    .replace("# source_address", "source_address").replace("# cookies = ", "cookies = ").replace("# [login]", "[login]")
    .replace('# Instagram = ["fin1-login"]', 'Instagram = ["fin1-login"]')
)  # fmt: skip


def test_sample_login_route_when_uncommented():
    found, sites, login = load(LOGIN)
    assert login == {"Instagram": ["fin1-login"]} and found[-1].cookies.file == Path("/etc/scdlbot/instagram-cookies.txt")
    pool = RoutePool(found, sites, login)
    assert pool.pick_login("Instagram").name == "fin1-login" and pool.pick_login("TikTok") is None
    assert "fin1-login" not in {route.name for route in pool.allowed("TikTok")}  # [sites] default doesn't have it
    no_lists = RoutePool(found, {}, login)
    assert [route.name for route in no_lists.allowed("TikTok")] == ["fin1", "fra1", "fin1-v6"]  # never for anonymous downloads


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ('[[routes]]\nname = "a"\n[login]\nInstagram = ["a"]', "route a has no cookies"),
        ('[[routes]]\nname = "a"\ncookies = "/c.txt"\n[login]\ndefault = ["a"]', "[login]: unknown site 'default'"),
        ('[[routes]]\nname = "a"\ncookies = "/c.txt"\n[login]\ninstagram = ["a"]', "did you mean 'Instagram'"),
    ],
)
def test_login_mistakes_are_reported(text, error):
    with pytest.raises(RoutesError, match=re.escape(error)):
        load(text)


def test_login_route_that_keeps_failing_is_left_out_until_reload():
    found, sites, login = load(LOGIN.replace('Instagram = ["fin1-login"]', 'Instagram = ["fin1-login", "fra1"]'))
    pool = RoutePool(found, sites, login)
    watch = RouteWatch(pool, min_jobs=20, ratio=0.2, exclusion=7200)
    for _ in range(routes.LOGIN_STREAK - 1):
        pool.record("Instagram", "fin1-login", "login", "ERROR: [Instagram] x: Instagram sent an empty media response.")
    pool.record("Instagram", "fin1-login", None)  # one success: the account works
    for _ in range(routes.LOGIN_STREAK - 2):
        pool.record("Instagram", "fin1-login", "restricted")
    pool.record("Instagram", "fin1-login", "failed")  # Instagram's 401 "Please wait a few minutes", as yt-dlp reports it
    pool.record("Instagram", "fin1-login", "unavailable")  # the post is gone: says nothing about the account
    assert watch.check(pool.attempts[-1][0] + 1) == []
    pool.record("Instagram", "fin1-login", "login", "ERROR: [Instagram] x: Instagram sent an empty media response.")
    now = pool.attempts[-1][0] + 1
    (message,) = watch.check(now)
    assert message.startswith("🔑 <b>Instagram</b> login route <b>fin1-login</b>: the last 8 downloads that needed a login failed")
    assert "empty media response" in message
    assert pool.excluded("Instagram", "fin1-login", now + 23 * 3600)
    assert {pool.pick_login("Instagram").name for _ in range(3)} == {"fra1"}  # the other account
    assert routes.observe.ROUTE_UP.labels("Instagram", "fin1-login")._value.get() == 0
    pool.update(found, sites, {"Instagram": ["fin1-login", "fra1"]})  # reloaded with new cookies
    assert not pool.excluded("Instagram", "fin1-login")
