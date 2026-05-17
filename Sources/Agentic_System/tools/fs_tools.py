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

MAX_TOOL_RESULT_BYTES = 100 * 1024
MAX_FILE_SIZE = MAX_TOOL_RESULT_BYTES
READ_FILE_MIN_LINES_IN_SLICE = 1
READ_FILE_MAX_LINES_IN_SLICE = 500
READ_FILE_MAX_LINES_WHOLE = 1500
GLOB_MAX_RESULTS = 500
LIST_DIR_MAX_RESULTS = 500
GREP_MAX_RESULTS = 400

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


def _base_path_error(base_path: str) -> str | None:
    safe_base = _safe_base(base_path)
    if not os.path.isdir(safe_base):
        return f"Error: Tool base path is not a directory: {safe_base}"

    base = Path(safe_base)
    build_markers = ("build.ninja", ".ninja_deps", ".ninja_log")
    if any((base / marker).exists() for marker in build_markers):
        return (
            "Error: Tool base path is not a source root. "
            f"It points at a V8 build/output directory ({safe_base}). "
            "Set V8_PATH to the V8 source root before using list_dir, glob_search, "
            "grep_search, or read_file. No path fallback was applied."
        )

    return None


def _byte_len(text: str) -> int:
    return len(text.encode("utf-8", errors="replace"))


def _size_limit_hint(tool_name: str) -> str:
    return (
        f"{tool_name} went beyond its max result budget of {MAX_TOOL_RESULT_BYTES} bytes. "
        "Retry with more specific information: for read_file, provide a smaller "
        "line_start/line_end window; for grep_search, provide a narrower pattern and, "
        "when possible, file_path plus optional line_start/line_end."
    )


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
            f"Error: At most {READ_FILE_MAX_LINES_IN_SLICE} lines per line_start..line_end window. "
            "Use a smaller line_start..line_end window."
        )
    return (ls, le)


def _truncate_lines(text: str, max_lines: int, continuation_hint: str) -> str:
    if max_lines <= 0:
        return continuation_hint.strip()
    lines = text.splitlines(keepends=True)
    if len(lines) <= max_lines:
        return text
    truncated = "".join(lines[:max_lines]).rstrip("\n")
    return f"{truncated}\n\n[truncated after {max_lines} lines; {continuation_hint}]"


def read_file_from_base(args: dict[str, Any], base_path: str) -> str:
    file_path = args.get("file_path")
    if not file_path:
        return "Error: file_path is required"

    base_error = _base_path_error(base_path)
    if base_error:
        return base_error

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

    try:
        if not os.path.exists(full_path):
            return f"Error: File {file_path} does not exist."
        if not os.path.isfile(full_path):
            return f"Error: File {file_path} is not a regular file."

        file_size = os.path.getsize(full_path)

        if file_size > MAX_FILE_SIZE and line_range is None:
            return (
                f"Error: read_file went beyond its max full-read budget. "
                f"File is {file_size} bytes; full-read limit is {MAX_FILE_SIZE} bytes. "
                "Use line_start and line_end (1-based inclusive line numbers) for controlled partial "
                f"access; each window can return at most {READ_FILE_MAX_LINES_IN_SLICE} lines. "
                "Example: line_start=1, line_end=300. You can also use grep_search with "
                "file_path and a narrower pattern to locate specific lines first."
            )

        if line_range is None:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            if _byte_len(content) > MAX_TOOL_RESULT_BYTES:
                return (
                    f"Error: {_size_limit_hint('read_file')} "
                    "The decoded file content exceeded the byte limit; retry with line_start and line_end."
                )
            result = _truncate_lines(
                content,
                READ_FILE_MAX_LINES_WHOLE,
                "use line_start and line_end for the next section",
            )
            if _byte_len(result) > MAX_TOOL_RESULT_BYTES:
                return (
                    f"Error: {_size_limit_hint('read_file')} "
                    "The truncated full-file result still exceeded the byte limit; retry with line_start and line_end."
                )
            return result

        start, end = line_range
        parts: list[str] = []
        total = 0
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f, start=1):
                if i < start:
                    continue
                if i > end:
                    break
                total += _byte_len(line)
                if total > MAX_TOOL_RESULT_BYTES:
                    return (
                        f"Error: {_size_limit_hint('read_file')} "
                        f"Selected slice lines {start}-{end} exceeded the budget. "
                        "Use a smaller line_start..line_end range or use grep_search "
                        "with file_path and a narrower pattern to find exact lines."
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

    base_error = _base_path_error(base_path)
    if base_error:
        return base_error

    if os.path.isabs(pattern) or ".." in pattern:
        return "Error: Pattern cannot be absolute or contain '..'"

    search_path = os.path.join(_safe_base(base_path), pattern)
    try:
        files = glob.glob(search_path, recursive=True)
        safe_files = [f for f in files if is_within_base(f, base_path)]
        relative_files = sorted(os.path.relpath(f, _safe_base(base_path)) for f in safe_files)
        truncated = len(relative_files) > GLOB_MAX_RESULTS
        if truncated:
            relative_files = relative_files[:GLOB_MAX_RESULTS]
        return json.dumps(
            {
                "matches": relative_files,
                "returned_count": len(relative_files),
                "truncated": truncated,
                "max_results": GLOB_MAX_RESULTS,
            },
            indent=2,
        )
    except Exception as e:
        return f"Error during glob search: {str(e)}"


def _is_searchable_file(path: str) -> bool:
    name = os.path.basename(path)
    ext = os.path.splitext(name)[1].lower()
    if name in {".ninja_deps", ".ninja_log"}:
        return False
    return ext not in _EXCLUDED_EXTENSIONS


def _get_grep_file_list(base_path: str, start_path: str | None = None) -> Iterator[tuple[str, str]]:
    safe_base = _safe_base(base_path)
    search_root = start_path or safe_base
    if os.path.isfile(search_root):
        if _is_searchable_file(search_root) and is_within_base(search_root, safe_base):
            yield search_root, os.path.splitext(search_root)[1].lower()
        return

    for root, dirs, files in os.walk(search_root):
        dirs[:] = sorted(
            d
            for d in dirs
            if d not in _EXCLUDED_DIRS and "immutable" not in Path(root, d).parts
        )
        for name in sorted(files):
            full_path = os.path.join(root, name)
            ext = os.path.splitext(name)[1].lower()
            if not _is_searchable_file(full_path):
                continue
            if not is_within_base(full_path, safe_base):
                continue
            yield full_path, ext


def _grep_result(
    matches: list[dict[str, Any]],
    *,
    truncated: bool = False,
    truncation_reason: str | None = None,
    omitted_match: dict[str, Any] | None = None,
) -> str:
    returned_matches = list(matches)
    dropped_for_budget = 0

    while True:
        payload: dict[str, Any] = {
            "matches": returned_matches,
            "returned_count": len(returned_matches),
            "truncated": truncated,
            "max_results": GREP_MAX_RESULTS,
            "max_bytes": MAX_TOOL_RESULT_BYTES,
        }
        if truncation_reason:
            payload["truncation_reason"] = truncation_reason
        if omitted_match:
            payload["first_omitted_match"] = omitted_match
        if dropped_for_budget:
            payload["dropped_returned_matches_for_budget"] = dropped_for_budget
        if truncated:
            payload["continuation_hint"] = _size_limit_hint("grep_search")

        result = json.dumps(payload, indent=2)
        if _byte_len(result) <= MAX_TOOL_RESULT_BYTES or not returned_matches:
            return result

        truncated = True
        truncation_reason = truncation_reason or "result would exceed max_bytes"
        returned_matches.pop()
        dropped_for_budget += 1


def grep_search_in_base(args: dict[str, Any], base_path: str) -> str:
    pattern = args.get("pattern") or args.get("query")
    if not pattern:
        return "Error: pattern is required"

    base_error = _base_path_error(base_path)
    if base_error:
        return base_error

    safe_base = _safe_base(base_path)
    target = args.get("file_path") or args.get("target_path") or args.get("target_directory")
    full_target = None
    if target:
        full_target = resolve_path(safe_base, str(target))
        if not is_within_base(full_target, safe_base):
            return "Error: Access denied. grep_search target is outside the service directory."
        if "immutable" in Path(full_target).parts:
            return "Error: Access to 'immutable' directory is restricted."
        if not os.path.exists(full_target):
            return f"Error: grep_search target {target} does not exist."

    range_or_err = _line_range_from_args(args)
    if isinstance(range_or_err, str):
        return range_or_err
    line_range = range_or_err
    matches: list[dict[str, Any]] = []

    try:
        regex = re.compile(pattern)
    except re.error as e:
        return f"Error: Invalid regex pattern - {str(e)}"

    try:
        truncated = False
        truncation_reason = None
        omitted_match = None
        for file_path, _ext in _get_grep_file_list(safe_base, full_target):
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    for line_number, line in enumerate(f, start=1):
                        if line_range is not None:
                            start, end = line_range
                            if line_number < start:
                                continue
                            if line_number > end:
                                break
                        if regex.search(line):
                            relative_path = os.path.relpath(file_path, safe_base)
                            candidate = {
                                "file": relative_path,
                                "line_number": line_number,
                                "line": line.strip(),
                            }
                            candidate_result = json.dumps(
                                {
                                    "matches": matches + [candidate],
                                    "returned_count": len(matches) + 1,
                                    "truncated": False,
                                    "max_results": GREP_MAX_RESULTS,
                                    "max_bytes": MAX_TOOL_RESULT_BYTES,
                                },
                                indent=2,
                            )
                            if _byte_len(candidate_result) > MAX_TOOL_RESULT_BYTES:
                                truncated = True
                                truncation_reason = "result would exceed max_bytes"
                                omitted_match = {
                                    "file": relative_path,
                                    "line_number": line_number,
                                }
                                break
                            matches.append(candidate)
                            if len(matches) >= GREP_MAX_RESULTS:
                                truncated = True
                                truncation_reason = "result reached max_results"
                                break
                    if truncated:
                        break
            except OSError:
                continue
            if truncated:
                break

        return _grep_result(
            matches,
            truncated=truncated,
            truncation_reason=truncation_reason,
            omitted_match=omitted_match,
        )
    except Exception as e:
        return f"Error during grep search: {str(e)}"


def list_dir_in_base(args: dict[str, Any], base_path: str) -> str:
    target_directory = args.get("target_directory", ".") or "."

    base_error = _base_path_error(base_path)
    if base_error:
        return base_error

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
        filtered = sorted(filtered)
        truncated = len(filtered) > LIST_DIR_MAX_RESULTS
        if truncated:
            filtered = filtered[:LIST_DIR_MAX_RESULTS]
        return json.dumps(
            {
                "entries": filtered,
                "returned_count": len(filtered),
                "truncated": truncated,
                "max_results": LIST_DIR_MAX_RESULTS,
            },
            indent=2,
        )
    except Exception as e:
        return f"Error listing directory: {str(e)}"
