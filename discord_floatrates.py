#!/usr/bin/env python3
"""GrassRO Floating Rates Discord relay, colocated with the Rare Drops announcer.

Read-only toward rAthena: follows rathena-map.service's systemd journal.
No SQL, rAthena source patches, logging overrides, or map-server restart.
New events only: no historical replay and no persistence if relay is offline.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib import error, request
from urllib.parse import urlsplit

MARKER = '[GR_FLOAT_EVENT]'
MAP_UNIT = 'rathena-map.service'
MAP_EXE = '/root/rathena/map-server'
ORANGE = 0xFF9900
VIOLET = 0xEE82EE
SPONSOR_RE = re.compile(r'^(EXP/Job|Drops) sponsor=(.*?) zeny=(\d+) seconds=(\d+) extended=([01])$')
STOP_RE = re.compile(r'^(EXP/Job|Drops) stopped gm=([01])$')


def event_from_message(message: str) -> dict | None:
    if MARKER not in message:
        return None
    value = message.split(MARKER, 1)[1].strip()
    match = SPONSOR_RE.fullmatch(value)
    if match:
        category, sponsor, zeny, seconds, extended = match.groups()
        return {
            'kind': 'EXP' if category == 'EXP/Job' else 'DROP',
            'action': 'extended' if extended == '1' else 'activated',
            'sponsor': sponsor,
            'zeny': int(zeny),
            'seconds': int(seconds),
        }
    match = STOP_RE.fullmatch(value)
    if match:
        category, gm = match.groups()
        return {
            'kind': 'EXP' if category == 'EXP/Job' else 'DROP',
            'action': 'gm_end' if gm == '1' else 'expired',
        }
    # Intentionally do not forward internal state/reload/conflict debug messages.
    return None


def safe_name(raw: str) -> str:
    name = ''.join(ch if ch.isprintable() else ' ' for ch in raw).strip()[:40]
    name = re.sub(r'([\\`*_~|<>])', r'\\\1', name)
    return (name.replace('@', '@\u200b') or 'Unknown')


def fmt_duration(seconds: int) -> str:
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    chunks = []
    if hours:
        chunks.append(f'{hours}h')
    if minutes:
        chunks.append(f'{minutes}m')
    if seconds or not chunks:
        chunks.append(f'{seconds}s')
    return ' '.join(chunks)


def payload_for(event: dict) -> dict:
    kind = event['kind']
    color = ORANGE if kind == 'EXP' else VIOLET
    label = 'EXP 2x' if kind == 'EXP' else 'DROP 2x'
    note = ('Base and Job EXP' if kind == 'EXP'
            else 'Eligible normal/mini-boss drops only; MVP and castle/treasure rates excluded')
    action = event['action']
    fields = []
    if action in ('activated', 'extended'):
        verb = 'activated' if action == 'activated' else 'extended'
        title = f'GrassRO — {label} {verb.title()}'
        description = (f'**{safe_name(event["sponsor"])}** {verb} **{label}** '
                       f'for **{fmt_duration(event["seconds"])}**.')
        fields = [
            {'name': 'Zeny sponsored', 'value': f'{event["zeny"]:,}', 'inline': True},
            {'name': 'Time added', 'value': fmt_duration(event['seconds']), 'inline': True},
        ]
    elif action == 'expired':
        title = f'GrassRO — {label} Ended'
        description = f'**{label}** expired naturally.'
    else:
        title = f'GrassRO — {label} Ended by GM'
        description = f'**{label}** was ended by a GM.'
    embed = {
        'title': title,
        'description': f'{description}\n\n{note}',
        'color': color,
        'footer': {'text': 'GrassRO Floating Rates | @floatevent'},
    }
    if fields:
        embed['fields'] = fields
    return {
        'username': 'GrassRO Floating Rates',
        'embeds': [embed],
        'allowed_mentions': {'parse': []},
    }


def read_webhook(path: Path) -> str:
    value = path.read_text(encoding='utf-8').strip()
    info = urlsplit(value)
    if (info.scheme != 'https'
            or info.hostname not in ('discord.com', 'discordapp.com', 'canary.discord.com', 'ptb.discord.com')
            or not re.fullmatch(r'/api/webhooks/\d+/[^/?#]+', info.path)
            or info.username or info.password or info.fragment):
        raise ValueError('Credential is not a valid HTTPS Discord webhook URL')
    return value


def credential_path(cli_file: str | None) -> Path:
    if cli_file:
        return Path(cli_file)
    directory = os.environ.get('CREDENTIALS_DIRECTORY')
    if directory:
        return Path(directory) / 'webhook'
    return Path('/root/rathena/discord_floatrates_webhook.txt')


def send_once(payload: dict, webhook: str) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req = request.Request(webhook, data=data, method='POST', headers={
        'Content-Type': 'application/json',
        'User-Agent': 'GrassRO-FloatRates-Journal/2.0',
    })
    with request.urlopen(req, timeout=12) as response:
        if response.status not in (200, 204):
            raise RuntimeError(f'Unexpected HTTP status {response.status}')


def send_with_retry(payload: dict, webhook: str) -> None:
    delay = 2.0
    while True:
        try:
            send_once(payload, webhook)
            return
        except error.HTTPError as exc:
            # Never log exception text: it may contain a full webhook URL.
            print(f'Discord returned HTTP {exc.code}; retrying', file=sys.stderr, flush=True)
            retry_after = exc.headers.get('Retry-After') if exc.headers else None
            if retry_after:
                try:
                    delay = max(2, min(60, float(retry_after)))
                except ValueError:
                    pass
        except (error.URLError, OSError, RuntimeError) as exc:
            print(f'Discord delivery failed ({type(exc).__name__}); retrying',
                  file=sys.stderr, flush=True)
        time.sleep(delay)
        delay = min(60, delay * 2)


def process_entry(line: str) -> dict | None:
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(record, dict):
        return None
    if record.get('_SYSTEMD_UNIT') != MAP_UNIT or record.get('_EXE') != MAP_EXE:
        return None
    message = record.get('MESSAGE')
    if isinstance(message, list):
        try:
            message = bytes(message).decode('utf-8', errors='replace')
        except (TypeError, ValueError):
            return None
    if not isinstance(message, str):
        return None
    message = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', message).replace('\x00', '')
    return event_from_message(message)


def watch(webhook: str) -> None:
    # -n 0 means this process never replays earlier sponsorships on first launch
    # or when restarted. It watches only future journal events.
    command = [
        '/usr/bin/journalctl', '--no-pager', '--follow', '--lines=0',
        '--output=json', f'_SYSTEMD_UNIT={MAP_UNIT}',
    ]
    print(f'Watching future events from {MAP_UNIT} (no game configuration changes).', flush=True)
    while True:
        try:
            with subprocess.Popen(command, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True,
                                  encoding='utf-8', errors='replace',
                                  bufsize=1) as proc:
                assert proc.stdout is not None
                for line in proc.stdout:
                    event = process_entry(line)
                    if event:
                        send_with_retry(payload_for(event), webhook)
                        print(f'Sent {event["kind"]} {event["action"]} notification', flush=True)
                rc = proc.wait()
                print(f'journalctl exited (status {rc}); restarting reader',
                      file=sys.stderr, flush=True)
        except OSError as exc:
            print(f'Cannot start journal reader ({type(exc).__name__}); retrying',
                  file=sys.stderr, flush=True)
        time.sleep(5)


def self_test() -> None:
    samples = [
        ('[Debug]: script debug : 2000000 110393989 : [GR_FLOAT_EVENT] EXP/Job sponsor=GM Grass zeny=10000000 seconds=3600 extended=0', 'EXP', 'activated'),
        ('[Debug]: script debug : 2000000 110393989 : [GR_FLOAT_EVENT] Drops sponsor=Venice zeny=5000000 seconds=900 extended=1', 'DROP', 'extended'),
        ('[Debug]: script debug : 0 110407867 : [GR_FLOAT_EVENT] EXP/Job stopped gm=0', 'EXP', 'expired'),
        ('[Debug]: script debug : 2000000 110425072 : [GR_FLOAT_EVENT] Drops stopped gm=1', 'DROP', 'gm_end'),
    ]
    for msg, kind, action in samples:
        event = event_from_message(msg)
        assert event and event['kind'] == kind and event['action'] == action
        payload = payload_for(event)
        assert payload['allowed_mentions'] == {'parse': []}
        assert payload['embeds'][0]['color'] == (ORANGE if kind == 'EXP' else VIOLET)
        entry = {'_SYSTEMD_UNIT': MAP_UNIT, '_EXE': MAP_EXE, 'MESSAGE': msg}
        assert process_entry(json.dumps(entry)) == event
    assert event_from_message('[GR_FLOAT_EVENT] Exp rates restored on script load') is None
    assert process_entry(json.dumps({'_SYSTEMD_UNIT': 'something-else', '_EXE': MAP_EXE, 'MESSAGE': samples[0][0]})) is None
    assert safe_name('@everyone') != '@everyone'
    print('PASS: 4 event types, message parsing, colors, source filtering, safe mentions')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test', action='store_true', help='Offline parser test; no Discord request')
    parser.add_argument('--test-webhook', action='store_true', help='Send one clearly labeled test embed')
    parser.add_argument('--webhook-file', help='Optional webhook secret file; defaults to /root/rathena/discord_floatrates_webhook.txt')
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return 0

    try:
        webhook = read_webhook(credential_path(args.webhook_file))
    except (OSError, ValueError) as exc:
        print(f'Webhook setup error ({type(exc).__name__}): check the dedicated credential file',
              file=sys.stderr)
        return 2

    if args.test_webhook:
        test_payload = {
            'username': 'GrassRO Floating Rates',
            'embeds': [
                {'title': 'TEST — EXP 2x relay', 'description': 'Connectivity test only. No game event was activated.', 'color': ORANGE},
                {'title': 'TEST — DROP 2x relay', 'description': 'Connectivity test only. No game event was activated.', 'color': VIOLET},
            ],
            'allowed_mentions': {'parse': []},
        }
        try:
            send_once(test_payload, webhook)
            print('PASS: test embeds delivered (game server unchanged)')
            return 0
        except error.HTTPError as exc:
            print(f'FAIL: Discord HTTP {exc.code} (URL not displayed)', file=sys.stderr)
            return 1
        except (error.URLError, OSError, RuntimeError) as exc:
            print(f'FAIL: {type(exc).__name__} (URL not displayed)', file=sys.stderr)
            return 1

    watch(webhook)
    return 0


if __name__ == '__main__':
    sys.exit(main())
