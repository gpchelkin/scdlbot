"""Prometheus metrics, systemd notifications, error alerts to the bot owner, optional Sentry."""

import asyncio
import html
import logging
import os
import socket
import time
from collections import Counter as Tally
from collections import defaultdict

from prometheus_client import Counter, Gauge, Histogram, start_http_server
from telegram import Bot
from telegram.constants import ParseMode

log = logging.getLogger(__name__)

UPDATES = Counter("scdlbot_updates", "Handled updates", ["type"])
JOBS = Counter("scdlbot_jobs", "Finished download jobs", ["site", "kind", "status"])
JOB_SECONDS = Histogram("scdlbot_job_duration_seconds", "Time from job start to finish", ["kind"], buckets=(5, 10, 20, 30, 60, 120, 300, 600, 1200, 1800, 3600))
QUEUE_WAIT_SECONDS = Histogram("scdlbot_queue_wait_seconds", "Time jobs wait in the queue", buckets=(1, 5, 10, 30, 60, 120, 300, 600, 1800))
QUEUE_SIZE = Gauge("scdlbot_queue_size", "Jobs waiting in the queue", ["pool"])
WORKERS_BUSY = Gauge("scdlbot_workers_busy", "Workers running a job", ["pool"])
ITEMS_SENT = Counter("scdlbot_items_sent", "Files sent", ["kind"])
BYTES_SENT = Counter("scdlbot_bytes_sent", "Bytes sent", ["kind"])
DOWNLOAD_ERRORS = Counter("scdlbot_download_errors", "Download errors", ["code"])
REENCODES = Counter("scdlbot_video_reencodes", "Videos re-encoded to h264 (Telegram iOS plays neither VP9 nor some HEVC)", ["codec", "result"])
TELEGRAM_ERRORS = Counter("scdlbot_telegram_errors", "Telegram API errors while sending", ["type"])
REJECTED = Counter("scdlbot_rejected", "Links not accepted because of limits", ["reason"])
ACTIVE_USERS = Gauge("scdlbot_active_users", "Distinct users who sent links or commands", ["period"])
USERS_BY_LANGUAGE = Gauge("scdlbot_active_users_by_language", "Distinct users in the last 30 days by Telegram app language", ["lang"])


def set_languages(counts: list[tuple[str, int]], top: int = 20) -> None:
    """Top languages get their own series, the rest is summed up as "other" to keep the number of series small."""
    USERS_BY_LANGUAGE.clear()  # languages that dropped out of the top must disappear
    for lang, count in counts[:top]:
        USERS_BY_LANGUAGE.labels(lang or "unknown").set(count)
    if rest := sum(count for _, count in counts[top:]):
        USERS_BY_LANGUAGE.labels("other").set(rest)


def start_metrics_server(host: str, port: int) -> None:
    if port:
        start_http_server(port, addr=host)
        log.info("Metrics on http://%s:%s/metrics", host, port)


def sd_notify(state: str) -> None:
    """Tell systemd about our state (READY=1, WATCHDOG=1, STATUS=...). No-op outside systemd."""
    address = os.getenv("NOTIFY_SOCKET")
    if not address:
        return
    if address.startswith("@"):
        address = "\0" + address[1:]  # abstract socket
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.sendto(state.encode(), address)
    except OSError as exc:
        log.warning("sd_notify failed: %s", exc)


def watchdog_interval() -> float:
    """Half of systemd's WatchdogSec, or 30 seconds."""
    usec = int(os.getenv("WATCHDOG_USEC", "0"))
    return usec / 2_000_000 if usec else 30


class OwnerAlerts(logging.Handler):
    """Forward ERROR logs (unexpected exceptions) to the bot owner, at most one message per minute."""

    def __init__(self, bot: Bot, owner_id: int, every: float = 60):
        super().__init__(logging.ERROR)
        self.setFormatter(logging.Formatter("%(name)s: %(message)s"))
        self.bot, self.owner_id, self.every = bot, owner_id, every
        self.loop = asyncio.get_running_loop()
        self.last = 0.0
        self.suppressed = 0
        self.tasks: set[asyncio.Task] = set()

    def emit(self, record: logging.LogRecord) -> None:
        if time.monotonic() - self.last < self.every:
            self.suppressed += 1
            return
        self.last = time.monotonic()
        text = self.format(record)[-3500:]
        if self.suppressed:
            text += f"\n\n(+{self.suppressed} more errors since the previous alert)"
            self.suppressed = 0
        self.loop.call_soon_threadsafe(self._send, f"⚠️ <b>scdlbot error</b>\n<pre>{html.escape(text)}</pre>")

    def _send(self, text: str) -> None:
        task = self.loop.create_task(self._send_async(text))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _send_async(self, text: str) -> None:
        try:
            await self.bot.send_message(self.owner_id, text, parse_mode=ParseMode.HTML)
        except Exception as exc:
            log.warning("Could not send an alert to the owner: %s", exc)


def init_sentry(dsn: str) -> None:
    if dsn:
        import sentry_sdk

        # The default logging integration turns ERROR logs into Sentry events.
        sentry_sdk.init(dsn=dsn, send_default_pii=False)
        log.info("Sentry enabled")


# Error codes that point at a problem with the site or with us (logged out, IP blocked, site changed),
# rather than with the link (deleted, private, DRM, too large...):
INFRA_ERRORS = frozenset({"login", "failed", "timeout", "send"})


class SiteWatch:
    """Notices when many downloads from one site fail at once, e.g. YouTube logged us out or SoundCloud blocked our IP."""

    def __init__(self, min_jobs: int, ratio: float, window: int, repeat: int = 3600):
        self.min_jobs, self.ratio, self.window, self.repeat = min_jobs, ratio, window, repeat
        self.down: dict[str, float] = {}  # failing sites: when the owner was last told

    def check(self, results: list[tuple[str, str | None, int]], now: float, last_errors: dict[str, str]) -> list[str]:
        """Messages for the owner, given (site, error code, jobs) of the last window."""
        by_site: dict[str, Tally] = defaultdict(Tally)
        for site, error, count in results:
            by_site[site][error] += count
        messages = []
        for site, codes in sorted(by_site.items()):
            total = sum(codes.values())
            failed = sum(count for code, count in codes.items() if code in INFRA_ERRORS)
            summary = f"{failed} of {total} downloads ({failed / total:.0%}) failed in the last {self.window // 60} min"
            if total >= self.min_jobs and failed / total >= self.ratio:
                if now - self.down.get(site, float("-inf")) >= self.repeat:
                    self.down[site] = now
                    reasons = ", ".join(f"{code} {count}" for code, count in codes.most_common() if code in INFRA_ERRORS)
                    text = f"⚠️ <b>{html.escape(site)}</b>: {summary}: {reasons}."
                    if last_error := last_errors.get(site):
                        text += f"\nLast error: <code>{html.escape(last_error[:400])}</code>"
                    messages.append(text)
            elif site in self.down and total >= 5 and failed / total < self.ratio / 2:
                del self.down[site]
                messages.append(f"✅ <b>{html.escape(site)}</b> is back to normal: {summary}.")
        return messages
