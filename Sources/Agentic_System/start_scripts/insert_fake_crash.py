#!/usr/bin/env python3
import argparse
import base64
import hashlib
import os
import sys
from datetime import datetime

import psycopg2


def _db_connect():
    host = os.getenv("POSTGRES_HOST")
    if not host:
        raise RuntimeError("POSTGRES_HOST is required")

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


def _ensure_fuzzer(conn, fuzzer_id):
    cur = conn.cursor()
    if fuzzer_id is not None:
        cur.execute("SELECT fuzzer_id FROM main WHERE fuzzer_id = %s", (fuzzer_id,))
        if cur.fetchone():
            return fuzzer_id
        raise RuntimeError(f"fuzzer_id {fuzzer_id} not found in main")

    cur.execute(
        "INSERT INTO main (status, last_activity, engine_arguments) VALUES ('active', NOW(), ARRAY[]::TEXT[]) RETURNING fuzzer_id"
    )
    new_id = cur.fetchone()[0]
    return new_id


def main() -> int:
    parser = argparse.ArgumentParser(description="Insert a fake crashing corpus entry")
    parser.add_argument("--fzil", required=True, help="Path to .fzil crash file")
    parser.add_argument("--fuzzer-id", type=int, default=None, help="Existing fuzzer_id to attach to")
    args = parser.parse_args()

    fzil_path = args.fzil
    if not os.path.isfile(fzil_path):
        print(f"File not found: {fzil_path}", file=sys.stderr)
        return 1

    with open(fzil_path, "rb") as f:
        raw = f.read()

    program_hash = hashlib.sha256(raw).hexdigest()
    program_base64 = base64.b64encode(raw).decode("ascii")

    conn = _db_connect()
    try:
        conn.autocommit = False
        fuzzer_id = _ensure_fuzzer(conn, args.fuzzer_id)

        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO program (program_hash, fuzzer_id, program_base64, created_at, inserted_at)
            VALUES (%s, %s, %s, NOW(), NOW())
            ON CONFLICT (program_hash) DO NOTHING
            """,
            (program_hash, fuzzer_id, program_base64),
        )

        cur.execute(
            """
            INSERT INTO execution (
                program_hash,
                execution_outcome_id,
                coverage_total,
                edges_found,
                total_edges,
                is_new_edge,
                stdout,
                stderr,
                fuzzout,
                created_at
            ) VALUES (%s, 1, NULL, NULL, NULL, FALSE, %s, %s, %s, NOW())
            """,
            (
                program_hash,
                f"Fake crash inserted {datetime.utcnow().isoformat()}Z",
                "",
                "",
            ),
        )

        conn.commit()
    finally:
        conn.close()

    print(program_hash)
    return 0


if __name__ == "__main__":
    sys.exit(main())
