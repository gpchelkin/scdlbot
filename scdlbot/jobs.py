"""The download queue: admission limits, a fixed pool of workers, and the pipeline of one job.

Pipeline: yt-dlp downloads -> ffmpeg fixes (h264, split) -> Telegram upload, file by file as they finish.
"""

import asyncio
import logging
import shutil
import time
from collections import Counter
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, aclosing, suppress
from pathlib import Path

import httpx
from telegram import Bot, ReplyParameters
from telegram.constants import ParseMode
from telegram.error import Forbidden, TelegramError

from scdlbot import observe, texts, twitter
from scdlbot.config import Config
from scdlbot.db import DB, Job
from scdlbot.links import AUDIO, PHOTO, SITES, VIDEO, site_by_name
from scdlbot.media import ensure_h264, ensure_jpeg, split
from scdlbot.routes import RETRY_ERRORS, Route, RoutePool, site_label
from scdlbot.sender import Sender
from scdlbot.ytdlp import DownloadError, download, to_item

log = logging.getLogger(__name__)

POOLS = {site.name: site.pool for site in SITES}
RESUME_MAX_AGE = 3600  # after a restart, re-run queued jobs younger than this


def pool_for(site: str) -> str:
    return POOLS.get(site, "main")


class JobQueue:
    """Jobs wait in per-pool queues, each pool has its own workers.

    YouTube gets a small pool of its own: every YouTube download runs deno (~250 MB) to solve JS challenges,
    so their number must be limited, without making jobs from other sites wait behind them.
    """

    def __init__(
        self, cfg: Config, db: DB, bot: Bot, routes: RoutePool,
        http_client: Callable[[Route], AbstractAsyncContextManager[httpx.AsyncClient]] = Route.http_client,
    ):  # fmt: skip
        self.cfg, self.db, self.bot, self.routes, self.http_client = cfg, db, bot, routes, http_client
        self.pools = {"main": cfg.workers, "youtube": cfg.youtube_workers}  # pool name: number of workers
        self.queues: dict[str, asyncio.Queue[Job]] = {pool: asyncio.Queue() for pool in self.pools}
        self.busy: Counter[str] = Counter()  # running jobs by pool
        self.active: Counter[int] = Counter()  # queued + running jobs by owner
        self.last_progress = time.monotonic()
        self.workers: list[asyncio.Task] = []
        self.last_errors: dict[str, str] = {}  # site: the latest download error, for owner alerts

    def size(self) -> int:
        return sum(queue.qsize() for queue in self.queues.values())

    def busy_total(self) -> int:
        return sum(self.busy.values())

    def _update_gauges(self) -> None:
        for pool, queue in self.queues.items():
            observe.QUEUE_SIZE.labels(pool).set(queue.qsize())
            observe.WORKERS_BUSY.labels(pool).set(self.busy[pool])

    # Used by handlers

    def room_for(self, owner: int, wanted: int) -> tuple[int, str | None]:
        """How many of the wanted jobs this owner may add now, and why not all of them."""
        if self.size() >= self.cfg.max_queue:
            return 0, "queue"
        free = self.cfg.max_user_jobs - self.active[owner]
        return (max(0, min(wanted, free)), None if free >= wanted else "user")

    def waiting(self, site: str) -> int:
        """Jobs ahead of a new one from this site, if all workers of its pool are busy."""
        pool = pool_for(site)
        return self.queues[pool].qsize() + 1 if self.busy[pool] >= self.pools[pool] else 0

    def put(self, job: Job) -> None:
        self.active[job.owner] += 1
        self.queues[pool_for(job.site)].put_nowait(job)
        self._update_gauges()

    # Lifecycle

    async def resume(self) -> None:
        jobs = await self.db.resume(RESUME_MAX_AGE)
        for job in jobs:
            self.put(job)
        if jobs:
            log.info("Resumed %d jobs interrupted by restart", len(jobs))

    def start(self) -> None:
        self.workers = [asyncio.create_task(self._worker(pool), name=f"{pool}-{n}") for pool, count in self.pools.items() for n in range(count)]

    async def stop(self) -> None:
        for worker in self.workers:
            worker.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)

    def healthy(self) -> bool:
        """False if there is work but nothing moved for longer than any job may take: then we are stuck."""
        stalled = time.monotonic() - self.last_progress > self.cfg.job_timeout + 60
        return not (stalled and (self.busy_total() or self.size()))

    # Workers

    async def _worker(self, pool: str) -> None:
        while True:
            job = await self.queues[pool].get()
            self.busy[pool] += 1
            self._update_gauges()
            try:
                await self._run(job)
            except asyncio.CancelledError:
                await self.db.requeue(job)  # shutting down: it runs again after restart
                raise
            except Exception:
                log.exception("Job %s crashed: %s", job.id, job.url)
            finally:
                self.busy[pool] -= 1
                self._update_gauges()
                self.active[job.owner] -= 1
                if self.active[job.owner] <= 0:
                    del self.active[job.owner]
                self.last_progress = time.monotonic()

    async def _run(self, job: Job) -> None:
        started = time.monotonic()
        observe.QUEUE_WAIT_SECONDS.observe(max(0, time.time() - job.created_at))
        route = self.routes.pick(job.site)
        job.route = route.name
        await self.db.start_job(job)
        self.last_progress = time.monotonic()
        job_dir = self.cfg.dl_dir / str(job.id)
        job_dir.mkdir(parents=True, exist_ok=True)
        sender = Sender(self.bot, self.cfg, job)
        error, total = None, None
        try:
            try:
                async with asyncio.timeout(self.cfg.job_timeout):
                    total = await self._download_through(job, job_dir, sender, route)
            except DownloadError as exc:
                error = exc.code
                self.last_errors[job.site] = str(exc)
                log.info("Job %s: %s", job.id, exc)
            except TimeoutError:
                error = "timeout"
                self.routes.record(job.site, job.route or "", "timeout")
            await sender.flush()  # send what we've got, even after an error
        except Forbidden as exc:
            log.info("Job %s: bot can't write to chat %s: %s", job.id, job.chat_id, exc)
            await self.db.chat_left(job.chat_id)
            error = "forbidden"
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)

        if error == "forbidden":
            job.status = "cancelled"
        elif sender.sent:
            job.status = "partial" if error or sender.failed else "done"
        else:
            job.status = "timeout" if error == "timeout" else "failed"
        job.error = error or ("send" if sender.failed else None)
        job.items, job.bytes = sender.sent, sender.bytes
        await self.db.finish_job(job)

        observe.JOBS.labels(site_label(job.site), job.kind, job.status, job.route).inc()
        observe.JOB_SECONDS.labels(job.kind).observe(time.monotonic() - started)
        if job.error:
            observe.DOWNLOAD_ERRORS.labels(job.error, site_label(job.site), job.route).inc()
        log.info("Job %s %s in %.0fs via %s: %s, %d files, error %s", job.id, job.status, time.monotonic() - started, job.route, job.url, job.items, job.error)
        if job.status != "cancelled":
            await self._report(job, total)
            await self._delete_wait_message(job)

    async def _download_through(self, job: Job, job_dir: Path, sender: Sender, route: Route) -> int | None:
        """Download through the job's route. If the site blocks it or it can't connect before anything was sent,
        try once more through another route of the site."""
        try:
            total = await self._download(job, job_dir, sender, route)
        except DownloadError as exc:
            self.routes.record(job.site, route.name, exc.code, str(exc))
            other = self.routes.pick(job.site, avoid=route.name)
            if exc.code not in RETRY_ERRORS or other.name == route.name or sender.sent or sender.failed or sender.album:
                raise
            log.info("Job %s: %s via %s, trying again via %s", job.id, exc.code, route.name, other.name)
            observe.ROUTE_RETRIES.labels(site_label(job.site), route.name, exc.code).inc()
            shutil.rmtree(job_dir, ignore_errors=True)
            job_dir.mkdir(parents=True)
            job.route = other.name
            try:
                total = await self._download(job, job_dir, sender, other)
            except DownloadError as again:
                self.routes.record(job.site, other.name, again.code, str(again))
                raise
            route = other
        self.routes.record(job.site, route.name, None)
        return total

    async def _download(self, job: Job, job_dir: Path, sender: Sender, route: Route) -> int | None:
        """Download and send files one by one. Returns the playlist size, if it was a playlist."""
        if job.site == "Twitter":
            async with self.http_client(route) as client:
                if (tweet := await twitter.fetch(job.url, client)) and not tweet.has_video:
                    # yt-dlp only knows videos: photos and text-only tweets come from Twitter's embed endpoint
                    if not tweet.photos:
                        await sender.send_text(twitter.text_item(tweet, job_dir))
                        return None
                    for item in await twitter.photo_items(tweet, job_dir, client):
                        await sender.add(item)
                    return None
        extra = route.ytdlp_args(job_dir)
        photos = bool((site := site_by_name(job.site)) and site.photos)
        total = None
        short = False
        url = twitter.for_ytdlp(job.url) if job.site == "Twitter" else job.url
        async with aclosing(download(url, job.kind, job_dir, self.cfg, extra, photos, bool(job.mp3))) as infos:
            async for info in infos:
                self.last_progress = time.monotonic()
                if job.kind == AUDIO and info.get("media_type") == "short" and info.get("playlist_index") is None:
                    # A short behind a youtu.be or watch?v= link: only yt-dlp can tell, so get it again as a video
                    Path(info["filepath"]).unlink(missing_ok=True)
                    short = True
                    break
                item = to_item(info, job.kind, job.site)
                total = item.playlist_count
                if item.kind == VIDEO:
                    item = await ensure_h264(item, self.cfg)
                elif item.kind == PHOTO:
                    item = await ensure_jpeg(item, self.cfg)
                parts = await split(item, self.cfg)
                if not parts:
                    log.warning("Job %s: could not split %s", job.id, item.path.name)
                    sender.failed += 1
                for part in parts:
                    await sender.add(part)
        if short:
            job.kind = VIDEO  # counted as a video in stats too
            return await self._download(job, job_dir, sender, route)
        return total

    async def _report(self, job: Job, total: int | None) -> None:
        """Tell the user what went wrong, or that a playlist was cut."""
        if job.status == "done":
            if not (total and total > self.cfg.max_playlist_items):
                return
            text = texts.CAPPED.format(sent=job.items, total=total)
        elif job.status == "partial":
            text = texts.ERRORS["partial"]
        else:
            text = texts.ERRORS.get(job.error or "", texts.ERRORS["failed"])
            text = text.format(max_mb=self.cfg.max_convert_file_size // 1_000_000)
        try:
            reply = ReplyParameters(job.message_id, allow_sending_without_reply=True)
            await self.bot.send_message(job.chat_id, text, parse_mode=ParseMode.HTML, reply_parameters=reply)
        except TelegramError as exc:
            log.info("Job %s: could not report: %s", job.id, exc)

    async def _delete_wait_message(self, job: Job) -> None:
        if job.wait_message_id and not await self.db.active_jobs_for_message(job.chat_id, job.message_id):
            with suppress(TelegramError):
                await self.bot.delete_message(job.chat_id, job.wait_message_id)
