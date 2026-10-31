#!/usr/bin/env python3
"""Turn Instagram accounts bought "with cookies" into cookies.txt files for login routes (see deploy/routes.sample.toml).

Sellers give one account per line, fields separated by ":": login, password, 2FA secret, the app's user agent,
device IDs, then the app session's cookies and headers ("mid=...;sessionid=...;Authorization=Bearer IGT:2:...;
IG-U-DS-USER-ID=...;"). yt-dlp downloads Instagram through the website, which needs only the session's cookies:
sessionid, ds_user_id and mid. The password and 2FA secret are not written anywhere.

    python3 tools/instagram_cookies.py ACCOUNTS_FILE /etc/scdlbot/instagram --group www-data --prefix ig

writes /etc/scdlbot/instagram/ig1.cookies.txt, ig2... for the accounts in order (without --prefix: LOGIN.cookies.txt),
mode 640, group www-data (readable by the bot), and prints a [[routes]] block for each one for the routes file.
"""

import argparse
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
    session = re.search(r"(?<![\w-])sessionid=([^;:\s]+)", line)
    if not login or not session:
        return None
    cookies = {"sessionid": session[1]}
    user_id = re.search(r"(?:IG-U-DS-USER-ID|ds_user_id)=(\d+)", line) or re.match(r"(\d+)%3A", session[1])
    if user_id:
        cookies["ds_user_id"] = user_id[1]
    if mid := re.search(r"(?<![\w-])mid=([^;:\s]+)", line):
        cookies["mid"] = mid[1]
    return login, cookies


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
        name = f"{args.prefix}{len(written) + 1}" if args.prefix else re.sub(r"[^A-Za-z0-9._-]", "_", login)
        path = args.out / f"{name}.cookies.txt"
        path.touch(mode=0o640, exist_ok=True)
        path.chmod(0o640)
        path.write_text(netscape(cookies, expires))
        if args.group:
            shutil.chown(path, group=args.group)
        written.append(path)
        print(f"{login}: user ID {cookies.get('ds_user_id', '?')}, {', '.join(cookies)} -> {path}")
    if written:
        print("\n# For the routes file, one account per IP (e.g. fin1 = 0.0.0.0, fin1-v6 = ::, fra1 = the proxy):")
        names = [f"ig{number}" for number in range(1, len(written) + 1)]
        for name, path in zip(names, written, strict=True):
            print(f'[[routes]]\nname = "{name}"\nsource_address = "0.0.0.0"\ncookies = "{path}"\n')
        print("[login]\nInstagram = [" + ", ".join(f'"{name}"' for name in names) + "]")


if __name__ == "__main__":
    main()
