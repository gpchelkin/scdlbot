"""`scdlbot profile`: the bot's Telegram profile (what @BotFather sets) kept in files, telegram_settings/ in the repo.

    scdlbot profile                             what differs between the files and the bot's profile on Telegram
    scdlbot profile --apply                     set what differs
    scdlbot profile --apply --userpic FILE.jpg  also upload FILE.jpg (in the directory) as the bot's userpic

The directory (--dir, else TELEGRAM_SETTINGS, else ./telegram_settings) has name.txt, about.txt (the short description
on the bot's page), description.txt (what new users see before /start) and commands.txt ("command - description" per
line, as @BotFather takes them); a missing file leaves that part alone. They are for all languages (Telegram's
default). Only what differs is set: Telegram rate-limits these changes, renames most. Telegram doesn't let a bot
compare its userpic, so it's uploaded only with --userpic. Still in @BotFather: the inline placeholder (inline.txt),
privacy mode in groups, and whether the bot may join groups.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from telegram import Bot, BotCommand, InputProfilePhotoStatic

from scdlbot.config import Config

COMMAND = re.compile(r"[a-z0-9_]{1,32}")


@dataclass(frozen=True)
class Part:
    key: str
    file: str
    label: str
    limit: int  # characters (for commands: their number)


PARTS = (
    Part("name", "name.txt", "name", 64),
    Part("about", "about.txt", "about (short description)", 120),
    Part("description", "description.txt", "description", 512),
    Part("commands", "commands.txt", "commands", 100),
)


def read_commands(text: str) -> tuple[tuple[str, str], ...]:
    """(command, description) of each "command - description" line."""
    commands = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        command, separator, description = (part.strip() for part in line.partition(" - "))
        if not separator or not COMMAND.fullmatch(command) or not 1 <= len(description) <= 256:
            raise SystemExit(f"commands.txt line {number}: {line!r} isn't 'command - description' (a-z, 0-9, _; up to 256 characters)")
        commands.append((command, description))
    return tuple(commands)


def wanted(directory: Path) -> dict[str, str | tuple[tuple[str, str], ...]]:
    """What the files say; parts without a file are left out."""
    if not directory.is_dir():
        raise SystemExit(f"No directory {directory}: give --dir, or set TELEGRAM_SETTINGS to the repo's telegram_settings/")
    result = {}
    for part in PARTS:
        path = directory / part.file
        if not path.exists():
            continue
        text = path.read_text().strip()
        value = read_commands(text) if part.key == "commands" else text
        if len(value) > part.limit:
            raise SystemExit(f"{path}: {len(value)} {'commands' if part.key == 'commands' else 'characters'}, Telegram takes up to {part.limit}")
        result[part.key] = value
    return result


async def live(bot: Bot) -> dict[str, str | tuple[tuple[str, str], ...]]:
    return {
        "name": (await bot.get_my_name()).name,
        "about": (await bot.get_my_short_description()).short_description,
        "description": (await bot.get_my_description()).description,
        "commands": tuple((command.command, command.description) for command in await bot.get_my_commands()),
    }


def show(value: str | tuple[tuple[str, str], ...]) -> str:
    if isinstance(value, tuple):
        return "\n".join(f"    {command} - {description}" for command, description in value) or "    (none)"
    return "\n".join(f"    {line}" for line in value.splitlines()) or "    (empty)"


def differences(want: dict, have: dict) -> list[str]:
    """The keys of the parts that differ, and prints all parts."""
    changed = []
    for part in PARTS:
        if part.key not in want:
            print(f"{part.label}: no {part.file}, left as it is")
        elif want[part.key] == have[part.key]:
            print(f"{part.label}: as in {part.file}")
        else:
            changed.append(part.key)
            print(f"{part.label}: differs\n  on Telegram:\n{show(have[part.key])}\n  in {part.file}:\n{show(want[part.key])}")
    return changed


async def apply_part(bot: Bot, key: str, value: str | tuple[tuple[str, str], ...]) -> None:
    if key == "name":
        await bot.set_my_name(value)
    elif key == "about":
        await bot.set_my_short_description(value)
    elif key == "description":
        await bot.set_my_description(value)
    elif key == "commands":
        if value:
            await bot.set_my_commands([BotCommand(command, description) for command, description in value])
        else:
            await bot.delete_my_commands()


async def profile(cfg: Config, directory: Path, apply: bool = False, userpic: Path | None = None) -> None:
    if not cfg.token:
        raise SystemExit("TG_BOT_TOKEN is not set")
    want = wanted(directory)
    if userpic and not userpic.is_absolute():
        userpic = directory / userpic
    if userpic and not userpic.is_file():
        raise SystemExit(f"No userpic {userpic}")
    async with Bot(cfg.token, base_url=f"{cfg.tg_api}/bot", base_file_url=f"{cfg.tg_api}/file/bot", local_mode=cfg.local_mode) as bot:
        print(f"@{bot.username}, settings from {directory}:")
        changed = differences(want, await live(bot))
        if not apply:
            if changed or userpic:
                print("\nNothing was changed. To set " + ", ".join([*changed, *(["the userpic"] if userpic else [])]) + ": run it again with --apply")
            return
        for key in changed:
            await apply_part(bot, key, want[key])
            print(f"Set the {key}")
        if userpic:
            # Bytes, not a path: in local mode a path would have to be readable by the Bot API server
            await bot.set_my_profile_photo(InputProfilePhotoStatic(userpic.read_bytes()))
            print(f"Set the userpic to {userpic.name}")
        if not changed and not userpic:
            print("\nNothing to set")
