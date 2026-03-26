#!/usr/bin/env python3
"""
Inspect checkpoint DB and agent memory.
Checkpoint DB: runtime_data/ika_snapshots/<namespace>.db (fog, ebg_crash, ebg_plateau)
Agent memory: runtime_data/agent_memory/<session>/<entry_id>.json
"""

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

_agentic_root = Path(__file__).resolve().parents[1] / "Sources" / "Agentic_System"
if str(_agentic_root) not in sys.path:
    sys.path.insert(0, str(_agentic_root))

from tools.agent_memory_db import _base_dir, list_ids, load


def get_checkpoint_db_path(namespace: str) -> Path:
    override = os.getenv("IKA_CHECKPOINT_DB_PATH", "").strip()
    if override:
        return Path(override)
    root_raw = os.getenv("IKA_CHECKPOINT_DIR", "").strip()
    if root_raw:
        root = Path(root_raw).expanduser()
    else:
        root = _agentic_root / "runtime_data" / "ika_snapshots"
    return root / f"{namespace}.db"


def cmd_checkpoints(args: argparse.Namespace) -> int:
    db_path = get_checkpoint_db_path(args.namespace)
    if not db_path.exists():
        print(f"DB not found: {db_path}", file=sys.stderr)
        return 1
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT uid, scope, created_at FROM checkpoints ORDER BY created_at"
        ).fetchall()
    for row in rows:
        print(f"{row['uid']}\t{row['scope']}\t{row['created_at']}")
    if args.count:
        print(f"\nTotal: {len(rows)} checkpoint(s)")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    db_path = get_checkpoint_db_path(args.namespace)
    if not db_path.exists():
        print(f"DB not found: {db_path}", file=sys.stderr)
        return 1
    with sqlite3.connect(str(db_path)) as conn:
        cursor = conn.execute(
            "SELECT payload_json FROM checkpoints WHERE uid = ? LIMIT 1",
            (args.uid,),
        )
        row = cursor.fetchone()
    if not row:
        print(f"No checkpoint with uid: {args.uid}", file=sys.stderr)
        return 1
    try:
        payload = json.loads(row[0])
    except Exception as e:
        print(f"Invalid JSON payload: {e}", file=sys.stderr)
        return 1
    out = json.dumps(payload, indent=2)
    if args.truncate and len(out) > args.truncate:
        out = out[: args.truncate] + "\n... (truncated)"
    print(out)
    return 0


def cmd_memory_sessions(_args: argparse.Namespace) -> int:
    base = _base_dir()
    if not base.exists():
        print("No agent_memory directory")
        return 0
    for d in sorted(base.iterdir()):
        if d.is_dir():
            count = sum(1 for p in d.iterdir() if p.suffix == ".json" and p.is_file())
            print(f"{d.name}\t{count} entries")
    return 0


def cmd_memory_list(args: argparse.Namespace) -> int:
    os.environ["AGENT_MEMORY_SESSION"] = args.session
    os.environ.pop("FOG_SESSION_ID", None)
    ids = list_ids()
    for eid in ids:
        print(eid)
    if args.count:
        print(f"\nTotal: {len(ids)} entry(ies)")
    return 0


def cmd_memory_show(args: argparse.Namespace) -> int:
    os.environ["AGENT_MEMORY_SESSION"] = args.session
    os.environ.pop("FOG_SESSION_ID", None)
    data = load(args.entry_id)
    if not data:
        print(f"Entry not found: {args.entry_id}", file=sys.stderr)
        return 1
    out = json.dumps(data, indent=2)
    if args.truncate and len(out) > args.truncate:
        out = out[: args.truncate] + "\n... (truncated)"
    print(out)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect checkpoint DB and agent memory")
    sub = parser.add_subparsers(dest="cmd", required=True)

    cp = sub.add_parser("checkpoints", help="List checkpoint UIDs")
    cp.add_argument("namespace", nargs="?", default="fog", help="fog, ebg_crash, ebg_plateau")
    cp.add_argument("-c", "--count", action="store_true", help="Print total count")
    cp.set_defaults(func=cmd_checkpoints)

    show = sub.add_parser("show", help="Show checkpoint payload by UID")
    show.add_argument("uid", help="Checkpoint UID")
    show.add_argument("namespace", nargs="?", default="fog", help="DB namespace")
    show.add_argument("-t", "--truncate", type=int, metavar="N", help="Truncate output to N chars")
    show.set_defaults(func=cmd_show)

    ms = sub.add_parser("memory-sessions", help="List agent memory sessions")
    ms.set_defaults(func=cmd_memory_sessions)

    ml = sub.add_parser("memory-list", help="List entry IDs in a session")
    ml.add_argument("session", help="Session ID (e.g. fog_20260317_102945_e29179cc)")
    ml.add_argument("-c", "--count", action="store_true", help="Print total count")
    ml.set_defaults(func=cmd_memory_list)

    msh = sub.add_parser("memory-show", help="Show agent memory entry content")
    msh.add_argument("session", help="Session ID")
    msh.add_argument("entry_id", help="Entry ID")
    msh.add_argument("-t", "--truncate", type=int, metavar="N", help="Truncate output to N chars")
    msh.set_defaults(func=cmd_memory_show)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
