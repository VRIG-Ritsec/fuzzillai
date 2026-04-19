"""
Generic filesystem tool helpers shared by the V8 and Swift tool wrappers.
"""

from __future__ import annotations

import glob
import json
import os
import re
from pathlib import Path
from typing import Any, Iterator

MAX_FILE_SIZE = 256 * 1024
READ_FILE_MIN_LINES_IN_SLICE = 500
READ_FILE_MAX_LINES_IN_SLICE = 500
GREP_MAX_RESULTS = 200

_EXCLUDED_EXTENSIONS = {
    ".a",
    ".bin",
    ".class",
    ".dll",
    ".dylib",
    ".exe",
    ".gif",
    ".gz",
    ".ico",
    ".jpeg",
    ".jpg",
    ".log",
    ".mp3",
    ".mp4",
    ".mov",
    ".o",
    ".out",
    ".pdf",
    ".png",
    ".pyc",
    ".so",
    ".tar",
    ".wasm",
    ".xz",
    ".zip",
    ".zst",
}

_EXCLUDED_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "dist",
    "node_modules",
}


def _safe_base(base_path: str) -> str:
    return os.path.abspath(os.path.expanduser(base_path))


def is_within_base(path: str, base_path: str) -> bool:
    safe_base = _safe_base(base_path)
    candidate = os.path.abspath(os.path.expanduser(path))
    try:
        return os.path.commonpath([safe_base, candidate]) == safe_base
    except ValueError:
        return False


def resolve_path(base_path: str, target_path: str) -> str:
    return os.path.abspath(os.path.join(_safe_base(base_path), target_path))


def _line_range_from_args(args: dict[str, Any]):
    """Return None for whole-file read, (start, end) 1-based inclusive for a slice, or str on error."""
    ls_raw = args.get("line_start")
    le_raw = args.get("line_end")
    if ls_raw is None and le_raw is None:
        return None
    try:
        ls = int(ls_raw) if ls_raw is not None else None
        le = int(le_raw) if le_raw is not None else None
    except (TypeError, ValueError):
        return "Error: line_start and line_end must be integers when provided."
    if ls is not None and ls < 1:
        return "Error: line_start must be >= 1."
    if le is not None and le < 1:
        return "Error: line_end must be >= 1."
    if ls is None:
        ls = 1
    if le is None:
        le = ls + READ_FILE_MAX_LINES_IN_SLICE - 1
    if le < ls:
        return "Error: line_end must be >= line_start."
    span = le - ls + 1
    if span > READ_FILE_MAX_LINES_IN_SLICE:
        return (
            f"Error: At most {READ_FILE_MAX_LINES_IN_SLICE} lines per read. "
            "Use a smaller line_start..line_end window."
        )
    if span < READ_FILE_MIN_LINES_IN_SLICE:
        le = ls + READ_FILE_MIN_LINES_IN_SLICE - 1
    return (ls, le)


def read_file_from_base(args: dict[str, Any], base_path: str) -> str:
    file_path = args.get("file_path")
    if not file_path:
        return "Error: file_path is required"

    range_or_err = _line_range_from_args(args)
    if isinstance(range_or_err, str):
        return range_or_err
    line_range = range_or_err

    safe_base = _safe_base(base_path)
    full_path = resolve_path(safe_base, str(file_path))

    if not is_within_base(full_path, safe_base):
        return "Error: Access denied. File is outside the service directory."

    if "immutable" in Path(full_path).parts:
        return "Error: Access to 'immutable' directory is restricted."

    max_return_chars = max(MAX_FILE_SIZE * 4, READ_FILE_MAX_LINES_IN_SLICE * 4096)

    try:
        if not os.path.exists(full_path):
            return f"Error: File {file_path} does not exist."
        if not os.path.isfile(full_path):
            return f"Error: File {file_path} is not a regular file."

        file_size = os.path.getsize(full_path)

        if file_size > MAX_FILE_SIZE and line_range is None:
            return (
                f"Error: File is too large ({file_size} bytes) for a full read. "
                f"Whole-file limit is {MAX_FILE_SIZE // 1024} KB. "
                "Use line_start and line_end (1-based inclusive line numbers) for controlled partial "
                f"access; each window is {READ_FILE_MIN_LINES_IN_SLICE} lines "
                f"(requests with a narrower span are expanded to that size). "
                "Example: line_start=1, line_end=500, then line_start=501, line_end=1000, until done."
            )

        if line_range is None:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()

        start, end = line_range
        parts: list[str] = []
        total = 0
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f, start=1):
                if i < start:
                    continue
                if i > end:
                    break
                total += len(line)
                if total > max_return_chars:
                    return (
                        "Error: Selected slice exceeds the return size cap. "
                        "Use a smaller line_start..line_end range or shorter lines."
                    )
                parts.append(line)

        if not parts:
            return f"(no content for lines {start}-{end}; file may have fewer than {start} lines)"

        return "".join(parts)
    except Exception as e:
        return f"Error reading file: {str(e)}"


def glob_search_in_base(args: dict[str, Any], base_path: str) -> str:
    pattern = args.get("pattern")
    if not pattern:
        return "Error: pattern is required"

    if os.path.isabs(pattern) or ".." in pattern:
        return "Error: Pattern cannot be absolute or contain '..'"

    search_path = os.path.join(_safe_base(base_path), pattern)
    try:
        files = glob.glob(search_path, recursive=True)
        safe_files = [f for f in files if is_within_base(f, base_path)]
        relative_files = sorted(os.path.relpath(f, _safe_base(base_path)) for f in safe_files)
        return json.dumps(relative_files, indent=2)
    except Exception as e:
        return f"Error during glob search: {str(e)}"


def _get_grep_file_list(base_path: str) -> Iterator[tuple[str, str]]:
    safe_base = _safe_base(base_path)
    for root, dirs, files in os.walk(safe_base):
        dirs[:] = sorted(
            d
            for d in dirs
            if d not in _EXCLUDED_DIRS and "immutable" not in Path(root, d).parts
        )
        for name in sorted(files):
            ext = os.path.splitext(name)[1].lower()
            if ext in _EXCLUDED_EXTENSIONS:
                continue
            full_path = os.path.join(root, name)
            if not is_within_base(full_path, safe_base):
                continue
            yield full_path, ext


def grep_search_in_base(args: dict[str, Any], base_path: str) -> str:
    pattern = args.get("pattern") or args.get("query")
    if not pattern:
        return "Error: pattern is required"

    safe_base = _safe_base(base_path)
    matches = []

    try:
        regex = re.compile(pattern)
    except re.error as e:
        return f"Error: Invalid regex pattern - {str(e)}"

    try:
        for file_path, _ext in _get_grep_file_list(safe_base):
            if len(matches) >= GREP_MAX_RESULTS:
                break
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    for line_number, line in enumerate(f, start=1):
                        if regex.search(line):
                            relative_path = os.path.relpath(file_path, safe_base)
                            matches.append(
                                {
                                    "file": relative_path,
                                    "line_number": line_number,
                                    "line": line.strip(),
                                }
                            )
                            if len(matches) >= GREP_MAX_RESULTS:
                                break
            except OSError:
                continue

        return json.dumps(matches, indent=2)
    except Exception as e:
        return f"Error during grep search: {str(e)}"


def list_dir_in_base(args: dict[str, Any], base_path: str) -> str:
    target_directory = args.get("target_directory", ".") or "."

    safe_base = _safe_base(base_path)
    full_path = resolve_path(safe_base, str(target_directory))

    if not is_within_base(full_path, safe_base):
        return "Error: Access denied. Directory is outside the service directory."

    try:
        if not os.path.exists(full_path):
            return f"Error: Directory {target_directory} does not exist."
        if not os.path.isdir(full_path):
            return f"Error: {target_directory} is not a directory."
        files = os.listdir(full_path)
        filtered = []
        for file_name in files:
            ext = os.path.splitext(file_name)[1].lower()
            if ext in _EXCLUDED_EXTENSIONS:
                continue
            if file_name in _EXCLUDED_DIRS:
                continue
            filtered.append(file_name)
        return json.dumps(sorted(filtered), indent=2)
    except Exception as e:
        return f"Error listing directory: {str(e)}"
