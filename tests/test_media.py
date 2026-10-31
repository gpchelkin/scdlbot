import shutil
import subprocess
from pathlib import Path

import pytest

from scdlbot.config import Config
from scdlbot.media import Item, ensure_jpeg


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
