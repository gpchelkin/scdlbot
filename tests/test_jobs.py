import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from scdlbot import jobs as jobs_module
from scdlbot.config import Config
from scdlbot.db import DB, Job
from scdlbot.jobs import JobQueue
from scdlbot.ytdlp import Cookies, DownloadError


class FakeBot:
    username, id = "scdlbot", 1

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        async def method(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return SimpleNamespace(message_id=99)  # like a sent Message

        return method

    def names(self):
        return [name for name, _, _ in self.calls]


def fake_download(count: int, error: str | None = None, delay: float = 0, playlist_count: int | None = None):
    async def download(url, kind, job_dir: Path, cfg, extra=()):
        for n in range(count):
            await asyncio.sleep(delay)
            path = job_dir / f"{n}.m4a"
            path.write_bytes(b"x" * 100)
            info = {"filepath": str(path), "title": f"Artist - Track {n}", "duration": 60}
            if count > 1 or playlist_count:
                info |= {"playlist_index": n + 1, "playlist_count": playlist_count or count}
            yield info
        if error:
            raise DownloadError(error)

    return download


@pytest.fixture
async def setup(tmp_path, monkeypatch):
    cfg = Config(dl_dir=tmp_path / "dl", max_playlist_items=12, job_timeout=5, workers=2, max_user_jobs=2)
    db = await DB.open(":memory:")
    bot = FakeBot()
    queue = JobQueue(cfg, db, bot, Cookies(cfg))

    async def run(download, **job_fields) -> Job:
        monkeypatch.setattr(jobs_module, "download", download)
        values = {"chat_id": 5, "chat_type": "private", "user_id": 7, "message_id": 10, "url": "https://youtu.be/x", "site": "YouTube", "kind": "audio", "captions": True}
        job = await db.add_job(Job(status="queued", wait_message_id=11, **(values | job_fields)))
        await queue._run(job)
        return job

    yield cfg, db, bot, queue, run
    await db.close()


async def test_single_track(setup):
    cfg, db, bot, queue, run = setup
    job = await run(fake_download(1))
    assert (job.status, job.items, job.error) == ("done", 1, None)
    assert bot.names() == ["send_audio", "delete_message"]
    _, _, kwargs = bot.calls[0]
    assert (kwargs["performer"], kwargs["title"]) == ("Artist", "Track 0")
    assert "got it from" in kwargs["caption"]
    assert not (cfg.dl_dir / str(job.id)).exists()  # job dir cleaned up


async def test_album_in_groups_of_ten_and_cap_message(setup):
    cfg, db, bot, queue, run = setup
    job = await run(fake_download(12, playlist_count=40))
    assert (job.status, job.items) == ("done", 12)
    assert bot.names() == ["send_media_group", "send_media_group", "send_message", "delete_message"]
    assert len(bot.calls[0][1][1]) == 10 and len(bot.calls[1][1][1]) == 2
    assert "12 of 40" in bot.calls[2][1][1]


async def test_partial_and_failed(setup):
    cfg, db, bot, queue, run = setup
    job = await run(fake_download(1, error="unavailable"))
    assert (job.status, job.error) == ("partial", "unavailable")
    job = await run(fake_download(0, error="region"))
    assert (job.status, job.error, job.items) == ("failed", "region", 0)
    assert "my country" in bot.calls[-2][1][1]


async def test_timeout_keeps_what_was_sent(setup):
    cfg, db, bot, queue, run = setup
    job = await run(fake_download(10, delay=0.6, playlist_count=10), url="https://youtu.be/slow")
    assert job.status == "partial" and job.error == "timeout" and 5 <= job.items <= 9


async def test_limits(setup):
    cfg, db, bot, queue, run = setup
    assert queue.room_for(7, 5) == (2, "user")
    queue.put(Job(5, "private", 7, 1, "u", "s", "audio", True, "queued"))
    queue.put(Job(5, "private", 7, 2, "u", "s", "audio", True, "queued"))
    assert queue.room_for(7, 1) == (0, "user")
    assert queue.room_for(8, 1) == (1, None)
