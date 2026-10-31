"""SQLite storage: chat settings, jobs (the durable queue and raw stats), daily stats.

Timestamps are unix seconds, days are UTC 'YYYY-MM-DD'. Handy queries:
    sqlite3 scdlbot.sqlite3 "SELECT status, count(*) FROM jobs WHERE created_at > strftime('%s', 'now', '-1 day') GROUP BY 1"
    sqlite3 scdlbot.sqlite3 "SELECT day, count(*) FROM user_days GROUP BY 1 ORDER BY 1 DESC LIMIT 30"
"""

import time
from dataclasses import dataclass, fields
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite

# Each item is one schema version, applied in order and tracked in PRAGMA user_version. Only ever append.
MIGRATIONS = (
    """
    CREATE TABLE chats (
        chat_id INTEGER PRIMARY KEY,
        type TEXT NOT NULL,
        mode TEXT,                -- NULL: default ('dl' in private chats, 'ask' in groups)
        captions INTEGER,         -- NULL: default (off for NO_FLOOD_CHAT_IDS)
        unknown_sites INTEGER NOT NULL DEFAULT 0,
        first_seen INTEGER NOT NULL,
        last_seen INTEGER NOT NULL,
        left_at INTEGER           -- bot was removed or blocked
    );
    CREATE TABLE jobs (
        id INTEGER PRIMARY KEY,
        created_at INTEGER NOT NULL,
        started_at INTEGER,
        finished_at INTEGER,
        chat_id INTEGER NOT NULL,
        chat_type TEXT NOT NULL,
        user_id INTEGER,
        message_id INTEGER NOT NULL,  -- the message with the link
        wait_message_id INTEGER,      -- our "wait a bit" or "what to do?" message
        url TEXT NOT NULL,
        site TEXT NOT NULL,
        kind TEXT NOT NULL,           -- audio | video
        captions INTEGER NOT NULL,
        status TEXT NOT NULL,         -- asked > queued > running > done | partial | failed | timeout | cancelled | expired
        error TEXT,
        items INTEGER NOT NULL DEFAULT 0,
        bytes INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX jobs_by_status ON jobs (status, created_at);
    CREATE INDEX jobs_by_message ON jobs (chat_id, message_id);
    CREATE TABLE user_days (
        day TEXT NOT NULL,
        user_id INTEGER NOT NULL,
        PRIMARY KEY (day, user_id)
    ) WITHOUT ROWID;
    CREATE TABLE daily (
        day TEXT NOT NULL,
        site TEXT NOT NULL,
        kind TEXT NOT NULL,
        status TEXT NOT NULL,
        chat_type TEXT NOT NULL,
        jobs INTEGER NOT NULL DEFAULT 0,
        items INTEGER NOT NULL DEFAULT 0,
        bytes INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (day, site, kind, status, chat_type)
    ) WITHOUT ROWID;
    """,
    # Telegram app language of the user (language part of the IETF tag: "en", "ru", "pt"), for stats:
    "ALTER TABLE user_days ADD COLUMN lang TEXT;",
    # "Always MP3" chat setting, copied to each job when it's created:
    "ALTER TABLE chats ADD COLUMN mp3 INTEGER NOT NULL DEFAULT 0; ALTER TABLE jobs ADD COLUMN mp3 INTEGER NOT NULL DEFAULT 0;",
    # 1: the row was added from an outside list of user IDs (users of the old bot), not by the bot itself; it stays 1 after they come back:
    "ALTER TABLE chats ADD COLUMN imported INTEGER NOT NULL DEFAULT 0;",
    # The route (proxy or source IP, see routes.py) the job's download went through, the last one if it was tried again:
    "ALTER TABLE jobs ADD COLUMN route TEXT;",
)

ACTIVE = ("queued", "running")


def now() -> int:
    return int(time.time())


def today(days_ago: int = 0) -> str:
    return (datetime.now(UTC) - timedelta(days=days_ago)).strftime("%Y-%m-%d")


def language(code: str | None) -> str | None:
    """Telegram gives the user's app language as an IETF tag ("en", "pt-br", "zh-hans"): keep the language part.

    That's the closest thing to a country Telegram tells bots.
    """
    lang = (code or "").split("-")[0].lower()
    return lang if lang.isalpha() and 2 <= len(lang) <= 3 else None


@dataclass
class Job:
    chat_id: int
    chat_type: str
    user_id: int | None
    message_id: int
    url: str
    site: str
    kind: str
    captions: bool
    status: str
    wait_message_id: int | None = None
    mp3: bool = False  # convert audio to mp3 even when the original is m4a
    route: str | None = None
    id: int | None = None
    created_at: int = 0
    started_at: int | None = None
    finished_at: int | None = None
    error: str | None = None
    items: int = 0
    bytes: int = 0

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> Job:
        return cls(**dict(row))

    @property
    def owner(self) -> int:
        """Whose limits this job counts against: the user, or the chat for channel posts."""
        return self.user_id or self.chat_id


JOB_COLUMNS = [f.name for f in fields(Job) if f.name != "id"]


@dataclass(frozen=True)
class ChatSettings:
    mode: str  # "dl" | "ask"
    captions: bool
    unknown_sites: bool
    mp3: bool = False


class DB:
    def __init__(self, conn: aiosqlite.Connection):
        self.conn = conn

    @classmethod
    async def open(cls, path: Path | str) -> DB:
        conn = await aiosqlite.connect(path, isolation_level=None)  # autocommit; every statement is its own transaction
        conn.row_factory = aiosqlite.Row
        await conn.executescript("PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA busy_timeout=5000;")
        db = cls(conn)
        (version,) = (await db._all("PRAGMA user_version"))[0]
        for number, script in enumerate(MIGRATIONS[version:], start=version + 1):
            await conn.executescript(f"BEGIN; {script}; PRAGMA user_version = {number}; COMMIT;")
        return db

    async def close(self) -> None:
        await self.conn.close()

    async def _all(self, sql: str, params: tuple = ()) -> list[aiosqlite.Row]:
        async with self.conn.execute(sql, params) as cursor:
            return list(await cursor.fetchall())

    async def _run(self, sql: str, params: tuple = ()) -> int:
        async with self.conn.execute(sql, params) as cursor:
            return cursor.rowcount

    # Chats and users

    async def touch_chat(self, chat_id: int, chat_type: str, default_captions: bool) -> ChatSettings:
        (row,) = await self._all(
            "INSERT INTO chats (chat_id, type, first_seen, last_seen) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (chat_id) DO UPDATE SET type = excluded.type, last_seen = excluded.last_seen, left_at = NULL "
            "RETURNING mode, captions, unknown_sites, mp3",
            (chat_id, chat_type, now(), now()),
        )
        return ChatSettings(
            mode=row["mode"] or ("dl" if chat_type == "private" else "ask"),
            captions=default_captions if row["captions"] is None else bool(row["captions"]),
            unknown_sites=bool(row["unknown_sites"]),
            mp3=bool(row["mp3"]),
        )

    async def set_chat(self, chat_id: int, *, mode: str | None = None, captions: bool | None = None, unknown_sites: bool | None = None, mp3: bool | None = None) -> None:
        values = {"mode": mode, "captions": captions, "unknown_sites": unknown_sites, "mp3": mp3}
        for column, value in values.items():
            if value is not None:
                await self._run(f"UPDATE chats SET {column} = ? WHERE chat_id = ?", (value, chat_id))

    async def chat_left(self, chat_id: int) -> None:
        await self._run("UPDATE chats SET left_at = ? WHERE chat_id = ?", (now(), chat_id))

    async def touch_user(self, user_id: int, language_code: str | None = None) -> None:
        await self._run(
            "INSERT INTO user_days (day, user_id, lang) VALUES (?, ?, ?) ON CONFLICT DO UPDATE SET lang = excluded.lang WHERE lang IS NULL AND excluded.lang IS NOT NULL",
            (today(), user_id, language(language_code)),
        )

    # Jobs

    async def add_job(self, job: Job) -> Job:
        job.created_at = now()
        placeholders = ", ".join("?" * len(JOB_COLUMNS))
        sql = f"INSERT INTO jobs ({', '.join(JOB_COLUMNS)}) VALUES ({placeholders}) RETURNING id"
        ((job.id,),) = await self._all(sql, tuple(getattr(job, c) for c in JOB_COLUMNS))
        return job

    async def queue_asked(self, chat_id: int, message_id: int, wait_message_id: int) -> list[Job]:
        rows = await self._all(
            "UPDATE jobs SET status = 'queued', wait_message_id = ? WHERE chat_id = ? AND message_id = ? AND status = 'asked' RETURNING *",
            (wait_message_id, chat_id, message_id),
        )
        return sorted((Job.from_row(r) for r in rows), key=lambda j: j.id)

    async def cancel_asked(self, chat_id: int, message_id: int) -> int:
        return await self._run(
            "UPDATE jobs SET status = 'cancelled', finished_at = ? WHERE chat_id = ? AND message_id = ? AND status = 'asked'",
            (now(), chat_id, message_id),
        )

    async def start_job(self, job: Job) -> None:
        job.status, job.started_at = "running", now()
        await self._run("UPDATE jobs SET status = ?, started_at = ?, route = ? WHERE id = ?", (job.status, job.started_at, job.route, job.id))

    async def finish_job(self, job: Job) -> None:
        job.finished_at = now()
        await self._run(
            "UPDATE jobs SET status = ?, error = ?, items = ?, bytes = ?, finished_at = ?, route = ? WHERE id = ?",
            (job.status, job.error, job.items, job.bytes, job.finished_at, job.route, job.id),
        )
        await self._run(
            "INSERT INTO daily (day, site, kind, status, chat_type, jobs, items, bytes) VALUES (?, ?, ?, ?, ?, 1, ?, ?) "
            "ON CONFLICT DO UPDATE SET jobs = jobs + 1, items = items + excluded.items, bytes = bytes + excluded.bytes",
            (today(), job.site, job.kind, job.status, job.chat_type, job.items, job.bytes),
        )

    async def requeue(self, job: Job) -> None:
        """Put an interrupted job back, so it runs again after a restart."""
        job.status = "queued"
        await self._run("UPDATE jobs SET status = 'queued', started_at = NULL WHERE id = ?", (job.id,))

    async def active_jobs_for_message(self, chat_id: int, message_id: int) -> int:
        rows = await self._all("SELECT count(*) FROM jobs WHERE chat_id = ? AND message_id = ? AND status IN (?, ?)", (chat_id, message_id, *ACTIVE))
        return rows[0][0]

    async def resume(self, max_age: int) -> list[Job]:
        """Jobs to run again after a restart; older ones are given up."""
        await self._run("UPDATE jobs SET status = 'queued', started_at = NULL WHERE status = 'running'")
        await self._run("UPDATE jobs SET status = 'expired', finished_at = ? WHERE status = 'queued' AND created_at < ?", (now(), now() - max_age))
        return [Job.from_row(r) for r in await self._all("SELECT * FROM jobs WHERE status = 'queued' ORDER BY id")]

    async def prune(self, retention_days: int, ask_ttl: int = 48 * 3600) -> None:
        await self._run("UPDATE jobs SET status = 'expired', finished_at = ? WHERE status = 'asked' AND created_at < ?", (now(), now() - ask_ttl))
        if retention_days > 0:
            await self._run(
                "DELETE FROM jobs WHERE created_at < ? AND status NOT IN ('asked', 'queued', 'running')",
                (now() - retention_days * 86400,),
            )

    # Stats

    async def active_users(self) -> dict[str, int]:
        result = {}
        for period, days in (("day", 1), ("week", 7), ("month", 30)):
            rows = await self._all("SELECT count(DISTINCT user_id) FROM user_days WHERE day > ?", (today(days),))
            result[period] = rows[0][0]
        return result

    async def languages(self, days: int = 30) -> list[tuple[str, int]]:
        """Distinct users by app language over the last days, most common first ("" if Telegram didn't say)."""
        rows = await self._all("SELECT coalesce(lang, ''), count(DISTINCT user_id) FROM user_days WHERE day > ? GROUP BY 1 ORDER BY 2 DESC, 1", (today(days),))
        return [(r[0], r[1]) for r in rows]

    async def jobs_by_status(self, since_day: str) -> dict[str, int]:
        rows = await self._all("SELECT status, sum(jobs) FROM daily WHERE day >= ? GROUP BY status ORDER BY 2 DESC", (since_day,))
        return {r[0]: r[1] for r in rows}

    async def recent_results(self, since: int) -> list[tuple[str, str | None, int]]:
        """(site, error code, jobs) for jobs created since then and already finished."""
        rows = await self._all(
            "SELECT site, error, count(*) FROM jobs WHERE status IN ('done', 'partial', 'failed', 'timeout') AND created_at > ? GROUP BY site, error",
            (since,),
        )
        return [(r[0], r[1], r[2]) for r in rows]

    async def top_sites(self, since_day: str, limit: int = 10) -> list[tuple[str, int, int]]:
        rows = await self._all(
            "SELECT site, sum(jobs), sum(CASE WHEN status = 'done' THEN jobs ELSE 0 END) FROM daily WHERE day >= ? GROUP BY site ORDER BY 2 DESC LIMIT ?",
            (since_day, limit),
        )
        return [(r[0], r[1], r[2]) for r in rows]
