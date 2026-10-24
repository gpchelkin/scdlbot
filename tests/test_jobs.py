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
    async def download(url, kind, job_dir: Path, cfg, extra=(), photos=False, mp3=False):
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


async def test_youtube_has_its_own_pool(setup):
    cfg, db, bot, queue, run = setup
    queue.put(Job(5, "private", 7, 1, "https://youtu.be/x", "YouTube", "audio", True, "queued"))
    queue.put(Job(5, "private", 8, 2, "https://soundcloud.com/a/b", "SoundCloud", "audio", True, "queued"))
    assert (queue.queues["youtube"].qsize(), queue.queues["main"].qsize(), queue.size()) == (1, 1, 2)
    queue.busy["youtube"] = queue.pools["youtube"]  # all YouTube workers busy: new YouTube links wait, others don't
    assert queue.waiting("YouTube") == 2 and queue.waiting("SoundCloud") == 0


def fake_instagram_post(kinds: str):
    """kinds like "pvp": photo, video, photo; one entry each, as yt-dlp.download yields them."""

    async def download(url, kind, job_dir: Path, cfg, extra=(), photos=False, mp3=False):
        assert photos  # Instagram asks for photos
        for n, k in enumerate(kinds, start=1):
            path = job_dir / f"{n:03d}.{'jpg' if k == 'p' else 'mp4'}"
            path.write_bytes(b"x" * 100)
            info = {"filepath": str(path), "channel": "gpchelkin", "uploader": "George Pchelkin", "description": "Апшеронская <узкоколейка>"}
            if len(kinds) > 1:
                info |= {"playlist_index": n, "playlist_count": len(kinds)}
            if k == "p":
                info["_photo"] = True
            else:
                info |= {"vcodec": "avc1.4d401f", "width": 720, "height": 720}
            yield info

    return download


async def test_instagram_carousel_goes_out_as_one_album_with_author(setup):
    cfg, db, bot, queue, run = setup
    job = await run(fake_instagram_post("ppvp"), url="https://www.instagram.com/gpchelkin/p/B5P4ApFHE6_/?igsh=MWQ1ZGUx", site="Instagram", kind="video")
    assert (job.status, job.items) == ("done", 4)
    name, args, kwargs = bot.calls[0]
    assert name == "send_media_group"
    media = args[1]
    assert [type(m).__name__ for m in media] == ["InputMediaPhoto", "InputMediaPhoto", "InputMediaVideo", "InputMediaPhoto"]
    caption = media[0].caption
    assert 'Author: <b>George Pchelkin</b> (<a href="https://www.instagram.com/gpchelkin/">@gpchelkin</a>)' in caption
    assert "<blockquote expandable>Апшеронская &lt;узкоколейка&gt;</blockquote>" in caption
    # Every file starts with the invisible source link, without the sharer's tracking; the rest only on the first:
    hidden = '<a href="https://www.instagram.com/gpchelkin/p/B5P4ApFHE6_/">\u200b\u200b</a>'
    assert caption.startswith(hidden + "@scdlbot <i>got it from</i>")
    assert all(m.caption == hidden for m in media[1:])


async def test_instagram_single_photo(setup):
    cfg, db, bot, queue, run = setup
    job = await run(fake_instagram_post("p"), url="https://www.instagram.com/p/B2DWUSAn9E9/", site="Instagram", kind="video")
    assert job.status == "done"
    assert bot.names()[0] == "send_photo"
    assert "@gpchelkin" in bot.calls[0][2]["caption"]


async def test_source_links_follow_settings(tmp_path):
    from scdlbot.media import Item
    from scdlbot.sender import Sender

    url = "https://youtu.be/jNQXAC9IVRw?si=AbC"
    item = Item(tmp_path / "a.m4a", "audio", source_url="https://www.youtube.com/watch?v=jNQXAC9IVRw")
    no_captions = Job(5, "private", 7, 10, url, "YouTube", "audio", False, "running", id=1)
    caption = Sender(FakeBot(), Config(), no_captions)._caption(item)
    assert caption == '<a href="https://www.youtube.com/watch?v=jNQXAC9IVRw">\u200b\u200b</a>'
    assert Sender(FakeBot(), Config(hidden_source_links=False), no_captions)._caption(item) is None
    with_captions = Job(5, "private", 7, 10, url, "YouTube", "audio", True, "running", id=1)
    caption = Sender(FakeBot(), Config(normalize_urls=False, hidden_source_links=False), with_captions)._caption(item)
    assert caption == f'@scdlbot <i>got it from</i> <a href="{url}">YouTube</a>'


async def test_short_behind_a_youtu_be_link_goes_out_as_video(setup):
    cfg, db, bot, queue, run = setup
    kinds = []

    async def download(url, kind, job_dir: Path, cfg, extra=(), photos=False, mp3=False):
        kinds.append(kind)
        path = job_dir / ("short.mp4" if kind == "video" else "short.m4a")
        path.write_bytes(b"x" * 100)
        yield {"filepath": str(path), "title": "Short", "duration": 20, "media_type": "short", "vcodec": "avc1.4D401F"}

    job = await run(download)  # https://youtu.be/x, audio
    assert (job.status, job.kind, kinds) == ("done", "video", ["audio", "video"])
    assert bot.names() == ["send_video", "delete_message"]


def test_author_line():
    from scdlbot.media import Item
    from scdlbot.sender import author_line

    def line(**kw):
        return author_line(Item(Path("/a"), "video", **kw))

    assert line(author_name="NASA", author_handle="nasa", author_url="https://x.com/nasa") == 'Author: <a href="https://x.com/nasa">@nasa</a>'
    assert line(author_handle="a_b") == "Author: <code>@a_b</code>"
    assert line(author_name="Tom & Jerry") == "Author: <b>Tom &amp; Jerry</b>"
    assert line() is None


async def test_twitter_photos_and_text_via_embed_endpoint(tmp_path):
    import httpx

    from tests.test_twitter import PHOTO_TWEET, client_for

    text_only = PHOTO_TWEET | {"text": "just words", "display_text_range": [0, 10], "mediaDetails": []}
    cfg = Config(dl_dir=tmp_path / "dl")
    db = await DB.open(":memory:")
    bot = FakeBot()
    async with client_for({"111": PHOTO_TWEET, "222": text_only}) as http:
        queue = JobQueue(cfg, db, bot, Cookies(cfg), http)
        for twid in ("111", "222"):
            job = await db.add_job(Job(5, "private", 7, 10, f"https://x.com/u/status/{twid}", "Twitter", "video", True, "queued"))
            await queue._run(job)
            assert job.status == "done"
    album, text = bot.calls[0], bot.calls[1]
    assert album[0] == "send_media_group" and [type(m).__name__ for m in album[1][1]] == ["InputMediaPhoto", "InputMediaPhoto"]
    assert "got it from</i> <a" in album[1][1][0].caption and ">Twitter</a>" in album[1][1][0].caption
    assert text[0] == "send_message" and "just words" in text[1][1] and "@hairball1952" in text[1][1]
    await db.close()
    assert isinstance(http, httpx.AsyncClient)
