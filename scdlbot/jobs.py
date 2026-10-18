"""The download queue: admission limits, a fixed pool of workers, and the pipeline of one job.

Pipeline: yt-dlp downloads -> ffmpeg fixes (h264, split) -> Telegram upload, file by file as they finish.
"""

import asyncio
import logging
import random
import shutil
import time
from collections import Counter
from contextlib import aclosing, suppress

from telegram import Bot, ReplyParameters
from telegram.constants import ParseMode
from telegram.error import Forbidden, TelegramError

from scdlbot import observe, texts
from scdlbot.config import Config
from scdlbot.db import DB, Job
from scdlbot.links import SITES, VIDEO
from scdlbot.media import ensure_h264, split
from scdlbot.sender import Sender
from scdlbot.ytdlp import Cookies, DownloadError, download, to_item

log = logging.getLogger(__name__)

KNOWN_SITES = {site.name for site in SITES}  # metrics label "other" for the rest, to keep cardinality low
RESUME_MAX_AGE = 3600  # after a restart, re-run queued jobs younger than this


class JobQueue:
    def __init__(self, cfg: Config, db: DB, bot: Bot, cookies: Cookies):
        self.cfg, self.db, self.bot, self.cookies = cfg, db, bot, cookies
        self.queue: asyncio.Queue[Job] = asyncio.Queue()
        self.active: Counter[int] = Counter()  # queued + running jobs by owner
        self.busy = 0
        self.last_progress = time.monotonic()
        self.workers: list[asyncio.Task] = []

    # Used by handlers

    def room_for(self, owner: int, wanted: int) -> tuple[int, str | None]:
        """How many of the wanted jobs this owner may add now, and why not all of them."""
        if self.queue.qsize() >= self.cfg.max_queue:
            return 0, "queue"
        free = self.cfg.max_user_jobs - self.active[owner]
        return (max(0, min(wanted, free)), None if free >= wanted else "user")

    def waiting(self) -> int:
        """Jobs ahead of a new one, if all workers are busy."""
        return self.queue.qsize() + 1 if self.busy >= self.cfg.workers else 0

    def put(self, job: Job) -> None:
        self.active[job.owner] += 1
        self.queue.put_nowait(job)
        observe.QUEUE_SIZE.set(self.queue.qsize())

    # Lifecycle

    async def resume(self) -> None:
        jobs = await self.db.resume(RESUME_MAX_AGE)
        for job in jobs:
            self.put(job)
        if jobs:
            log.info("Resumed %d jobs interrupted by restart", len(jobs))

    def start(self) -> None:
        self.workers = [asyncio.create_task(self._worker(), name=f"worker-{n}") for n in range(self.cfg.workers)]

    async def stop(self) -> None:
        for worker in self.workers:
            worker.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)

    def healthy(self) -> bool:
        """False if there is work but nothing moved for longer than any job may take: then we are stuck."""
        stalled = time.monotonic() - self.last_progress > self.cfg.job_timeout + 60
        return not (stalled and (self.busy or self.queue.qsize()))

    # Workers

    async def _worker(self) -> None:
        while True:
            job = await self.queue.get()
            observe.QUEUE_SIZE.set(self.queue.qsize())
            self.busy += 1
            observe.WORKERS_BUSY.set(self.busy)
            try:
                await self._run(job)
            except asyncio.CancelledError:
                await self.db.requeue(job)  # shutting down: it runs again after restart
                raise
            except Exception:
                log.exception("Job %s crashed: %s", job.id, job.url)
            finally:
                self.busy -= 1
                observe.WORKERS_BUSY.set(self.busy)
                self.active[job.owner] -= 1
                if self.active[job.owner] <= 0:
                    del self.active[job.owner]
                self.last_progress = time.monotonic()

    async def _run(self, job: Job) -> None:
        started = time.monotonic()
        observe.QUEUE_WAIT_SECONDS.observe(max(0, time.time() - job.created_at))
        await self.db.start_job(job)
        self.last_progress = time.monotonic()
        job_dir = self.cfg.dl_dir / str(job.id)
        job_dir.mkdir(parents=True, exist_ok=True)
        sender = Sender(self.bot, self.cfg, job)
        error, total = None, None
        try:
            try:
                async with asyncio.timeout(self.cfg.job_timeout):
                    total = await self._download(job, job_dir, sender)
            except DownloadError as exc:
                error = exc.code
                log.info("Job %s: %s", job.id, exc)
            except TimeoutError:
                error = "timeout"
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

        observe.JOBS.labels(job.site if job.site in KNOWN_SITES else "other", job.kind, job.status).inc()
        observe.JOB_SECONDS.labels(job.kind).observe(time.monotonic() - started)
        if job.error:
            observe.DOWNLOAD_ERRORS.labels(job.error).inc()
        log.info("Job %s %s in %.0fs: %s, %d files, error %s", job.id, job.status, time.monotonic() - started, job.url, job.items, job.error)
        if job.status != "cancelled":
            await self._report(job, total)
            await self._delete_wait_message(job)

    async def _download(self, job: Job, job_dir, sender: Sender) -> int | None:
        """Download and send files one by one. Returns the playlist size, if it was a playlist."""
        extra = self.cookies.args(job_dir)
        proxy = random.choice(self.cfg.proxies) if self.cfg.proxies else None
        if proxy:
            extra += ["--proxy", proxy]
        if self.cfg.source_ips:
            extra += ["--source-address", random.choice(self.cfg.source_ips)]
        total = None
        async with aclosing(download(job.url, job.kind, job_dir, self.cfg, extra)) as infos:
            async for info in infos:
                self.last_progress = time.monotonic()
                item = to_item(info, job.kind)
                total = item.playlist_count
                if job.kind == VIDEO:
                    item = await ensure_h264(item, self.cfg)
                parts = await split(item, self.cfg)
                if not parts:
                    log.warning("Job %s: could not split %s", job.id, item.path.name)
                    sender.failed += 1
                for part in parts:
                    await sender.add(part)
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
