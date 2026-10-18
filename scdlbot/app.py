"""Wire everything together: Telegram application, database, job queue and background tasks."""

import asyncio
import itertools
import logging
import shutil
from urllib.parse import urljoin

import httpx
from telegram import MessageEntity, Update
from telegram.ext import (
    AIORateLimiter,
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from scdlbot import handlers, observe
from scdlbot.config import Config
from scdlbot.db import DB
from scdlbot.handlers import Runtime
from scdlbot.jobs import JobQueue
from scdlbot.ytdlp import Cookies

log = logging.getLogger(__name__)

# Edited messages and other update types are not even delivered to us:
ALLOWED_UPDATES = [Update.MESSAGE, Update.CHANNEL_POST, Update.CALLBACK_QUERY, Update.MY_CHAT_MEMBER]

LINKS = filters.Entity(MessageEntity.URL) | filters.Entity(MessageEntity.TEXT_LINK)
CAPTION_LINKS = filters.CaptionEntity(MessageEntity.URL) | filters.CaptionEntity(MessageEntity.TEXT_LINK)


def build(cfg: Config) -> Application:
    http_version = "1.1" if cfg.local_mode else "2"
    app = (
        ApplicationBuilder()
        .token(cfg.token)
        .base_url(f"{cfg.tg_api}/bot")
        .base_file_url(f"{cfg.tg_api}/file/bot")
        .local_mode(cfg.local_mode)
        .http_version(http_version)
        .get_updates_http_version(http_version)
        .connection_pool_size(cfg.workers + 32)
        .pool_timeout(10)
        .connect_timeout(10)
        .read_timeout(30)
        .write_timeout(30)
        .rate_limiter(AIORateLimiter(max_retries=3))
        .concurrent_updates(True)  # handlers never wait for downloads, so they can all run at once
        .post_init(post_init)
        .post_stop(post_stop)
        .post_shutdown(post_shutdown)
        .build()
    )
    app.bot_data["cfg"] = cfg
    not_edited = ~filters.UpdateType.EDITED
    app.add_handler(CommandHandler(["start", "help"], handlers.help_command, filters=not_edited))
    app.add_handler(CommandHandler("settings", handlers.settings_command, filters=not_edited))
    app.add_handler(CommandHandler("dl", handlers.dl_command, filters=not_edited & ~filters.FORWARDED))
    app.add_handler(CommandHandler("stats", handlers.stats_command, filters=not_edited))
    app.add_handler(MessageHandler(not_edited & ~filters.COMMAND & ((filters.TEXT & LINKS) | (filters.CAPTION & CAPTION_LINKS)), handlers.links_message))
    app.add_handler(CallbackQueryHandler(handlers.settings_button, pattern=r"^settings:(dl|ask|captions|unknown_sites|close)$"))
    app.add_handler(CallbackQueryHandler(handlers.ask_button, pattern=r"^ask:(dl|no):-?\d+$"))
    app.add_handler(CallbackQueryHandler(handlers.old_button))
    app.add_handler(ChatMemberHandler(handlers.my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_error_handler(handlers.error_handler)
    return app


async def post_init(app: Application) -> None:
    cfg: Config = app.bot_data["cfg"]
    # Leftovers of jobs killed by a crash. Only our numbered job dirs, in case DL_DIR points somewhere shared:
    cfg.dl_dir.mkdir(parents=True, exist_ok=True)
    for path in cfg.dl_dir.iterdir():
        if path.is_dir() and path.name.isdigit():
            shutil.rmtree(path, ignore_errors=True)
    db = await DB.open(cfg.db_path)
    http = httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"})
    cookies = Cookies(cfg)
    await cookies.refresh(http)
    queue = JobQueue(cfg, db, app.bot, cookies)
    rt = Runtime(cfg, db, queue, http)
    app.bot_data["runtime"] = rt
    if cfg.owner_id:
        logging.getLogger().addHandler(observe.OwnerAlerts(app.bot, cfg.owner_id))
    observe.start_metrics_server(cfg.metrics_host, cfg.metrics_port)
    await queue.resume()
    queue.start()
    rt.tasks = [asyncio.create_task(coro) for coro in (watchdog(rt), housekeeping(rt), refresh_cookies(rt, cookies))]
    observe.sd_notify("READY=1")
    log.info("@%s is running with %d workers, Bot API %s%s", app.bot.username, cfg.workers, cfg.tg_api, " (local mode)" if cfg.local_mode else "")


async def post_stop(app: Application) -> None:
    rt: Runtime = app.bot_data["runtime"]
    observe.sd_notify("STOPPING=1")
    for task in rt.tasks:
        task.cancel()
    await rt.queue.stop()  # interrupted jobs go back to the queue in the database


async def post_shutdown(app: Application) -> None:
    rt: Runtime = app.bot_data["runtime"]
    await rt.http.aclose()
    await rt.db.close()


async def watchdog(rt: Runtime) -> None:
    """Ping systemd's watchdog only while workers make progress, so a stuck bot gets restarted."""
    stuck = False
    while True:
        if rt.queue.healthy():
            observe.sd_notify(f"WATCHDOG=1\nSTATUS=queue {rt.queue.queue.qsize()}, busy {rt.queue.busy}/{rt.cfg.workers}")
            stuck = False
        elif not stuck:
            log.error("Workers made no progress for %d seconds, stopping watchdog pings so systemd restarts the bot", rt.cfg.job_timeout + 60)
            stuck = True
        await asyncio.sleep(observe.watchdog_interval())


async def housekeeping(rt: Runtime) -> None:
    """Active users gauges every 5 minutes, old rows pruned every hour."""
    for run in itertools.count():
        try:
            for period, count in (await rt.db.active_users()).items():
                observe.ACTIVE_USERS.labels(period).set(count)
            if run % 12 == 0:
                await rt.db.prune(rt.cfg.jobs_retention_days)
        except Exception:
            log.exception("Housekeeping failed")
        await asyncio.sleep(300)


async def refresh_cookies(rt: Runtime, cookies: Cookies) -> None:
    if not cookies.url:
        return
    while True:
        await asyncio.sleep(rt.cfg.cookies_refresh)
        await cookies.refresh(rt.http)


def run(cfg: Config) -> None:
    if not cfg.token:
        raise SystemExit("TG_BOT_TOKEN is not set")
    observe.init_sentry(cfg.sentry_dsn)
    app = build(cfg)
    common = {"allowed_updates": ALLOWED_UPDATES, "drop_pending_updates": cfg.drop_pending_updates}
    if cfg.webhook:
        app.run_webhook(
            listen=cfg.host,
            port=cfg.port,
            url_path=cfg.webhook_url_path,
            webhook_url=urljoin(cfg.webhook_url_root, cfg.webhook_url_path),
            secret_token=cfg.webhook_secret,
            cert=cfg.webhook_cert,
            key=cfg.webhook_key,
            max_connections=100,
            **common,
        )
    else:
        app.run_polling(**common)
