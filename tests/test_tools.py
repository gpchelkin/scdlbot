import importlib.util
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


def test_converted_cookies_load_in_a_login_route(tmp_path, monkeypatch):
    from scdlbot.routes import parse

    tool = load_tool("instagram_cookies")
    path = tmp_path / "ig1.cookies.txt"
    path.write_text(tool.netscape(tool.cookies_of(LINE)[1], 2_000_000_000))
    routes, _, login = parse({"routes": [{"name": "ig1", "source_address": "0.0.0.0", "cookies": str(path)}], "login": {"Instagram": ["ig1"]}}, tmp_path)
    args = routes[0].ytdlp_args(tmp_path)
    assert args[-2] == "--cookies" and Path(args[-1]).read_text() == path.read_text() and login == {"Instagram": ["ig1"]}
