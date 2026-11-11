import base64
import importlib.util
import json
import stat
import sys
from pathlib import Path

TOOLS = Path(__file__).parent.parent / "tools"

# A made-up account in the sellers' format: login:password:2FA:user agent:device IDs:cookies and headers
LINE = (
    "jane.doe42:hunter2:TOTPSECRET:Instagram 432.0.0.0.66 Android (31/12; 420dpi; 1080x2400; Oppo; X; y; z; en_US; 1):"
    "android-0;1;2;3:mid=aAAAAAABAAAAAAAAAAAAAAAA;rur=NHA,123456789,1824531819:01ff;sessionid=123456789%3AabcDEF%3A11%3AAYxyz;"
    "Authorization=Bearer IGT:2:eyJzZXNzaW9uaWQiOiJ4In0=;X-IG-WWW-Claim=hmac.x;IG-U-DS-USER-ID=123456789;X-MID=aAAAAAABAAAAAAAAAAAAAAAA;::"
)
# And in the other format: login:password:2FA:the website's cookies as base64-encoded JSON
WEB_COOKIES = {
    "csrftoken": "abcCSRF",
    "ds_user_id": "987654321",
    "ig_did": "0A1B2C3D-4E5F",
    "mid": "bBBBBBBBBBBB",
    "rur": '"CLN\\054987654321\\0541824531819:01fe"',
    "sessionid": "987654321%3AxyzXYZ%3A9%3AAYabc",
}
LINE_JSON = "john.roe7:hunter3:ABCDEFGHIJKLMNOP:" + base64.b64encode(json.dumps(WEB_COOKIES).encode()).decode().rstrip("=")


def b64json(data) -> str:
    return base64.b64encode(json.dumps(data).encode()).decode()


def load_tool(name: str):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_instagram_cookies_keeps_only_the_session(tmp_path, monkeypatch, capsys):
    tool = load_tool("instagram_cookies")
    login, cookies = tool.cookies_of(LINE)
    assert login == "jane.doe42"
    assert cookies == {"sessionid": "123456789%3AabcDEF%3A11%3AAYxyz", "ds_user_id": "123456789", "mid": "aAAAAAABAAAAAAAAAAAAAAAA"}
    assert tool.cookies_of("someone:password:no session here") is None

    accounts = tmp_path / "accounts.txt"
    accounts.write_text(LINE + "\n\nbroken:line\n")
    monkeypatch.setattr(sys, "argv", ["instagram_cookies.py", str(accounts), str(tmp_path / "out")])
    tool.main()
    path = tmp_path / "out" / "jane.doe42.cookies.txt"
    text = path.read_text()
    assert text.startswith("# Netscape HTTP Cookie File\n.instagram.com\tTRUE\t/\tTRUE\t")
    assert "\tsessionid\t123456789%3AabcDEF%3A11%3AAYxyz\n" in text
    assert "hunter2" not in text and "TOTPSECRET" not in text  # no password or 2FA secret
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    out = capsys.readouterr()
    assert "hunter2" not in out.out and "line 3: no sessionid" in out.err
    assert 'name = "ig1"' in out.out and 'Instagram = ["ig1"]' in out.out

    monkeypatch.setattr(sys, "argv", ["instagram_cookies.py", str(accounts), str(tmp_path / "numbered"), "--prefix", "ig"])
    tool.main()
    assert (tmp_path / "numbered" / "ig1.cookies.txt").read_text() == text  # named for the routes file, not by login


def test_instagram_cookies_reads_base64_json_website_cookies(tmp_path, monkeypatch, capsys):
    tool = load_tool("instagram_cookies")
    assert tool.cookies_of(LINE_JSON) == ("john.roe7", WEB_COOKIES)
    # a browser extension's export, without ds_user_id (it comes from the session) and with a cookie a cookies.txt can't hold
    export = [{"name": "sessionid", "value": "11%3Ax"}, {"name": "mid", "value": "m"}, {"name": "odd", "value": "a b"}]
    assert tool.cookies_of("a:b:c:" + b64json(export)) == ("a", {"sessionid": "11%3Ax", "mid": "m", "ds_user_id": "11"})
    assert tool.cookies_of("a:b:c:" + b64json({"csrftoken": "x"})) is None  # no session
    assert tool.cookies_of("a:b:c:eyJnot-base64-json") is None

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "ig1.cookies.txt").write_text("an older account\n")
    accounts = tmp_path / "accounts.txt"
    accounts.write_text(f"{LINE_JSON}\n{LINE}\n")
    monkeypatch.setattr(sys, "argv", ["instagram_cookies.py", str(accounts), str(out_dir), "--prefix", "ig", "--first", "4"])
    tool.main()
    assert (out_dir / "ig1.cookies.txt").read_text() == "an older account\n"  # --first 4 left it alone
    text = (out_dir / "ig4.cookies.txt").read_text()
    assert "\tcsrftoken\tabcCSRF\n" in text and "\tsessionid\t987654321%3AxyzXYZ%3A9%3AAYabc\n" in text
    assert "hunter3" not in text and "ABCDEFGHIJKLMNOP" not in text
    assert "\tsessionid\t123456789%3AabcDEF%3A11%3AAYxyz\n" in (out_dir / "ig5.cookies.txt").read_text()
    out = capsys.readouterr().out
    assert 'name = "ig4"' in out and 'name = "ig5"' in out and 'Instagram = ["ig4", "ig5"]' in out and "hunter3" not in out


def test_converted_cookies_load_in_a_login_route(tmp_path, monkeypatch):
    from scdlbot.routes import parse

    tool = load_tool("instagram_cookies")
    path = tmp_path / "ig1.cookies.txt"
    path.write_text(tool.netscape(tool.cookies_of(LINE)[1], 2_000_000_000))
    routes, _, login = parse({"routes": [{"name": "ig1", "source_address": "0.0.0.0", "cookies": str(path)}], "login": {"Instagram": ["ig1"]}}, tmp_path)
    args = routes[0].ytdlp_args(tmp_path)
    assert args[-2] == "--cookies" and Path(args[-1]).read_text() == path.read_text() and login == {"Instagram": ["ig1"]}


def test_firefox_cookies_copy_is_the_database_file(tmp_path):
    import sqlite3

    tool = load_tool("serve_firefox_cookies")
    firefox = sqlite3.connect(tmp_path / "cookies.sqlite")  # like Firefox: WAL, locked
    firefox.execute("PRAGMA journal_mode=WAL")
    firefox.execute("PRAGMA locking_mode=EXCLUSIVE")
    firefox.execute("CREATE TABLE moz_cookies (host TEXT, name TEXT, value TEXT)")
    firefox.executemany("INSERT INTO moz_cookies VALUES (?, ?, ?)", [(".youtube.com", f"c{i}", "v") for i in range(300)])
    firefox.commit()
    firefox.execute("PRAGMA wal_checkpoint")  # in the database file now
    firefox.execute("PRAGMA wal_autocheckpoint=0")
    firefox.execute("INSERT INTO moz_cookies VALUES ('.tiktok.com', 'x', 'only in the log')")
    firefox.commit()
    (tmp_path / "copy.sqlite").write_bytes(tool.snapshot(tmp_path))
    copy = sqlite3.connect(f"file:{tmp_path / 'copy.sqlite'}?immutable=1", uri=True)
    assert copy.execute("SELECT count(*) FROM moz_cookies").fetchone() == (300,) and copy.execute("PRAGMA quick_check").fetchone() == ("ok",)
    firefox.close()
