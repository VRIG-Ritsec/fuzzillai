#!/usr/bin/env python3
"""
Test GDB/MI line breakpoints using tools._shared (substitute-path inside start_mi_debug_session).

start_mi_debug_session applies set substitute-path when V8_PATH is set.
gdb_set_breakpoint rewrites absolute paths under V8_PATH to ../../src/...:line.

Default line 7593 matches many x64.debug d8 builds (GDB line, not always editor line).

Usage:
  export D8_PATH=/path/to/d8
  python3 Scripts/test_mi_d8_line_breakpoint.py
  python3 Scripts/test_mi_d8_line_breakpoint.py --line 7593 --dwarf-prefix ../../src
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_AGENTIC = _ROOT / "Sources" / "Agentic_System"


def _console_lines(mi_json: str) -> str:
    """Pull human-readable lines from gdb_run_command JSON output."""
    lines: list[str] = []
    try:
        data = json.loads(mi_json)
    except json.JSONDecodeError:
        return mi_json
    if not isinstance(data, list):
        return mi_json
    for item in data:
        if isinstance(item, dict) and item.get("type") == "console":
            p = item.get("payload")
            if isinstance(p, str):
                lines.append(p.rstrip())
    return "\n".join(lines) if lines else mi_json


def main() -> int:
    parser = argparse.ArgumentParser(description="MI test: line breakpoint on d8.cc after substitute-path")
    parser.add_argument(
        "--v8-src",
        default=os.environ.get("V8_PATH", "/mnt/vdc/v8_vrig/v8/src"),
        help="Absolute path to V8 src root (directory containing d8/)",
    )
    parser.add_argument(
        "--d8",
        default=os.environ.get("D8_PATH", ""),
        help="Path to d8 binary (or set D8_PATH)",
    )
    parser.add_argument(
        "--line",
        type=int,
        default=7593,
        help="Source line in d8.cc per debug info (default: near main for many builds)",
    )
    parser.add_argument(
        "--dwarf-prefix",
        default="../../src",
        help="Path prefix as stored in DWARF (default ../../src)",
    )
    args = parser.parse_args()

    d8 = args.d8 or os.environ.get("D8_PATH", "")
    if not d8 or not os.path.isfile(d8):
        print("error: set D8_PATH or pass --d8 to a real d8 binary", file=sys.stderr)
        return 1
    v8_src = os.path.abspath(args.v8_src)
    if not os.path.isdir(v8_src):
        print(f"error: V8 src root not a directory: {v8_src}", file=sys.stderr)
        return 1

    os.environ["D8_PATH"] = os.path.abspath(d8)
    os.environ["V8_PATH"] = v8_src
    os.environ["GDB_DWARF_SRC_PREFIX"] = args.dwarf_prefix

    sys.path.insert(0, str(_AGENTIC))
    from tools import _shared as sh  # noqa: E402

    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write("print('line_bp_test');\n")
        js_path = f.name

    try:
        out = sh.start_mi_debug_session(js_path, "")
        if "Error" in out:
            print(out)
            return 1

        bp = sh.gdb_set_breakpoint(f"{v8_src}/d8/d8.cc", args.line)
        print("gdb_set_breakpoint (absolute d8.cc under V8_PATH):")
        print(_console_lines(bp)[:2500] or bp[:2500])

        run = sh.mi_run()
        if "breakpoint-hit" not in run and '"message": "stopped"' not in run:
            print("note: mi_run stream long; check for stop below via frame/regs.")

        regs = sh.gdb_run_command("i r rip rdi rsi")
        reg_text = _console_lines(regs) or regs[:1200]
        print("\nregs (rip, rdi, rsi):")
        print(reg_text[:1200])
        if "d8.cc" in reg_text or str(args.line) in reg_text:
            print("\nok: stopped at d8.cc line breakpoint (substitute-path + file:line).")

        sh.mi_continue()
    finally:
        sh.stop_mi_debug_session()
        try:
            os.unlink(js_path)
        except OSError:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
