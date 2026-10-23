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
        ("ERROR: [Instagram] Dd6dW9HxKkh: This content isn't available to everyone: It can't be seen by certain audiences.", "restricted"),
        ("ERROR: [youtube] x: Sign in to confirm your age. This video may be inappropriate for some users.", "restricted"),
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


# Real yt-dlp output (shortened) for an Instagram carousel: photo, video, photo; photos also fail as "No video formats".
CAROUSEL_OUTPUT = r"""
import json, sys
d = sys.argv[1]
def out(**kw): print(json.dumps(kw), flush=True)
print("WARNING: No video formats found!", file=sys.stderr)
out(__files_to_move={f"{d}/001.jpg": f"{d}/001.jpg"}, playlist_index=1, playlist_count=3, channel="gpchelkin", uploader="George Pchelkin")
print("ERROR: [Instagram] B5P4Ammnc0d: No video formats found!", file=sys.stderr)
out(format_id="dash-1v+dash-2a", __files_to_move={f"{d}/002.jpg": f"{d}/002.jpg"}, playlist_index=2, playlist_count=3, channel="gpchelkin")
out(filepath=f"{d}/002.mp4", playlist_index=2, playlist_count=3, vcodec="avc1.4d401f", width=720, height=720, channel="gpchelkin", uploader="George Pchelkin")
out(__files_to_move={f"{d}/003.jpg": f"{d}/003.jpg"}, playlist_index=3, playlist_count=3, channel="gpchelkin", uploader="George Pchelkin")
print("ERROR: [Instagram] B5P4AmqHj0c: No video formats found!", file=sys.stderr)
sys.exit(int(sys.argv[2]))
"""


async def run_fake_ytdlp(monkeypatch, tmp_path, exit_code=1, extra_error=None):
    import sys

    from scdlbot import ytdlp

    script = CAROUSEL_OUTPUT + (f'print("{extra_error}", file=sys.stderr)\n' if extra_error else "")
    script = script.replace("sys.exit(int(sys.argv[2]))\n", "") + "sys.exit(int(sys.argv[2]))\n"
    monkeypatch.setattr(ytdlp, "build_command", lambda *a, **k: [sys.executable, "-c", script, str(tmp_path), str(exit_code)])
    return [info async for info in ytdlp.download("https://www.instagram.com/p/x/", "video", tmp_path, Config(), photos=True)]


async def test_download_yields_photos_and_videos_in_order(monkeypatch, tmp_path):
    infos = await run_fake_ytdlp(monkeypatch, tmp_path)  # exit code 1 only because of the photos: not an error
    assert [(Path(i["filepath"]).name, bool(i.get("_photo"))) for i in infos] == [("001.jpg", True), ("002.mp4", False), ("003.jpg", True)]
    items = [to_item(info, "video", "Instagram") for info in infos]
    assert [i.kind for i in items] == ["photo", "video", "photo"]
    assert (items[0].author_name, items[0].author_handle, items[0].author_url) == ("George Pchelkin", "gpchelkin", "https://www.instagram.com/gpchelkin/")


async def test_download_still_reports_real_errors_next_to_photos(monkeypatch, tmp_path):
    from scdlbot.ytdlp import DownloadError

    with pytest.raises(DownloadError) as error:
        await run_fake_ytdlp(monkeypatch, tmp_path, extra_error="ERROR: [Instagram] zzz: Video unavailable")
    assert error.value.code == "unavailable"


def test_to_item_authors_per_site():
    x = to_item({"filepath": "/a.mp4", "uploader": "NASA", "uploader_id": "NASA"}, "video", "Twitter")
    assert (x.author_name, x.author_handle, x.author_url) == ("NASA", "NASA", "https://twitter.com/NASA")
    tiktok = to_item({"filepath": "/a.mp4", "uploader": "leenabhushan", "channel": "Leena"}, "video", "TikTok")
    assert (tiktok.author_name, tiktok.author_handle, tiktok.author_url) == ("Leena", "leenabhushan", "https://www.tiktok.com/@leenabhushan")
    other = to_item({"filepath": "/a.mp4", "uploader": "Someone"}, "video", "vimeo.com")
    assert (other.author_name, other.author_handle, other.author_url) == ("Someone", None, None)


def test_build_command_always_mp3():
    command = build_command("https://youtu.be/x", "audio", Path("/tmp/job"), Config(), [], mp3=True)
    assert command[command.index("--audio-format") + 1] == "mp3" and command[command.index("--audio-quality") + 1] == "2"
    assert build_command("https://youtu.be/x", "audio", Path("/tmp/job"), Config(), [])[13:] != command[13:]
