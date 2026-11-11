from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest
from telegram import InputProfilePhotoStatic

from scdlbot import profile as profile_module
from scdlbot.config import Config
from scdlbot.profile import profile, read_commands, wanted

SETTINGS = Path(__file__).parent.parent / "telegram_settings"


def test_the_repos_settings_fit_telegrams_limits():
    want = wanted(SETTINGS)
    assert set(want) == {"name", "about", "description", "commands"}
    assert ("dl", "<links> or reply to a message with links: download now") in want["commands"]


def test_commands_are_read_as_botfather_takes_them():
    assert read_commands("help - show the help message\n\ndl - <links>: download - now\n") == (("help", "show the help message"), ("dl", "<links>: download - now"))
    for wrong in ("help show the help", "Help - x", "/help - x", "help - "):
        with pytest.raises(SystemExit, match="line 1"):
            read_commands(wrong)


def test_too_long_texts_are_refused(tmp_path):
    (tmp_path / "about.txt").write_text("x" * 121)
    with pytest.raises(SystemExit, match="up to 120"):
        wanted(tmp_path)


class FakeBot:
    username = "testbot"
    made: ClassVar[list] = []  # every FakeBot made, newest last

    def __init__(self, token, **options):
        self.calls = []
        FakeBot.made.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get_my_name(self):
        return SimpleNamespace(name="Old name")

    async def get_my_short_description(self):
        return SimpleNamespace(short_description="I download music")

    async def get_my_description(self):
        return SimpleNamespace(description="Long text")

    async def get_my_commands(self):
        return [SimpleNamespace(command="help", description="show the help message")]

    async def set_my_name(self, name):
        self.calls.append(("name", name))

    async def set_my_short_description(self, text):
        self.calls.append(("about", text))

    async def set_my_commands(self, commands):
        self.calls.append(("commands", [(command.command, command.description) for command in commands]))

    async def set_my_profile_photo(self, photo):
        self.calls.append(("userpic", type(photo), photo.photo.input_file_content))


async def test_only_what_differs_is_set(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(profile_module, "Bot", FakeBot)
    (tmp_path / "name.txt").write_text("New name\n")
    (tmp_path / "about.txt").write_text("I download music\n")  # as on Telegram; no description.txt: left alone
    (tmp_path / "commands.txt").write_text("help - show the help message\nsettings - change mode and captions\n")
    (tmp_path / "userpic.jpg").write_bytes(b"\xff\xd8 a JPEG")
    cfg = Config(token="123:abc")

    await profile(cfg, tmp_path)  # without --apply: only shows the differences
    out = capsys.readouterr().out
    assert FakeBot.made[-1].calls == []
    assert "name: differs" in out and "about (short description): as in about.txt" in out and "no description.txt" in out and "--apply" in out

    await profile(cfg, tmp_path, apply=True, userpic=Path("userpic.jpg"))
    assert FakeBot.made[-1].calls == [
        ("name", "New name"),
        ("commands", [("help", "show the help message"), ("settings", "change mode and captions")]),
        ("userpic", InputProfilePhotoStatic, b"\xff\xd8 a JPEG"),
    ]
