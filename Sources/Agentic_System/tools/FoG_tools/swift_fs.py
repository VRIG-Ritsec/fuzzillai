"""
FoG Swift filesystem tools: swift_list_dir, swift_glob_search,
swift_grep_search, swift_read_file.
"""

from pathlib import Path

_agentic_dir = Path(__file__).resolve().parent.parent.parent
_ikacore_src = _agentic_dir / "IkaCore" / "src"
import sys
if str(_ikacore_src) not in sys.path:
    sys.path.insert(0, str(_ikacore_src))

from IkaCore.tools import IkaTools
from tools.fs_tools import (
    glob_search_in_base,
    grep_search_in_base,
    list_dir_in_base,
    read_file_from_base,
    MAX_TOOL_RESULT_BYTES,
    READ_FILE_MAX_LINES_IN_SLICE,
)

from ._shared import SWIFT_PATH, FUZZILLI_PATH
from .file_patch import record_swift_file_read


def _swift_list_dir_executor(params: dict) -> str:
    return list_dir_in_base(params, SWIFT_PATH)


def _swift_glob_search_executor(params: dict) -> str:
    return glob_search_in_base(params, SWIFT_PATH)


def _swift_grep_search_executor(params: dict) -> str:
    return grep_search_in_base(params, SWIFT_PATH)


def _swift_read_file_executor(params: dict) -> str:
    normalized = dict(params)
    file_path = normalized.get("file_path", "")
    if not file_path:
        return "Error: file_path is required"
    if file_path.startswith("Sources/") or file_path.startswith("Fuzzilli/"):
        base_path = FUZZILLI_PATH
    else:
        base_path = SWIFT_PATH
    result = read_file_from_base(normalized, base_path)
    if not result.startswith("Error:"):
        try:
            record_swift_file_read(file_path)
        except Exception:
            pass
    return result


swift_list_dir_tool = IkaTools(
    id="swift_list_dir",
    name="swift_list_dir",
    description="List entries in a Fuzzilli Swift directory. Use targeted relative directories like '.' or 'CodeGen'. Returns JSON names only.",
    parameters={
        "type": "object",
        "properties": {
            "target_directory": {
                "type": "string",
                "description": "Directory relative to SWIFT_PATH to inspect.",
            }
        },
        "required": [],
    },
    execute_function=_swift_list_dir_executor,
)

swift_glob_search_tool = IkaTools(
    id="swift_glob_search",
    name="swift_glob_search",
    description="Searches for files matching a glob pattern under SWIFT_PATH.",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "The glob pattern to search for relative to SWIFT_PATH (for example, '**/*.swift').",
            }
        },
        "required": ["pattern"],
    },
    limit_calls=12,
    execute_function=_swift_glob_search_executor,
)

swift_grep_search_tool = IkaTools(
    id="swift_grep_search",
    name="swift_grep_search",
    description=(
        "Searches for a regex pattern in files under SWIFT_PATH. Results are capped at "
        f"{MAX_TOOL_RESULT_BYTES} bytes; if the cap is hit, retry with a narrower pattern, "
        "file_path, or file_path plus line_start/line_end. When file_path is a directory, "
        "line_start/line_end applies to each searched file in that directory."
    ),
    parameters={
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "The regex pattern to search for.",
            },
            "file_path": {
                "type": "string",
                "description": "Optional file or directory relative to SWIFT_PATH to restrict the search.",
            },
            "line_start": {
                "type": "integer",
                "description": "Optional first line to search. With a directory target, this applies per file.",
            },
            "line_end": {
                "type": "integer",
                "description": "Optional last line to search. With a directory target, this applies per file.",
            },
        },
        "required": ["pattern"],
    },
    execute_function=_swift_grep_search_executor,
)

swift_read_file_tool = IkaTools(
    id="swift_read_file",
    name="swift_read_file",
    description=(
        "Reads file contents under SWIFT_PATH or FUZZILLI_PATH. Small files: omit line_start/line_end "
        f"to read the whole file. Reads are capped at {MAX_TOOL_RESULT_BYTES} bytes. Files or slices beyond that limit cannot be read in full; "
        f"use line_start and line_end (1-based inclusive line numbers). Each paged read returns at most {READ_FILE_MAX_LINES_IN_SLICE} lines per call."
    ),
    parameters={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to the file relative to SWIFT_PATH or FUZZILLI_PATH. Absolute paths are allowed only when still under those roots.",
            },
            "line_start": {
                "type": "integer",
                "description": (
                    "Optional. First line to return (1-based). With line_end, defines the slice; "
                    "If omitted but line_end is set, defaults to 1. If both are omitted, reads the entire file "
                    "when under the size limit."
                ),
            },
            "line_end": {
                "type": "integer",
                "description": (
                    "Optional. Last line to return (1-based, inclusive). If line_start is set and this "
                    "is omitted, it defaults to line_start + "
                    f"{READ_FILE_MAX_LINES_IN_SLICE - 1} (capped by max lines per call). "
                    "Required for controlled access when the tool reports the file is too large for a full read."
                ),
            },
        },
        "required": ["file_path"],
    },
    execute_function=_swift_read_file_executor,
)
