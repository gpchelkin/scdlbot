from pathlib import Path

import pytest

from scdlbot.config import Config
from scdlbot.ytdlp import build_command, classify_error, to_item


@pytest.mark.parametrize(
    ("line", "code"),
    [
        ("ERROR: [youtube] aaaaaaaaaaa: This video is unavailable", "unavailable"),
        ("ERROR: [generic] nothing: Unable to download webpage: HTTP Error 404: Not Found", "unavailable"),
        ("ERROR: [generic] Unsupported URL: https://example.com/", "unsupported"),
        ("ERROR: [youtube] x: Sign in to confirm you're not a bot. Use --cookies-from-browser", "login"),
        ("ERROR: [youtube] x: The uploader has not made this video available in your country", "region"),
        ("ERROR: [youtube] x: This live event will begin in 3 hours", "live"),
        ("[download] File is larger than max-filesize (4907257 bytes > 3145728 bytes). Aborting.", "too_large"),
        ("ERROR: [soundcloud] 1452743425: This video is DRM protected", "drm"),
        (
            "ERROR: [youtube] 1E3usks-vRs: Video unavailable. This content isn't available, try again later. Your account has been rate-limited by YouTube for up to an hour.",
            "login",
        ),
        ("ERROR: something else entirely", "failed"),
        ("WARNING: [youtube] Some formats are unavailable", "failed"),  # warnings don't count
    ],
)
def test_classify_error(line, code):
    assert classify_error([line]) == code


def test_build_command():
    cfg = Config(max_playlist_items=7, ytdlp_args=("--js-runtimes", "node"))
    audio = build_command("https://youtu.be/x", "audio", Path("/tmp/job"), cfg, ["--proxy", "http://p"])
    assert audio[1:3] == ["-m", "yt_dlp"]
    assert "-x" in audio and "1:7" in audio
    assert audio[-4:] == ["--proxy", "http://p", "--js-runtimes", "node"]
    video = build_command("https://x.com/u/status/1", "video", Path("/tmp/job"), cfg, [])
    assert "-x" not in video and "--merge-output-format" in video


def test_to_item_splits_artist_from_title():
    item = to_item({"filepath": "/a.m4a", "title": "JK FLESH - Static Demon", "uploader": "Falling Ethics", "duration": 311.04}, "audio")
    assert (item.performer, item.title, item.duration) == ("JK FLESH", "Static Demon", 311)
    item = to_item({"filepath": "/a.mp3", "track": "Nuff Respect", "artist": "Shitmat", "playlist_index": 1, "playlist_count": 3}, "audio")
    assert (item.performer, item.title, item.playlist_index) == ("Shitmat", "Nuff Respect", 1)
