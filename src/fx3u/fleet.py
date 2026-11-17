"""Independent polling/reconnect worker for each named Windows COM adapter.

Read-only runtime. One process owns all configured ports; stop it before deploy.
"""
import argparse
import json
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .client import Session


def load_config(path):
    rows = json.loads(path.read_text(encoding='utf-8'))
    names, ports = set(), set()
    if not isinstance(rows, list) or not rows:
        raise ValueError('Config must be a nonempty list')
    for row in rows:
        name, port = row['name'], row['port'].upper()
        if not re.fullmatch(r'[A-Za-z0-9_-]+', name) or not re.fullmatch(r'COM[1-9][0-9]*', port):
            raise ValueError('Use a simple station name and a canonical COM number')
        if name.casefold() in names or port in ports:
            raise ValueError('Duplicate station/port')
        names.add(name.casefold())
        ports.add(port)
        row['port'] = port
    return rows


def worker(row, root, stop, count, interval):
    name, port = row['name'], row['port']
    done, session = 0, None
    with (root/f'{name}-wire.jsonl').open('x', encoding='utf-8', buffering=1) as wire, \
         (root/f'{name}-states.jsonl').open('x', encoding='utf-8', buffering=1) as states:
        try:
            while not stop.is_set() and (count == 0 or done < count):
                event = {'name': name, 'port': port, 'at_utc': datetime.now(timezone.utc).isoformat()}
                try:
                    if session is None:
                        session = Session.open(port, wire)
                    event['snapshot'] = session.snapshot()
                    done += 1
                except Exception as exc:
                    event['error'] = f'{type(exc).__name__}: {exc}'
                    if session:
                        session.close()
                        session = None
                states.write(json.dumps(event)+'\n')
                stop.wait(max(2 if 'error' in event else .1, interval))
        finally:
            if session:
                session.close()


def add_arguments(p):
    p.add_argument('--config', type=Path, required=True, help='JSON list of {"name", "port"}')
    p.add_argument('--out-dir', type=Path, required=True)
    p.add_argument('--count', type=int, default=0, help='Successful snapshots per station, 0 = continuous')
    p.add_argument('--interval', type=float, default=1)
    p.add_argument('--duration', type=float, default=0, help='Optional wall time limit in seconds')


def run(args, p):
    if args.count < 0 or args.interval < 0 or args.duration < 0:
        p.error('Count, interval and duration must be nonnegative')
    rows = load_config(args.config)
    args.out_dir.mkdir(parents=True, exist_ok=False)
    stop = threading.Event()
    threads = [threading.Thread(target=worker, args=(r, args.out_dir, stop, args.count, args.interval)) for r in rows]
    for thread in threads:
        thread.start()
    deadline = time.monotonic()+args.duration if args.duration else None
    try:
        while any(t.is_alive() for t in threads):
            if deadline is not None and time.monotonic() >= deadline:
                stop.set()
            for thread in threads:
                thread.join(.2)
    except KeyboardInterrupt:
        stop.set()
        for thread in threads:
            thread.join()

