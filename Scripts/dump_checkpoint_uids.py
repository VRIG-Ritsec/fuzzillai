#!/usr/bin/env python3

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

_agentic_root = Path(__file__).resolve().parents[1] / "Sources" / "Agentic_System"
if str(_agentic_root) not in sys.path:
    sys.path.insert(0, str(_agentic_root))

_ikacore_src = _agentic_root / "IkaCore" / "src"
if _ikacore_src.exists() and str(_ikacore_src) not in sys.path:
    sys.path.insert(0, str(_ikacore_src))


def get_default_db_path(namespace: str) -> str:
    override = os.getenv("IKA_CHECKPOINT_DB_PATH", "").strip()
    if override:
        return override
    root_raw = os.getenv("IKA_CHECKPOINT_DIR", "").strip()
    if root_raw:
        root = Path(root_raw).expanduser()
    else:
        root = _agentic_root / "runtime_data" / "ika_snapshots"
    root.mkdir(parents=True, exist_ok=True)
    return str(root / f"{namespace}.db")


def dump_uids(db_path: str, verbose: bool = False) -> None:
    if not Path(db_path).exists():
        print(f"DB not found: {db_path}", file=sys.stderr)
        sys.exit(1)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT uid, scope, created_at FROM checkpoints ORDER BY created_at"
        ).fetchall()
    for row in rows:
        line = f"{row['uid']}\t{row['scope']}\t{row['created_at']}"
        if verbose:
            print(line)
        else:
            print(row["uid"])
    if verbose and rows:
        print(f"\nTotal: {len(rows)} checkpoint(s)")


def main():
    parser = argparse.ArgumentParser(description="Dump checkpoint UIDs from IkaCore checkpoint DB")
    parser.add_argument(
        "db",
        nargs="?",
        default=None,
        help="DB path or namespace (fog, ebg_crash, ebg_plateau). Default: fog",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Show scope and created_at",
    )
    args = parser.parse_args()
    if args.db is None:
        args.db = "fog"
    if args.db.endswith(".db") or "/" in args.db or "\\" in args.db:
        db_path = Path(args.db).expanduser().resolve()
    else:
        db_path = get_default_db_path(args.db)
    dump_uids(str(db_path), verbose=args.verbose)


if __name__ == "__main__":
    main()
