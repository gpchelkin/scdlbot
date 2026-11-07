import sqlite3

from scdlbot import observe
from scdlbot.db import DB, MIGRATIONS, Job
from scdlbot.db import now as db_now


def make_job(**kwargs) -> Job:
    values = {
        "chat_id": -1,
        "chat_type": "group",
        "user_id": 7,
        "message_id": 10,
        "url": "https://youtu.be/x",
        "site": "YouTube",
        "kind": "audio",
        "captions": True,
        "status": "queued",
    }
    return Job(**(values | kwargs))


async def test_chat_settings_defaults_and_updates():
    db = await DB.open(":memory:")
    settings = await db.touch_chat(1, "private", default_captions=True)
    assert (settings.mode, settings.captions, settings.unknown_sites) == ("ask_video", True, False)
    settings = await db.touch_chat(-5, "supergroup", default_captions=False)
    assert (settings.mode, settings.captions) == ("ask", False)
    await db.set_chat(-5, mode="dl", captions=True, unknown_sites=True)
    settings = await db.touch_chat(-5, "supergroup", default_captions=False)
    assert (settings.mode, settings.captions, settings.unknown_sites) == ("dl", True, True)
    await db.close()


async def test_ask_flow_and_stats():
    db = await DB.open(":memory:")
    await db.add_job(make_job(status="asked"))
    await db.add_job(make_job(status="asked", url="https://youtu.be/y"))
    jobs = await db.queue_asked(-1, 10, wait_message_id=11)
    assert [j.url for j in jobs] == ["https://youtu.be/x", "https://youtu.be/y"]
    assert all(j.status == "queued" and j.wait_message_id == 11 for j in jobs)
    assert await db.queue_asked(-1, 10, wait_message_id=11) == []  # second press does nothing
    assert await db.active_jobs_for_message(-1, 10) == 2

    await db.start_job(jobs[0])
    jobs[0].status, jobs[0].items, jobs[0].bytes, jobs[0].kinds = "done", 3, 1000, '{"photo": 2, "video": 1}'
    await db.finish_job(jobs[0])
    assert await db.active_jobs_for_message(-1, 10) == 1
    await db.touch_user(7)
    await db.touch_user(7, "pt-br")  # language learned later the same day
    await db.touch_user(8, "en")
    await db.touch_user(9, "weird_tag!")
    assert await db.active_users() == {"day": 3, "week": 3, "month": 3}
    assert await db.languages() == [("", 1), ("en", 1), ("pt", 1)]
    assert await db.top_sites("2000-01-01") == [("YouTube", 1, 1)]
    await db.add_job(make_job(user_id=8, status="done", kinds='{"photo": 1}'))
    assert await db.recent(since=0) == (2, {"done": 2, "queued": 1}, {"photo": 3, "video": 1})
    assert await db.recent(since=db_now() + 1) == (0, {}, {})
    await db.close()


async def test_resume_after_restart():
    db = await DB.open(":memory:")
    running = await db.add_job(make_job())
    await db.start_job(running)
    await db.add_job(make_job(url="https://youtu.be/z"))
    resumed = await db.resume(max_age=3600)
    assert [j.status for j in resumed] == ["queued", "queued"]
    assert await db.resume(max_age=-1) == []  # everything is too old now
    await db.close()


async def test_migration_from_first_version(tmp_path):
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as conn:  # a database created by the first release
        conn.executescript(MIGRATIONS[0] + "; PRAGMA user_version = 1;")
        conn.execute("INSERT INTO user_days (day, user_id) VALUES ('2026-10-18', 1)")
    db = await DB.open(path)
    await db.touch_user(2, "en")
    assert (await db._all("PRAGMA user_version"))[0][0] == len(MIGRATIONS)
    assert ("en", 1) in await db.languages(days=10_000)
    await db.touch_chat(3, "private", default_captions=True)
    assert (await db._all("SELECT imported FROM chats WHERE chat_id = 3"))[0][0] == 0
    await db.close()


async def test_chats_on_auto_get_asked_about_video_and_can_be_switched_back(tmp_path):
    path = tmp_path / "v5.sqlite3"
    chats = [
        (-1, "group", "dl"), (-2, "supergroup", "dl"), (-3, "supergroup", "ask"), (-4, "supergroup", None), (-5, "channel", "dl"), (-6, "channel", None),
        (6, "private", "dl"), (7, "private", None), (8, "private", "ask"),
    ]  # fmt: skip
    with sqlite3.connect(path) as conn:  # before Auto + ask about video
        conn.executescript("; ".join(MIGRATIONS[:5]) + "; PRAGMA user_version = 5;")
        conn.executemany("INSERT INTO chats (chat_id, type, mode, first_seen, last_seen) VALUES (?, ?, ?, 0, 0)", chats)
    db = await DB.open(path)
    rows = await db._all("SELECT chat_id, mode, previous_mode FROM chats ORDER BY chat_id")
    assert [tuple(row) for row in rows] == [
        (-6, None, None),  # channels and groups were on Ask by default
        (-5, "dl", None),  # a channel would get questions under its posts
        (-4, None, None),
        (-3, "ask", None),
        (-2, "ask_video", "dl"),
        (-1, "ask_video", "dl"),
        (6, "ask_video", "dl"),
        (7, "ask_video", "dl"),  # Auto was the default in private chats
        (8, "ask", None),
    ]
    await db.set_chat(-2, mode="ask_video")  # now it's the chat's own choice
    await db._run("UPDATE chats SET mode = previous_mode, previous_mode = NULL WHERE previous_mode IS NOT NULL")  # undo, see MIGRATIONS
    rows = await db._all("SELECT chat_id, mode, previous_mode FROM chats WHERE chat_id IN (-2, -1, 7) ORDER BY chat_id")
    assert [tuple(row) for row in rows] == [(-2, "ask_video", None), (-1, "dl", None), (7, "dl", None)]
    await db.close()


def test_languages_gauge_keeps_top_and_other():
    observe.set_languages([("ru", 50), ("en", 30), ("", 5), ("pt", 3), ("es", 2)], top=3)
    values = {s.labels["lang"]: s.value for s in observe.USERS_BY_LANGUAGE.collect()[0].samples}
    assert values == {"ru": 50, "en": 30, "unknown": 5, "other": 5}


async def test_recent_results():
    db = await DB.open(":memory:")
    for status, error in (("done", None), ("done", None), ("failed", "login"), ("queued", None)):
        job = await db.add_job(make_job(status=status))
        job.error = error
        if status != "queued":
            await db.finish_job(job)
    assert sorted(await db.recent_results(0), key=str) == sorted([("YouTube", None, 2), ("YouTube", "login", 1)], key=str)
    await db.close()


async def test_always_mp3_setting_goes_to_jobs():
    db = await DB.open(":memory:")
    assert (await db.touch_chat(1, "private", default_captions=True)).mp3 is False
    await db.set_chat(1, mp3=True)
    assert (await db.touch_chat(1, "private", default_captions=True)).mp3 is True
    job = await db.add_job(make_job(mp3=True))
    (row,) = await db._all("SELECT mp3 FROM jobs WHERE id = ?", (job.id,))
    assert row[0] == 1
    await db.close()
