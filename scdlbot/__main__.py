"""scdlbot: run the bot, or `scdlbot fetch URL` to download one link like the bot does, without Telegram."""

import argparse
import asyncio
import json
import logging
from contextlib import aclosing
from dataclasses import asdict
from pathlib import Path

import httpx

from scdlbot.config import Config
from scdlbot.links import AUDIO, VIDEO, classify
from scdlbot.media import ensure_h264, split
from scdlbot.ytdlp import Cookies, DownloadError, download, to_item


async def fetch(url: str, kind: str | None, cfg: Config) -> None:
    link = classify(url, cfg, unknown_sites=True)
    kind = kind or (link.kind if link else AUDIO)
    out = Path("downloads").absolute()
    out.mkdir(exist_ok=True)
    cookies = Cookies(cfg)
    async with httpx.AsyncClient() as client:
        await cookies.refresh(client)
    try:
        async with aclosing(download(url, kind, out, cfg, cookies.args(out))) as infos:
            async for info in infos:
                item = to_item(info, kind)
                if kind == VIDEO:
                    item = await ensure_h264(item, cfg)
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
    args = parser.parse_args()

    cfg = Config.from_env()
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=cfg.loglevel)
    for noisy in ("httpx", "httpcore", "apscheduler", "aiosqlite"):
        logging.getLogger(noisy).setLevel(max(logging.WARNING, logging.getLogger().level))

    if args.command == "fetch":
        asyncio.run(fetch(args.url, args.kind, cfg))
    else:
        from scdlbot.app import run

        run(cfg)


if __name__ == "__main__":
    main()
