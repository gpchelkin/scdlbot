#!/usr/bin/env python3
"""Turn Instagram accounts bought "with cookies" into cookies.txt files for login routes (see deploy/routes.sample.toml).

Sellers give one account per line, fields separated by ":", in one of two formats:
- login, password, 2FA secret, the app's user agent, device IDs, then the app session's cookies and headers
  ("mid=...;sessionid=...;Authorization=Bearer IGT:2:...;IG-U-DS-USER-ID=...;");
- login, password, 2FA secret, then the website's cookies as base64-encoded JSON ("eyJ..." for {"sessionid": ...}).
yt-dlp downloads Instagram through the website, so of an app session it needs only the cookies sessionid, ds_user_id
and mid; website cookies are kept as they are. The password and 2FA secret are not written anywhere.

    python3 tools/instagram_cookies.py ACCOUNTS_FILE /etc/scdlbot/instagram --group www-data --prefix ig [--first 4]

writes /etc/scdlbot/instagram/ig1.cookies.txt, ig2... (from igN with --first N, so that new accounts don't replace
the files of older ones) for the accounts in order (without --prefix: LOGIN.cookies.txt), mode 640, group www-data
(readable by the bot), and prints a [[routes]] block for each one for the routes file.
"""

import argparse
import base64
import json
import re
import shutil
import sys
import time
from pathlib import Path

DOMAIN = ".instagram.com"
YEAR = 365 * 24 * 3600


def cookies_of(line: str) -> tuple[str, dict[str, str]] | None:
    """(login, {cookie: value}) of a seller's line; None if it has no session."""
    login = line.split(":", 1)[0].strip()
    if not login:
        return None
    if session := re.search(r"(?<![\w-])sessionid=([^;:\s]+)", line):
        cookies = {"sessionid": session[1]}
        if user_id := re.search(r"(?:IG-U-DS-USER-ID|ds_user_id)=(\d+)", line):
            cookies["ds_user_id"] = user_id[1]
        if mid := re.search(r"(?<![\w-])mid=([^;:\s]+)", line):
            cookies["mid"] = mid[1]
    else:
        cookies = next((found for field in line.split(":")[1:] if (found := json_cookies(field))), {})
        if not cookies:
            return None
    if "ds_user_id" not in cookies and (user_id := re.match(r"(\d+)%3A", cookies["sessionid"])):
        cookies["ds_user_id"] = user_id[1]
    return login, cookies


def json_cookies(field: str) -> dict[str, str]:
    """{cookie: value} of a field of base64-encoded JSON cookies with a session, else {}.

    The JSON is {"name": "value", ...} or a browser extension's export, [{"name": ..., "value": ...}, ...].
    """
    field = field.strip()
    if not field.startswith(("eyJ", "W3s")):  # base64 of '{"' and '[{'
        return {}
    try:
        data = json.loads(base64.urlsafe_b64decode(field.translate(str.maketrans("+/", "-_")) + "=" * (-len(field) % 4)))
    except ValueError:  # not base64, UTF-8 or JSON
        return {}
    if isinstance(data, list):
        data = {item.get("name"): item.get("value") for item in data if isinstance(item, dict)}
    if not isinstance(data, dict):
        return {}
    # a cookies.txt line can't hold whitespace, so such a cookie (if any) is left out
    cookies = {str(name): str(value) for name, value in data.items() if name and isinstance(value, str | int)}
    cookies = {name: value for name, value in cookies.items() if value and not re.search(r"\s", name + value)}
    return cookies if "sessionid" in cookies else {}


def netscape(cookies: dict[str, str], expires: int) -> str:
    lines = ["# Netscape HTTP Cookie File"]
    lines += [f"{DOMAIN}\tTRUE\t/\tTRUE\t{expires}\t{name}\t{value}" for name, value in cookies.items()]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("accounts", type=Path, help="the seller's file, one account per line")
    parser.add_argument("out", type=Path, help="directory for the cookies.txt files")
    parser.add_argument("--group", help="group that may read the files (the bot's, e.g. www-data)")
    parser.add_argument("--prefix", help="name the files PREFIX1.cookies.txt, PREFIX2... instead of by login")
    parser.add_argument("--first", type=int, default=1, help="number of the first account (default 1): PREFIXFIRST.cookies.txt")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    expires = int(time.time()) + YEAR
    written = []
    for number, line in enumerate(args.accounts.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        if not (found := cookies_of(line)):
            print(f"line {number}: no sessionid, skipped", file=sys.stderr)
            continue
        login, cookies = found
        route = f"{args.prefix or 'ig'}{args.first + len(written)}"
        path = args.out / f"{route if args.prefix else re.sub(r'[^A-Za-z0-9._-]', '_', login)}.cookies.txt"
        replaced = " (replaced the old file)" if path.exists() else ""
        path.touch(mode=0o640, exist_ok=True)
        path.chmod(0o640)
        path.write_text(netscape(cookies, expires))
        if args.group:
            shutil.chown(path, group=args.group)
        written.append((route, path))
        print(f"{login}: user ID {cookies.get('ds_user_id', '?')}, {', '.join(cookies)} -> {path}{replaced}")
    if written:
        print("\n# For the routes file, one account per IP (e.g. fin1 = 0.0.0.0, fin1-v6 = ::, fra1 = the proxy):")
        for route, path in written:
            print(f'[[routes]]\nname = "{route}"\nsource_address = "0.0.0.0"\ncookies = "{path}"\n')
        print("[login]\nInstagram = [" + ", ".join(f'"{route}"' for route, _ in written) + "]")


if __name__ == "__main__":
    main()
