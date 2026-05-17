#!/usr/bin/env python3
"""
Lift stored FuzzIL programs in Postgres to JavaScript using FuzzILTool.

By default reads the whole program table. With --crashes-only, only rows that
have at least one execution with outcome Crashed (generated_program_queue is
never included in that mode).

For each lifted .js file, a sidecar <same_basename>.flags.txt is written with
# comments plus one engine flag per line from main.engine_arguments for that
program's fuzzer_id (or target_fuzzer_id for the queue).

Tables:
  - program (default)
  - generated_program_queue (optional, --include-queue or --queue-only)

Connection (same idea as Scripts/extract-crashes.py):
  POSTGRES_URL  OR  POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD

Usage:
  python3 Scripts/dump-all-programs-to-js.py -o ./crashes/js_dump
  python3 Scripts/dump-all-programs-to-js.py -o ./out --include-queue --limit 50
  python3 Scripts/dump-all-programs-to-js.py --crashes-only -o ./crashes/js_crashes
"""

from __future__ import annotations

import argparse
import base64
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

try:
    import psycopg2
except ImportError:
    print("Error: psycopg2 is required. Install: pip install psycopg2-binary", file=sys.stderr)
    sys.exit(1)


def connect():
    url = os.getenv("POSTGRES_URL")
    if url:
        return psycopg2.connect(url)
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        database=os.getenv("POSTGRES_DB", "fuzzilli_master"),
        user=os.getenv("POSTGRES_USER", "fuzzilli"),
        password=os.getenv("POSTGRES_PASSWORD", "fuzzilli123"),
    )


ProgramRow = Tuple[str, str, int, Optional[List[str]]]


def iter_program_rows(conn, limit: Optional[int], crashes_only: bool) -> Iterator[ProgramRow]:
    if crashes_only:
        q = """
            SELECT p.program_hash, p.program_base64, p.fuzzer_id, m.engine_arguments
            FROM program p
            LEFT JOIN main m ON m.fuzzer_id = p.fuzzer_id
            WHERE EXISTS (
                SELECT 1 FROM execution e
                JOIN execution_outcome eo ON e.execution_outcome_id = eo.id
                WHERE e.program_hash = p.program_hash AND eo.outcome = 'Crashed'
            )
            ORDER BY p.inserted_at NULLS LAST, p.created_at NULLS LAST
        """
    else:
        q = """
            SELECT p.program_hash, p.program_base64, p.fuzzer_id, m.engine_arguments
            FROM program p
            LEFT JOIN main m ON m.fuzzer_id = p.fuzzer_id
            ORDER BY p.inserted_at NULLS LAST, p.created_at NULLS LAST
        """
    if limit is not None:
        q += f" LIMIT {int(limit)}"
    with conn.cursor() as cur:
        cur.execute(q)
        while True:
            rows = cur.fetchmany(50)
            if not rows:
                break
            for program_hash, b64, fuzzer_id, engine_args in rows:
                if program_hash and b64:
                    args_list: Optional[List[str]] = None
                    if engine_args is not None:
                        args_list = [str(x) for x in engine_args]
                    yield (program_hash, b64, int(fuzzer_id), args_list)


QueueRow = Tuple[int, str, str, Optional[List[str]]]


def iter_queue_rows(conn, limit: Optional[int]) -> Iterator[QueueRow]:
    q = """
        SELECT q.target_fuzzer_id, q.program_hash, q.program_base64, m.engine_arguments
        FROM generated_program_queue q
        LEFT JOIN main m ON m.fuzzer_id = q.target_fuzzer_id
        ORDER BY q.created_at NULLS LAST
    """
    if limit is not None:
        q += f" LIMIT {int(limit)}"
    with conn.cursor() as cur:
        cur.execute(q)
        while True:
            rows = cur.fetchmany(50)
            if not rows:
                break
            for fid, program_hash, b64, engine_args in rows:
                if program_hash and b64:
                    args_list: Optional[List[str]] = None
                    if engine_args is not None:
                        args_list = [str(x) for x in engine_args]
                    yield (int(fid), program_hash, b64, args_list)


def write_engine_flags(path: Path, fuzzer_id: int, engine_args: Optional[List[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: List[str] = [
        f"# fuzzer_id={fuzzer_id}",
        "# Engine flags from main.engine_arguments (one arg per line below).",
    ]
    if engine_args:
        lines.extend(engine_args)
    else:
        lines.append("# engine_arguments: (null or empty in main table)")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def lift_one(fuzziltool: Path, b64: str, out_js: Path) -> Tuple[bool, str]:
    raw = base64.b64decode(b64, validate=False)
    out_js.parent.mkdir(parents=True, exist_ok=True)
    err: str = ""
    with tempfile.NamedTemporaryFile(suffix=".fzil", delete=False) as tmp:
        tmp.write(raw)
        fzil_path = tmp.name
    try:
        proc = subprocess.run(
            [str(fuzziltool), "--liftToJS", fzil_path],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            err = (proc.stderr or "") + (proc.stdout or "")
            return False, err.strip()[:8000]
        out_js.write_text(proc.stdout or "", encoding="utf-8")
        return True, ""
    finally:
        try:
            os.unlink(fzil_path)
        except OSError:
            pass


def load_dotenv(repo_root: Path) -> None:
    for name in (".env", "env.distributed"):
        p = repo_root / name
        if not p.is_file():
            continue
        with p.open() as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v


def main() -> int:
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent

    parser = argparse.ArgumentParser(description="Dump all program rows from Postgres to lifted JS files.")
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory (default: <repo>/crashes/js_dump_<timestamp>)",
    )
    parser.add_argument(
        "--include-queue",
        action="store_true",
        help="Also dump generated_program_queue rows",
    )
    parser.add_argument(
        "--queue-only",
        action="store_true",
        help="Only dump generated_program_queue",
    )
    parser.add_argument(
        "--crashes-only",
        action="store_true",
        help="Only program rows that have at least one execution with outcome Crashed (ignores generated_program_queue)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Max rows per table (for testing)",
    )
    parser.add_argument(
        "--fuzziltool",
        type=Path,
        default=Path(os.getenv("FUZZILTOOL", str(repo_root / ".build" / "debug" / "FuzzILTool"))),
        help="Path to FuzzILTool binary",
    )
    args = parser.parse_args()

    if args.queue_only and args.crashes_only:
        parser.error("--queue-only and --crashes-only cannot be used together")

    load_dotenv(repo_root)

    fuzziltool: Path = args.fuzziltool
    if not fuzziltool.is_file() or not os.access(fuzziltool, os.X_OK):
        print(f"Error: FuzzILTool not found or not executable: {fuzziltool}", file=sys.stderr)
        print("Build with: swift build   or set FUZZILTOOL", file=sys.stderr)
        return 1

    if args.output_dir is not None:
        out_root = args.output_dir.resolve()
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        prefix = "js_dump_crashes_" if args.crashes_only else "js_dump_"
        out_root = (repo_root / "crashes" / f"{prefix}{ts}").resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    try:
        conn = connect()
    except psycopg2.Error as e:
        print(f"Error: cannot connect to Postgres: {e}", file=sys.stderr)
        print("Set POSTGRES_URL or POSTGRES_HOST and related env vars.", file=sys.stderr)
        return 1

    ok = 0
    failed = 0

    dump_program = not args.queue_only
    dump_queue = (args.include_queue or args.queue_only) and not args.crashes_only
    if args.crashes_only and (args.include_queue or args.queue_only):
        print(
            "Note: skipping generated_program_queue (--crashes-only only dumps programs with a Crashed execution).",
            file=sys.stderr,
        )

    try:
        if dump_program:
            prog_dir = out_root / ("crash" if args.crashes_only else "program")
            for h, b64, fuzzer_id, engine_args in iter_program_rows(conn, args.limit, args.crashes_only):
                target = prog_dir / f"{h}.js"
                flags_path = prog_dir / f"{h}.flags.txt"
                write_engine_flags(flags_path, fuzzer_id, engine_args)
                good, msg = lift_one(fuzziltool, b64, target)
                if good:
                    ok += 1
                    label = "crash" if args.crashes_only else "program"
                    print(f"OK  {label}/{h}.js (+ {h}.flags.txt)")
                else:
                    failed += 1
                    err_path = prog_dir / f"{h}.lift_error.txt"
                    err_path.write_text(msg or "(no stderr)", encoding="utf-8")
                    label = "crash" if args.crashes_only else "program"
                    print(f"FAIL {label}/{h}.js -> {err_path}", file=sys.stderr)

        if dump_queue:
            qdir = out_root / "generated_program_queue"
            for fid, h, b64, engine_args in iter_queue_rows(conn, args.limit):
                safe_name = f"{fid}_{h}.js"
                target = qdir / safe_name
                flags_path = qdir / f"{fid}_{h}.flags.txt"
                write_engine_flags(flags_path, fid, engine_args)
                good, msg = lift_one(fuzziltool, b64, target)
                if good:
                    ok += 1
                    print(f"OK  generated_program_queue/{safe_name} (+ {fid}_{h}.flags.txt)")
                else:
                    failed += 1
                    err_path = qdir / f"{fid}_{h}.lift_error.txt"
                    err_path.write_text(msg or "(no stderr)", encoding="utf-8")
                    print(f"FAIL generated_program_queue/{safe_name} -> {err_path}", file=sys.stderr)
    finally:
        conn.close()

    print(f"Done. lifted={ok} failed={failed} output={out_root}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
