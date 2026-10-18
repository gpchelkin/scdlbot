from scdlbot.db import DB, Job


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
    assert (settings.mode, settings.captions, settings.unknown_sites) == ("dl", True, False)
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
    jobs[0].status, jobs[0].items, jobs[0].bytes = "done", 1, 1000
    await db.finish_job(jobs[0])
    assert await db.active_jobs_for_message(-1, 10) == 1
    await db.touch_user(7)
    await db.touch_user(7)
    assert await db.active_users() == {"day": 1, "week": 1, "month": 1}
    assert await db.top_sites("2000-01-01") == [("YouTube", 1, 1)]
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
