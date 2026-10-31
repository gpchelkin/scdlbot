"""scdlbot: run the bot, or `scdlbot fetch URL` to download one link like the bot does, without Telegram."""

import argparse
import asyncio
import json
import logging
from contextlib import aclosing
from dataclasses import asdict
from pathlib import Path

import httpx

from scdlbot import routes
from scdlbot.config import Config
from scdlbot.links import AUDIO, PHOTO, VIDEO, classify, site_by_name
from scdlbot.media import ensure_h264, ensure_jpeg, split
from scdlbot.ytdlp import DownloadError, download, to_item


async def fetch(url: str, kind: str | None, cfg: Config, mp3: bool = False, route_name: str | None = None) -> None:
    link = classify(url, cfg, unknown_sites=True)
    kind = kind or (link.kind if link else AUDIO)
    site = link.site if link else ""
    try:
        pool = routes.RoutePool(*routes.load(cfg))
    except routes.RoutesError as exc:
        raise SystemExit(f"Routes: {exc}") from None
    if route_name and route_name not in pool.routes:
        raise SystemExit(f"No route {route_name!r}; routes: {', '.join(pool.routes)}")
    route = pool.routes[route_name] if route_name else pool.pick(site)
    print(f"Route: {route.describe()}")
    out = Path("downloads").absolute()
    out.mkdir(exist_ok=True)
    cfg.cache_dir.mkdir(parents=True, exist_ok=True)
    if route.cookies:
        async with httpx.AsyncClient() as client:
            await route.cookies.refresh(client)
    photos = bool((known := site_by_name(site)) and known.photos)
    try:
        async with aclosing(download(url, kind, out, cfg, route.ytdlp_args(out), photos, mp3)) as infos:
            async for info in infos:
                item = to_item(info, kind, site)
                if item.kind == VIDEO:
                    item = await ensure_h264(item, cfg)
                elif item.kind == PHOTO:
                    item = await ensure_jpeg(item, cfg)
                for part in await split(item, cfg):
                    print(json.dumps(asdict(part) | {"path": str(part.path), "size": part.size}, ensure_ascii=False))
    except DownloadError as exc:
        print(f"Download error: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="scdlbot", description=__doc__)
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("run", help="run the bot (default)")
    fetch_parser = commands.add_parser("fetch", help="download one URL into ./downloads like the bot does")
    fetch_parser.add_argument("url")
    fetch_parser.add_argument("--kind", choices=[AUDIO, VIDEO], help="override audio/video detection")
    fetch_parser.add_argument("--mp3", action="store_true", help="convert audio to mp3, like the Always MP3 setting")
    fetch_parser.add_argument("--route", help="download through this route (see ROUTES_FILE); default: the site's first route")
    args = parser.parse_args()

    cfg = Config.from_env()
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=cfg.loglevel)
    for noisy in ("httpx", "httpcore", "apscheduler", "aiosqlite"):
        logging.getLogger(noisy).setLevel(max(logging.WARNING, logging.getLogger().level))

    if args.command == "fetch":
        asyncio.run(fetch(args.url, args.kind, cfg, args.mp3, args.route))
    else:
        from scdlbot.app import run

        run(cfg)


if __name__ == "__main__":
    main()
