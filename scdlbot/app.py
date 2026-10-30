"""Wire everything together: Telegram application, database, job queue and background tasks."""

import asyncio
import itertools
import logging
import shutil
import signal
import time
from urllib.parse import urljoin

import httpx
from telegram import MessageEntity, Update
from telegram.constants import ParseMode
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

from scdlbot import handlers, observe, routes
from scdlbot.config import Config
from scdlbot.db import DB
from scdlbot.handlers import Runtime
from scdlbot.jobs import JobQueue
from scdlbot.routes import RoutePool, RouteWatch

log = logging.getLogger(__name__)

# Edited messages and other update types are not even delivered to us:
ALLOWED_UPDATES = [Update.MESSAGE, Update.CHANNEL_POST, Update.CALLBACK_QUERY, Update.MY_CHAT_MEMBER]

LINKS = filters.Entity(MessageEntity.URL) | filters.Entity(MessageEntity.TEXT_LINK)
CAPTION_LINKS = filters.CaptionEntity(MessageEntity.URL) | filters.CaptionEntity(MessageEntity.TEXT_LINK)
MENTIONS = (
    filters.Entity(MessageEntity.MENTION)
    | filters.Entity(MessageEntity.TEXT_MENTION)
    | filters.CaptionEntity(MessageEntity.MENTION)
    | filters.CaptionEntity(MessageEntity.TEXT_MENTION)
)


def build(cfg: Config, pool: RoutePool) -> Application:
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
    app.bot_data["routes"] = pool
    not_edited = ~filters.UpdateType.EDITED
    app.add_handler(CommandHandler(["start", "help"], handlers.help_command, filters=not_edited))
    app.add_handler(CommandHandler("settings", handlers.settings_command, filters=not_edited))
    app.add_handler(CommandHandler("privacy", handlers.privacy_command, filters=not_edited))
    app.add_handler(CommandHandler("dl", handlers.dl_command, filters=not_edited & ~filters.FORWARDED))
    app.add_handler(CommandHandler("stats", handlers.stats_command, filters=not_edited))
    app.add_handler(MessageHandler(not_edited & ~filters.COMMAND & ((filters.TEXT & LINKS) | (filters.CAPTION & CAPTION_LINKS)), handlers.links_message))
    app.add_handler(MessageHandler(not_edited & ~filters.COMMAND & filters.REPLY & MENTIONS, handlers.mention_reply))  # "@bot" in reply to links
    app.add_handler(CallbackQueryHandler(handlers.settings_button, pattern=r"^settings:(dl|ask|captions|unknown_sites|mp3|close)$"))
    app.add_handler(CallbackQueryHandler(handlers.ask_button, pattern=r"^ask:(dl|no):-?\d+$"))
    app.add_handler(CallbackQueryHandler(handlers.old_button))
    app.add_handler(ChatMemberHandler(handlers.my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_error_handler(handlers.error_handler)
    return app


async def post_init(app: Application) -> None:
    cfg: Config = app.bot_data["cfg"]
    # Leftovers of jobs killed by a crash. Only our numbered job dirs, in case DL_DIR points somewhere shared:
    cfg.dl_dir.mkdir(parents=True, exist_ok=True)
    cfg.cache_dir.mkdir(exist_ok=True)
    for path in cfg.dl_dir.iterdir():
        if path.is_dir() and path.name.isdigit():
            shutil.rmtree(path, ignore_errors=True)
    db = await DB.open(cfg.db_path)
    http = httpx.AsyncClient(headers={"User-Agent": routes.BROWSER_UA})
    pool: RoutePool = app.bot_data["routes"]
    for cookies in pool.cookies():
        await cookies.refresh(http)
    queue = JobQueue(cfg, db, app.bot, pool)
    rt = Runtime(cfg, db, queue, http)
    app.bot_data["runtime"] = rt
    if cfg.owner_id:
        logging.getLogger().addHandler(observe.OwnerAlerts(app.bot, cfg.owner_id))
    observe.start_metrics_server(cfg.metrics_host, cfg.metrics_port)
    await queue.resume()
    queue.start()
    rt.tasks = [asyncio.create_task(coro) for coro in (watchdog(rt), housekeeping(rt), refresh_cookies(rt), watch_sites(rt, app.bot))]
    asyncio.get_running_loop().add_signal_handler(signal.SIGHUP, lambda: rt.tasks.append(asyncio.create_task(reload_routes(rt))))
    observe.sd_notify("READY=1")
    log.info("@%s is running with %d+%d workers, Bot API %s%s", app.bot.username, cfg.workers, cfg.youtube_workers, cfg.tg_api, " (local mode)" if cfg.local_mode else "")
    log.info("Routes: %s", describe_routes(pool))


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
            status = ", ".join(f"{pool}: queue {q.qsize()}, busy {rt.queue.busy[pool]}/{rt.queue.pools[pool]}" for pool, q in rt.queue.queues.items())
            observe.sd_notify(f"WATCHDOG=1\nSTATUS={status}")
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
            observe.set_languages(await rt.db.languages())
            if run % 12 == 0:
                await rt.db.prune(rt.cfg.jobs_retention_days)
        except Exception:
            log.exception("Housekeeping failed")
        await asyncio.sleep(300)


async def watch_sites(rt: Runtime, bot) -> None:
    """Every 5 minutes: leave out routes that a site keeps blocking, tell the owner about that,
    and when a site starts failing a lot whatever the route (and when it recovers)."""
    cfg = rt.cfg
    site_watch = observe.SiteWatch(cfg.alert_min_jobs, cfg.alert_failure_ratio, cfg.alert_window)
    route_watch = RouteWatch(rt.queue.routes, cfg.alert_min_jobs, cfg.alert_failure_ratio, cfg.route_exclusion)
    while True:
        await asyncio.sleep(300)
        try:
            messages = route_watch.check(time.time())
            results = await rt.db.recent_results(int(time.time()) - cfg.alert_window)
            messages += site_watch.check(results, time.monotonic(), rt.queue.last_errors)
            for text in messages:
                log.warning("Site alert: %s", text)
                if cfg.owner_id:
                    await bot.send_message(cfg.owner_id, text, parse_mode=ParseMode.HTML)
        except Exception as exc:
            log.warning("Site watch failed: %s", exc)  # not an error: that would alert the owner about the alerting


async def refresh_cookies(rt: Runtime) -> None:
    """Cookies that come from a URL are downloaded again every COOKIES_REFRESH seconds."""
    while True:
        await asyncio.sleep(rt.cfg.cookies_refresh)
        for cookies in rt.queue.routes.cookies():
            await cookies.refresh(rt.http)


def describe_routes(pool: RoutePool) -> str:
    sites = "; ".join(f"{site}: {', '.join(names)}" for site, names in pool.sites.items())
    login = "; ".join(f"{site} login: {', '.join(names)}" for site, names in pool.login.items())
    return "; ".join(part for part in (", ".join(route.describe() for route in pool.routes.values()), sites, login) if part)


async def reload_routes(rt: Runtime) -> None:
    """On SIGHUP (systemctl reload scdlbot): read ROUTES_FILE again. Running jobs keep the route they have."""
    if not rt.cfg.routes_file:
        log.info("SIGHUP: no ROUTES_FILE to reload")
        return
    try:
        new_routes, sites, login = routes.load(rt.cfg)
    except routes.RoutesError as exc:
        log.error("Routes not reloaded, still using the old ones: %s", exc)  # an error, so the owner hears about it
        return
    rt.queue.routes.update(new_routes, sites, login)
    for cookies in rt.queue.routes.cookies():
        await cookies.refresh(rt.http)
    log.info("Routes reloaded: %s", describe_routes(rt.queue.routes))


def run(cfg: Config) -> None:
    if not cfg.token:
        raise SystemExit("TG_BOT_TOKEN is not set")
    observe.init_sentry(cfg.sentry_dsn)
    try:
        pool = RoutePool(*routes.load(cfg), window=cfg.alert_window)
    except routes.RoutesError as exc:
        raise SystemExit(f"Routes: {exc}") from None
    app = build(cfg, pool)
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
