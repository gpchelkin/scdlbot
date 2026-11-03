import shutil
import subprocess
from pathlib import Path

import pytest

from scdlbot.config import Config
from scdlbot.media import Item, ensure_jpeg, fill_video_info


def make_webp(path: Path) -> Path:
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=64x64", "-frames:v", "1", str(path)], check=True)
    return path


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
async def test_ensure_jpeg_converts_webp(tmp_path):
    webp = make_webp(tmp_path / "001_photo.webp")
    item = await ensure_jpeg(Item(webp, "photo"), Config())
    assert item.path == tmp_path / "001_photo.jpg" and item.path.exists() and not webp.exists()
    jpeg = await ensure_jpeg(item, Config())
    assert jpeg is item  # already fine, untouched


def make_videos(tmp_path: Path) -> tuple[Path, Path]:
    """A 2-second 36x64 h264 video, and the same one turned sideways like a phone records it."""
    plain, rotated = tmp_path / "reel.mp4", tmp_path / "phone.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=36x64:d=2", "-c:v", "libx264", str(plain)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-display_rotation", "90", "-i", str(plain), "-c", "copy", str(rotated)], check=True)
    return plain, rotated


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
async def test_videos_without_size_get_it_from_the_file(tmp_path):
    plain, rotated = make_videos(tmp_path)
    item = await fill_video_info(Item(plain, "video"))  # Instagram's progressive formats come like this
    assert (item.vcodec, item.width, item.height, item.duration) == ("h264", 36, 64, 2)
    item = await fill_video_info(Item(rotated, "video", duration=5))
    assert (item.width, item.height, item.duration) == (64, 36, 5)  # turned sideways; what yt-dlp knew stays
    known = Item(plain, "video", duration=2, width=720, height=1280, vcodec="avc1")
    assert await fill_video_info(known) is known


async def test_sender_skips_files_that_disappeared(tmp_path):
    from scdlbot.db import Job
    from scdlbot.sender import Sender

    from .test_jobs import FakeBot

    bot = FakeBot()
    sender = Sender(bot, Config(), Job(5, "private", 7, 10, "https://x", "Instagram", "video", True, "running", id=1))
    present = tmp_path / "002.jpg"
    present.write_bytes(b"x")
    for n, path in enumerate((tmp_path / "001.webp", present), start=1):
        await sender.add(Item(Path(path), "photo", playlist_index=n))
    await sender.flush()
    assert (sender.sent, sender.failed) == (1, 1)
    assert bot.names() == ["send_photo"]


async def test_sent_files_are_counted_by_site_and_kind(tmp_path):
    from scdlbot import observe
    from scdlbot.db import Job
    from scdlbot.sender import Sender

    from .test_jobs import FakeBot

    def count(site: str, kind: str) -> float:
        return observe.ITEMS_SENT.labels(site, kind)._value.get()

    before = count("YouTube", "short"), count("YouTube", "video")
    sender = Sender(FakeBot(), Config(), Job(5, "private", 7, 10, "https://youtu.be/x", "YouTube", "video", True, "running", id=1))
    for name, short in (("short.mp4", True), ("long.mp4", False)):
        (tmp_path / name).write_bytes(b"x")
        await sender.add(Item(tmp_path / name, "video", short=short))
    assert (count("YouTube", "short"), count("YouTube", "video")) == (before[0] + 1, before[1] + 1)
    assert sender.kinds == {"short": 1, "video": 1}  # saved with the job, for /stats


async def test_long_videos_are_not_re_encoded(tmp_path):
    from scdlbot.media import ensure_h264

    item = Item(tmp_path / "long.mp4", "video", vcodec="vp9", duration=3600)
    assert await ensure_h264(item, Config()) is item  # sent as it is, without even probing the file


async def test_re_encode_that_runs_out_of_time_sends_the_original(tmp_path, monkeypatch):
    from scdlbot import media

    async def probe(path):
        return {"streams": [{"codec_type": "video", "codec_name": "vp9"}]}

    async def run(*cmd, timeout):
        assert timeout == 1200  # as long as the video, more than DL_TIMEOUT
        raise TimeoutError

    monkeypatch.setattr(media, "probe", probe)
    monkeypatch.setattr(media, "run", run)
    item = Item(tmp_path / "twenty_minutes.webm", "video", vcodec="vp9", duration=1200)
    assert await media.ensure_h264(item, Config(dl_timeout=300)) is item
