#!/usr/bin/env python3
"""Serve a Firefox profile's cookies to the bot on another server: GET /cookies.sqlite returns a fresh copy.

For a route's `cookies = "firefox:/local/profile/dir:http://ADDRESS:PORT/cookies.sqlite"` (deploy/routes.sample.toml):
the bot downloads it every COOKIES_REFRESH seconds into the local profile directory and hands that to yt-dlp. Firefox
keeps its cookies.sqlite locked, so the file is copied (again if Firefox wrote meanwhile), and the copy must pass
SQLite's check. Like yt-dlp's own --cookies-from-browser, it leaves out cookies.sqlite-wal: Firefox's exclusive lock
keeps the log's bookkeeping in its memory, and a copy of the log can hold changes older than the database (seen on a
real profile: merging it lost the newest cookie). Firefox moves the log into the database every few hundred kilobytes.

Listen only on a private address (e.g. the server's tailnet IP), and let only the bot's server connect, e.g. as a
systemd service on the server where Firefox runs:

    [Unit]
    Description=Firefox's cookies for scdlbot
    Wants=tailscaled.service
    After=tailscaled.service
    [Service]
    ExecStart=/usr/bin/python3 -I /usr/local/bin/serve_firefox_cookies.py /root/.mozilla/firefox/PROFILE 100.64.0.7 8765
    Restart=on-failure
    RestartSec=5
    IPAddressDeny=any
    IPAddressAllow=BOT_SERVER_TAILNET_IPV4/32 BOT_SERVER_TAILNET_IPV6/128
    ProtectSystem=strict
    ProtectHome=read-only
    PrivateTmp=yes
    NoNewPrivileges=yes
    [Install]
    WantedBy=multi-user.target
"""

import http.server
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

COOKIES = "cookies.sqlite"


def stamp(path: Path) -> tuple[int, int]:
    return path.stat().st_mtime_ns, path.stat().st_size


def snapshot(profile: Path, tries: int = 5) -> bytes:
    """A consistent copy of the profile's cookies.sqlite (without its write-ahead log, see above)."""
    source = profile / COOKIES
    for _ in range(tries):
        before = stamp(source)
        with tempfile.TemporaryDirectory() as directory:
            copy = Path(directory, COOKIES)
            shutil.copyfile(source, copy)
            if stamp(source) == before:  # Firefox didn't write while it was copied
                db = sqlite3.connect(f"file:{copy}?immutable=1", uri=True)  # just the file, no log
                try:
                    ok = db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
                finally:
                    db.close()
                if ok:
                    return copy.read_bytes()
        time.sleep(1)
    raise RuntimeError(f"no consistent copy of {source} in {tries} tries")


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit(f"Usage: {sys.argv[0]} PROFILE_DIR LISTEN_ADDRESS PORT")
    profile, address, port = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
    snapshot(profile)  # fail at start, not at the bot's first request, if the profile isn't there

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/cookies.sqlite":
                self.send_error(404)
                return
            try:
                body = snapshot(profile)
            except (OSError, RuntimeError, sqlite3.Error) as exc:
                self.send_error(503, str(exc))
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.sqlite3")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args) -> None:
            print(f"{self.address_string()} {format % args}", file=sys.stderr, flush=True)

    print(f"Serving {profile / COOKIES} on http://{address}:{port}/cookies.sqlite", file=sys.stderr, flush=True)
    http.server.ThreadingHTTPServer((address, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
