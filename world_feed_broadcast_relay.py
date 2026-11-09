#!/usr/bin/env python3

import fcntl
import json
import os
import re
import subprocess
import sys
import time

from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "log/world_feed_broadcast_cursor.json"
LOCK = ROOT / "log/world_feed_broadcast.lock"
WEBHOOK = ROOT / "discord_webhook.txt"

PREFIX = "[GR_WORLD_FEED_PLAYER_BROADCAST] "
MVP_PREFIX = "[GR_WORLD_FEED_MVP_KILL] "
OCA_PREFIX = "[GR_WORLD_FEED_OCA] "
MCA_PREFIX = "[GR_WORLD_FEED_MCA] "
CREATION_PREFIX = "[GR_WORLD_FEED_ITEM_CREATION] "

os.umask(0o077)


def sql(query):
    result = subprocess.run(
        [
            "mysql",
            "--batch",
            "--skip-column-names",
            "--raw",
            "--database=rathena",
            "--execute", query
        ],
        capture_output=True,
        text=True,
        timeout=20
    )

    if result.returncode:
        raise RuntimeError("MariaDB query failed")

    return result.stdout.splitlines()


def save_cursor(value):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE.with_name(STATE.name + f".{os.getpid()}.tmp")
    temp.write_text(json.dumps({"last_id": value}) + "\n")
    os.chmod(temp, 0o600)
    os.replace(temp, STATE)


def load_cursor():
    if not STATE.exists():
        raise RuntimeError(
            "Relay not initialized. Run --init first."
        )

    value = json.loads(STATE.read_text())["last_id"]

    if type(value) is not int or value < 0:
        raise RuntimeError("Invalid relay checkpoint")

    return value


def initialize():
    if STATE.exists():
        print("Existing checkpoint preserved.")
        return

    result = sql(
        "SELECT COALESCE(MAX(npc_id),0) "
        "FROM npclog"
    )

    last_id = int(result[0])
    save_cursor(last_id)

    print("Relay initialized at NPC log ID:", last_id)
    print("Earlier events will not be replayed.")


def escape_discord(value):
    value = value.replace("\r", " ").replace("\n", " ")
    value = "".join(
        ch if ord(ch) >= 32 else " "
        for ch in value
    )

    return re.sub(
        r'([\\`*_{}\[\]()#+.!>|~])',
        r'\\\1',
        value[:1750]
    )


def send_discord(message, heading):
    url = WEBHOOK.read_text().strip()
    parsed = urlparse(url)

    if (
        parsed.scheme != "https"
        or parsed.hostname not in (
            "discord.com", "discordapp.com"
        )
        or not parsed.path.startswith("/api/webhooks/")
    ):
        raise RuntimeError("Invalid webhook configuration")

    payload = json.dumps({
        "username": "GrassRO World Feed",
        "content": (
            heading + "\n"
            + escape_discord(message)
        ),
        "allowed_mentions": {"parse": []}
    }).encode("utf-8")

    request = Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "GrassRO-Discord-Announcer/1.0"
        },
        method="POST"
    )

    try:
        with urlopen(request, timeout=15) as response:
            if not 200 <= response.status < 300:
                raise RuntimeError("Discord rejected request")
    except HTTPError as exc:
        raise RuntimeError(
            f"Discord HTTP {exc.code}"
        ) from None
    except (URLError, TimeoutError):
        raise RuntimeError(
            "Discord connection failure"
        ) from None


def process_pending():
    last_id = load_cursor()

    rows = sql(
        "SELECT npc_id, HEX(mes) "
        "FROM npclog "
        f"WHERE npc_id > {last_id} "
        f"AND (LEFT(mes,{len(PREFIX)}) = '{PREFIX}' "
        f"OR LEFT(mes,{len(MVP_PREFIX)}) = '{MVP_PREFIX}' "
        f"OR LEFT(mes,{len(OCA_PREFIX)}) = '{OCA_PREFIX}' "
        f"OR LEFT(mes,{len(MCA_PREFIX)}) = '{MCA_PREFIX}' "
        f"OR LEFT(mes,{len(CREATION_PREFIX)}) = '{CREATION_PREFIX}') "
        "ORDER BY npc_id ASC LIMIT 50"
    )

    for row in rows:
        event_id, encoded = row.split("\t", 1)
        event_id = int(event_id)

        message = bytes.fromhex(encoded).decode("utf-8")

        if message.startswith(PREFIX):
            heading = "📢 **Paid Player Broadcast**"
            body = message[len(PREFIX):]
            kind = "paid broadcast"

        elif message.startswith(MVP_PREFIX):
            heading = "👑 **MVP Kill**"
            body = message[len(MVP_PREFIX):]
            kind = "MVP kill"

            if body.startswith("[MVP Kill] "):
                body = body[len("[MVP Kill] "):]

        elif message.startswith(OCA_PREFIX):
            heading = "🃏 **Old Card Album**"
            body = message[len(OCA_PREFIX):]
            kind = "OCA"

            if body.startswith("[OCA] "):
                body = body[len("[OCA] "):]

        elif message.startswith(MCA_PREFIX):
            heading = "🔮 **Mystical Card Album**"
            body = message[len(MCA_PREFIX):]
            kind = "MCA"

            if body.startswith("[MCA] "):
                body = body[len("[MCA] "):]

        elif message.startswith(CREATION_PREFIX):
            heading = "⚒️ **Item Creation**"
            body = message[len(CREATION_PREFIX):]
            kind = "item creation"

            if body.startswith("[Item Creation] "):
                body = body[len("[Item Creation] "):]

        else:
            raise RuntimeError("Unexpected event format")

        send_discord(body, heading)
        save_cursor(event_id)

        print(
            f"Delivered {kind} event {event_id}",
            flush=True
        )

    return len(rows)


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in (
        "--init", "--once", "--run"
    ):
        raise SystemExit(
            "Usage: relay.py --init | --once | --run"
        )

    LOCK.parent.mkdir(parents=True, exist_ok=True)

    with LOCK.open("a+") as lock:
        try:
            fcntl.flock(
                lock.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB
            )
        except BlockingIOError:
            raise SystemExit("Another relay is running.")

        mode = sys.argv[1]

        if mode == "--init":
            initialize()
            return

        if mode == "--once":
            print("Events delivered:", process_pending())
            return

        print("Monitoring paid broadcasts...", flush=True)

        while True:
            try:
                process_pending()
                time.sleep(5)
            except Exception as exc:
                print(
                    "Relay error:",
                    str(exc),
                    "- will retry",
                    flush=True
                )
                time.sleep(15)


if __name__ == "__main__":
    main()
