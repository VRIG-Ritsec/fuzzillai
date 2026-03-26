#!/usr/bin/env python3
import json
import os
import select
import subprocess
import sys
import time

import psycopg2

CHANNEL = "crash_corpus"


def _db_connect():
    host = os.getenv("POSTGRES_HOST")
    if not host:
        raise RuntimeError("POSTGRES_HOST is required for crash listener")

    port = os.getenv("POSTGRES_PORT", "5432")
    dbname = os.getenv("POSTGRES_DB", "fuzzilli_master")
    user = os.getenv("POSTGRES_USER", "fuzzilli")
    password = os.getenv("POSTGRES_PASSWORD", "fuzzilli123")

    return psycopg2.connect(
        host=host,
        port=port,
        dbname=dbname,
        user=user,
        password=password,
    )


def _run_ebg_crash(program_hash: str) -> None:
    if not program_hash:
        return
    cmd = [sys.executable, os.path.join(os.path.dirname(__file__), "..", "agents", "EBG_crash.py"), "--crash_program_hash", program_hash]
    subprocess.Popen(cmd, env=os.environ.copy())


def main() -> int:
    conn = _db_connect()
    conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)

    cur = conn.cursor()
    cur.execute(f"LISTEN {CHANNEL};")
    print(f"Listening for NOTIFY on channel '{CHANNEL}'")

    while True:
        if select.select([conn], [], [], 5) == ([], [], []):
            continue

        conn.poll()
        while conn.notifies:
            notify = conn.notifies.pop(0)
            payload = notify.payload
            try:
                data = json.loads(payload) if payload else {}
            except json.JSONDecodeError:
                data = {}
            program_hash = data.get("program_hash")
            _run_ebg_crash(program_hash)
            time.sleep(0.1)


if __name__ == "__main__":
    sys.exit(main())
