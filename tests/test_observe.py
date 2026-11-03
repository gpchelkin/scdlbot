from scdlbot.observe import SiteWatch


def results(site: str, ok: int, **errors: int) -> list[tuple[str, str | None, int]]:
    return [(site, None, ok), *((site, code, count) for code, count in errors.items())]


def test_site_watch_alerts_once_then_recovers():
    watch = SiteWatch(min_jobs=20, ratio=0.3, window=1800)
    # Deleted, private or DRM-protected links are the link's fault, not the site's:
    assert watch.check(results("YouTube", 50, unavailable=30, drm=5), 0, {}) == []

    last_errors = {"YouTube": "login: ERROR: [youtube] x: Sign in to confirm you're not a bot"}
    (alert,) = watch.check(results("YouTube", 20, login=25), 0, last_errors)
    assert alert.startswith("⚠️ <b>YouTube</b>: 25 of 45 downloads (56%) failed in the last 30 min: login 25.")
    assert "Sign in to confirm you&#x27;re not a bot" in alert

    assert watch.check(results("YouTube", 20, login=25), 300, {}) == []  # no repeats within an hour
    assert len(watch.check(results("YouTube", 20, login=25), 3700, {})) == 1  # but a reminder after it
    assert watch.check(results("Bandcamp", 1, failed=5), 3800, {}) == []  # too few downloads to judge

    (recovered,) = watch.check(results("YouTube", 40, login=1), 4000, {})
    assert recovered == "✅ <b>YouTube</b> is back to normal: 1 of 41 downloads (2%) failed in the last 30 min."
    assert watch.check(results("YouTube", 40, login=1), 4300, {}) == []
