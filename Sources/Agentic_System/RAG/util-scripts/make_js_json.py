#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from generator_common import REGRESSIONS_DIR, REGRESSIONS_JSON, parse_fuzzil_from_output, require_env, run_command, run_d8

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None


def collect_js_files(root_dir: Path) -> list[Path]:
    return sorted(path for path in root_dir.rglob("*.js") if path.is_file())


def process_one(js_path: Path, root_dir: Path) -> tuple[str, dict[str, str]]:
    fuzzilli_tool = require_env("FUZZILLI_TOOL_BIN")
    key = str(js_path.relative_to(root_dir).with_suffix(""))
    data = {"js": "", "Fuzzilli": "", "execution_data": ""}
    data["js"] = js_path.read_text(encoding="utf-8", errors="ignore")
    fuzz = run_command([fuzzilli_tool, "--compile", str(js_path)])
    combined = (fuzz.stdout or "") + (fuzz.stderr or "")
    data["Fuzzilli"] = parse_fuzzil_from_output(combined)
    data["execution_data"] = run_d8(js_path)
    return key, data


def iter_completed(futures: list, total: int):
    completed = as_completed(futures)
    if tqdm is None:
        return completed
    return tqdm(completed, total=total, desc="Processing", unit="file")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build regressions.json from the JS regression corpus.")
    parser.add_argument("--root", default=str(REGRESSIONS_DIR), help="Regression corpus directory")
    parser.add_argument("--output", default=str(REGRESSIONS_JSON), help="Output json path")
    parser.add_argument(
        "--workers",
        type=int,
        default=min(8, max(2, (os.cpu_count() or 4))),
        help="Number of worker threads",
    )
    args = parser.parse_args()

    root_dir = Path(args.root).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()

    if not root_dir.exists():
        print(f"Regression directory does not exist: {root_dir}")
        return 1

    try:
        require_env("D8_PATH")
        require_env("FUZZILLI_TOOL_BIN")
    except RuntimeError as exc:
        print(exc)
        return 1

    js_files = collect_js_files(root_dir)
    print(f"Discovered {len(js_files)} JavaScript files under '{root_dir}'")

    files_data: dict[str, dict[str, str]] = {}
    started_all = time.time()

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = [executor.submit(process_one, js_path, root_dir) for js_path in js_files]
        for future in iter_completed(futures, len(js_files)):
            key, data = future.result()
            files_data[key] = data

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(files_data, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Completed in {time.time() - started_all:.2f}s. Wrote {output_path} with {len(files_data)} entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
