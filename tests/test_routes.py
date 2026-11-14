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
    found, sites, accounts = parse(tomllib.loads(SAMPLE.read_text()), Path("/data"))
    assert [route.name for route in found] == ["fin1", "fra1", "fin1-v6"]
    assert found[1].ytdlp_args(Path("/job"))[:2] == ["--proxy", "http://100.64.0.7:8888"] and not found[1].cookies
    (firefox,) = accounts
    assert (firefox.name, firefox.route, firefox.always, firefox.when_needed) == ("firefox", "fra1", ("YouTube",), ("TikTok",))
    assert firefox.cookies.browser == "firefox:/var/lib/scdlbot/firefox/abcd1234.default-release"  # a full path, no HOME needed
    assert sites["YouTube"] == ["fra1"] and "SoundCloud" not in sites


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
        ('[[routes]]\nname = "a"\ncookies = "/c.txt"', "route a: cookies belong to an [[accounts]] entry"),
        ('[[routes]]\nname = "a"\n[login]\nInstagram = ["a"]', "[login] is gone"),
        ('[[routes]]\nname = "a"\n[[accounts]]\nname = "x"\nroute = "b"\ncookies = "/c.txt"\nalways = ["YouTube"]', "route = 'b' isn't one of"),
        ('[[routes]]\nname = "a"\n[[accounts]]\nname = "x"\nroute = "a"\nalways = ["YouTube"]', "account x: no cookies"),
        ('[[routes]]\nname = "a"\n[[accounts]]\nname = "x"\nroute = "a"\ncookies = "/c.txt"', "give it always = [sites], when_needed = [sites], or both"),
        ('[[routes]]\nname = "a"\n[[accounts]]\nname = "x"\nroute = "a"\ncookies = "/c.txt"\nalways = ["Youtube"]', "did you mean 'YouTube'"),
        ('[[routes]]\nname = "a"\n[[accounts]]\nname = "x"\nroute = "a"\ncookies = "/c.txt"\nwhen_needed = ["default"]', "unknown site 'default'"),
        ('[[routes]]\nname = "a"\n[[accounts]]\nname = "x"\nroute = "a"\ncookies = "/c.txt"\nalways = ["TikTok"]\nwhen_needed = ["TikTok"]', "TikTok can't be in both"),
        ('[[routes]]\nname = "a"\n[[accounts]]\nname = "a"\nroute = "a"\ncookies = "/c.txt"\nalways = ["YouTube"]', "a: this name is used twice"),
        ('[[routes]]\nname = "a"\n[[accounts]]\nname = "x"\nroute = "a"\ncookies = "/c.txt"\nalways = ["YouTube"]\nsites = ["YouTube"]', "unknown keys sites"),
        (
            '[[routes]]\nname = "a"\n[[accounts]]\nname = "x"\nroute = "a"\ncookies = "/c.txt"\nalways = ["YouTube"]'
            '\n[[accounts]]\nname = "y"\nroute = "a"\ncookies = "/d.txt"\nalways = ["YouTube"]',
            "accounts x and y both always log in to YouTube through a",
        ),
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
    found, sites, accounts = from_env(cfg)
    assert [(route.name, route.proxy, route.source_address) for route in found] == [
        ("direct", None, "0.0.0.0"),
        ("10.0.0.1:3128", "http://user:pw@10.0.0.1:3128", "0.0.0.0"),
    ]
    pool = RoutePool(found, sites, accounts)
    assert pool.pick("SoundCloud", avoid="direct").describe() == "10.0.0.1:3128 (proxy http://10.0.0.1:3128, from 0.0.0.0, as COOKIES_FILE@10.0.0.1:3128)"  # no password
    assert {pool.pick(site).cookies.file for site in ("SoundCloud", "TikTok", "example.com")} == {Path("/c.txt")}  # COOKIES_FILE everywhere
    assert from_env(Config()) == ([Route("direct")], {}, [])


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
    assert not pool.accounts


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


async def test_reload_tells_the_owner_about_accounts_that_cant_work(tmp_path, caplog):
    from types import SimpleNamespace

    from scdlbot.app import reload_routes

    (tmp_path / "ig1.cookies.txt").write_text("# Netscape HTTP Cookie File\n")
    path = tmp_path / "routes.toml"
    path.write_text(
        '[[routes]]\nname = "fin1"\n[[routes]]\nname = "fra1"\n[sites]\nYouTube = ["fin1"]\n'
        f'[[accounts]]\nname = "ig1"\nroute = "fin1"\ncookies = "{tmp_path}/ig1.cookies.txt"\nwhen_needed = ["Instagram"]\n'
        f'[[accounts]]\nname = "ig2"\nroute = "fra1"\ncookies = "{tmp_path}/ig2.cookies.txt"\nwhen_needed = ["Instagram"]\n'
        f'[[accounts]]\nname = "yt"\nroute = "fra1"\ncookies = "{tmp_path}/ig1.cookies.txt"\nalways = ["YouTube"]\n'
    )
    cfg = Config(routes_file=path, db_path=tmp_path / "db.sqlite3")
    rt = SimpleNamespace(cfg=cfg, queue=SimpleNamespace(routes=RoutePool(*routes.load(cfg))), http=None)
    caplog.set_level("INFO", logger="scdlbot.app")
    await reload_routes(rt)
    problems = [(record.levelname, record.getMessage()) for record in caplog.records if record.levelname in ("ERROR", "WARNING")]
    assert problems == [  # alerts for the owner
        ("ERROR", f"Account ig2 goes without cookies: {tmp_path}/ig2.cookies.txt doesn't exist"),
        ("ERROR", "Account yt always logs in to YouTube through fra1, but YouTube doesn't use fra1 ([sites])"),
        ("WARNING", f"The same cookies ({tmp_path}/ig1.cookies.txt) go out through routes fin1, fra1: a session that jumps between IPs looks hijacked"),
    ]
    assert "accounts: ig1 via fin1 (when needed Instagram), ig2 via fra1 (when needed Instagram), yt via fra1 (always YouTube)" in caplog.text


SAMPLE_IG1 = '# [[accounts]]\n# name = "ig1"\n# route = "fin1"\n# cookies = "/etc/scdlbot/instagram/ig1.cookies.txt"\n# when_needed = ["Instagram"]\n'
ACCOUNTS = SAMPLE.read_text().replace(SAMPLE_IG1, SAMPLE_IG1.replace("# ", "")) + (
    '[[accounts]]\nname = "ig2"\nroute = "fin1-v6"\ncookies = "/etc/scdlbot/instagram/ig2.cookies.txt"\nwhen_needed = ["Instagram"]\n'
)
assert ACCOUNTS.count('\nname = "ig1"') == 1


def test_sample_instagram_account_when_uncommented():
    found, sites, accounts = load(ACCOUNTS)
    assert [account.name for account in accounts] == ["firefox", "ig1", "ig2"]
    pool = RoutePool(found, sites, accounts)
    ig1 = pool.pick_login("Instagram")
    assert (ig1.name, ig1.via, ig1.source_address, ig1.cookies.file) == ("ig1", "fin1", "0.0.0.0", Path("/etc/scdlbot/instagram/ig1.cookies.txt"))
    assert ig1.describe() == "ig1 (account, via fin1: direct, from 0.0.0.0)"
    assert pool.pick_login("Instagram").name == "ig2" and pool.pick_login("SoundCloud") is None
    assert {route.cookies for route in (pool.pick("Instagram") for _ in range(3))} == {None}  # the accounts never go anonymous


def test_accounts_log_in_only_to_their_sites():
    found, sites, accounts = load(SAMPLE.read_text())
    pool = RoutePool(found, sites, accounts)
    youtube = pool.pick("YouTube")
    assert youtube.describe() == "fra1 (proxy http://100.64.0.7:8888, as firefox)" and youtube.cookies is accounts[0].cookies
    assert {(route.name, route.cookies) for route in (pool.pick("TikTok") for _ in range(2))} == {("fin1", None), ("fra1", None)}
    assert pool.untried("TikTok", {"fin1"}).describe() == "fra1 (proxy http://100.64.0.7:8888)"
    assert pool.pick("SoundCloud", avoid="fin1").cookies is None  # not one of firefox's sites
    tiktok = pool.pick_login("TikTok")
    assert tiktok.describe() == "firefox (account, via fra1: proxy http://100.64.0.7:8888)" and tiktok.cookies is accounts[0].cookies
    assert pool.untried("TikTok", set(), login=True).name == "firefox" and pool.untried("TikTok", {"firefox"}, login=True) is None
    assert pool.cookies() == [accounts[0].cookies]


def test_account_that_keeps_failing_is_left_out_until_reload():
    found, sites, accounts = load(ACCOUNTS)
    pool = RoutePool(found, sites, accounts)
    watch = RouteWatch(pool, min_jobs=20, ratio=0.2, exclusion=7200)
    for _ in range(routes.LOGIN_STREAK - 1):
        pool.record("Instagram", "ig1", "login", "ERROR: [Instagram] x: Instagram sent an empty media response.")
    pool.record("Instagram", "ig1", None)  # one success: the account works
    for _ in range(routes.LOGIN_STREAK - 2):
        pool.record("Instagram", "ig1", "restricted")
    pool.record("Instagram", "ig1", "failed")  # Instagram's 401 "Please wait a few minutes", as yt-dlp reports it
    pool.record("Instagram", "ig1", "unavailable")  # the post is gone: says nothing about the account
    assert watch.check(pool.attempts[-1][0] + 1) == []
    pool.record("Instagram", "ig1", "login", "ERROR: [Instagram] x: Instagram sent an empty media response.")
    now = pool.attempts[-1][0] + 1
    (message,) = watch.check(now)
    assert message.startswith("🔑 <b>Instagram</b> account <b>ig1</b>: the last 8 downloads that needed a login failed")
    assert "empty media response" in message
    assert pool.excluded("Instagram", "ig1", now + 23 * 3600)
    assert {pool.pick_login("Instagram").name for _ in range(3)} == {"ig2"}  # the other account
    assert routes.observe.ROUTE_UP.labels("Instagram", "ig1")._value.get() == 0
    pool.update(found, sites, accounts)  # reloaded with new cookies
    assert not pool.excluded("Instagram", "ig1")
