#!/usr/bin/env python3
"""Check which Telegram users the bot can still message, by sending each of them a "typing" chat action.

Run it on the bot's server, with the bot's settings (TG_BOT_TOKEN, and TG_BOT_API for a local Bot API server):

    set -a; . /etc/default/scdlbot; set +a
    python3 tools/reachable.py IDS_FILE RESULTS.tsv

IDS_FILE has one user ID per line. Results are appended to RESULTS.tsv as they come (ID, status, Telegram's answer),
so the check can be stopped and started again: IDs already in RESULTS.tsv are skipped.
At the end, RESULTS.reachable.txt gets only the IDs that answered "ok".

Statuses: ok, blocked (blocked the bot), deactivated (deleted account), not_started (never opened a chat with the bot),
error (anything else, see the answer column). RATE (requests per second, default 20) leaves room under Telegram's
~30 messages/second for the bot itself, which keeps running: the limit is per bot token, so going over it slows the bot too.
If Telegram still answers "429 Too Many Requests", everything pauses for as long as it asks.
"""

import asyncio
import os
import sys
import time
from collections import Counter
from pathlib import Path

import httpx

RATE = float(os.getenv("RATE", "20"))
paused_until = 0.0  # set on a 429 answer: no new requests until then


def status_of(http_code: int, answer: str) -> str:
    answer = answer.lower()
    if http_code == 200:
        return "ok"
    if "blocked by the user" in answer:
        return "blocked"
    if "deactivated" in answer:
        return "deactivated"
    if "chat not found" in answer or "can't initiate conversation" in answer:
        return "not_started"
    return "error"


async def check(client: httpx.AsyncClient, url: str, user_id: int) -> tuple[str, str]:
    global paused_until
    for attempt in range(5):
        try:
            response = await client.post(url, data={"chat_id": user_id, "action": "typing"})
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            await asyncio.sleep(2**attempt)
            answer = f"{type(exc).__name__}: {exc}"
            continue
        if response.status_code == 429:  # flood limit: everyone waits as long as Telegram says
            wait = body.get("parameters", {}).get("retry_after", 5) + 1
            paused_until = max(paused_until, time.monotonic() + wait)
            print(f"429 from Telegram, pausing for {wait}s", flush=True)
            await asyncio.sleep(wait)
            answer = body.get("description", "429")
            continue
        answer = body.get("description", "") or "ok"
        return status_of(response.status_code, answer), answer
    return "error", answer


async def main(ids_file: Path, results_file: Path) -> None:
    ids = [int(line) for line in ids_file.read_text().split() if line.lstrip("-").isdigit()]
    done = {int(line.split("\t", 1)[0]) for line in results_file.read_text().splitlines()} if results_file.exists() else set()
    todo = [user_id for user_id in dict.fromkeys(ids) if user_id > 0 and user_id not in done]
    print(f"{len(ids)} IDs, {len(done)} already checked, {len(todo)} to go at {RATE:g}/s: about {len(todo) / RATE / 60:.0f} min", flush=True)

    api = os.getenv("TG_BOT_API", "https://api.telegram.org").rstrip("/")
    url = f"{api}/bot{os.environ['TG_BOT_TOKEN']}/sendChatAction"
    counts: Counter[str] = Counter()
    started = time.monotonic()
    with results_file.open("a") as results:
        async with httpx.AsyncClient(timeout=30) as client:

            async def one(user_id: int) -> None:
                status, answer = await check(client, url, user_id)
                counts[status] += 1
                results.write(f"{user_id}\t{status}\t{answer}\n")
                results.flush()

            tasks = set()
            for n, user_id in enumerate(todo, start=1):
                task = asyncio.create_task(one(user_id))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
                await asyncio.sleep(max(1 / RATE, paused_until - time.monotonic()))
                if n % 1000 == 0:
                    print(f"{n}/{len(todo)} after {time.monotonic() - started:.0f}s: {dict(counts)}", flush=True)
            await asyncio.gather(*tasks)

    totals = Counter(line.split("\t")[1] for line in results_file.read_text().splitlines())
    reachable = sorted(int(line.split("\t", 1)[0]) for line in results_file.read_text().splitlines() if line.split("\t")[1] == "ok")
    reachable_file = results_file.with_suffix(".reachable.txt")
    reachable_file.write_text("".join(f"{user_id}\n" for user_id in reachable))
    print(f"done: {dict(totals)}; {len(reachable)} reachable IDs in {reachable_file}", flush=True)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    asyncio.run(main(Path(sys.argv[1]), Path(sys.argv[2])))
