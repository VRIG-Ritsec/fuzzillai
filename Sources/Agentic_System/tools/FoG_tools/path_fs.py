"""
FoG V8 path and filesystem tools: run_python, get_v8_path, get_realpath,
list_dir, glob_search, grep_search, read_file.
"""

import os
import shlex
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
    READ_FILE_MAX_LINES_IN_SLICE,
)

from ._shared import V8_PATH, run_command, get_output


def get_v8_path() -> str:
    return V8_PATH


def _run_python_executor(params: dict) -> str:
    code = params.get("code", "")
    if not code:
        return "Error: code parameter is required"
    return get_output(run_command(f"python3 -c '{code}' | head -n 1000"))


def _get_v8_path_executor(params: dict) -> str:
    return V8_PATH


def _get_realpath_executor(params: dict) -> str:
    path = params.get("path", "")
    if not path:
        return "Error: path parameter is required"
    return get_output(run_command(f"cd {shlex.quote(V8_PATH)} && realpath {shlex.quote(path)}"))


def _list_dir_executor(params: dict) -> str:
    return list_dir_in_base(params, V8_PATH)


def _glob_search_executor(params: dict) -> str:
    return glob_search_in_base(params, V8_PATH)


def _grep_search_executor(params: dict) -> str:
    return grep_search_in_base(params, V8_PATH)


def _read_file_executor(params: dict) -> str:
    normalized = dict(params)
    file_path = normalized.get("file_path", "")
    if file_path.startswith("v8/"):
        normalized["file_path"] = file_path[3:]
    return read_file_from_base(normalized, V8_PATH)


run_python_tool = IkaTools(
    name="run_python",
    description="Execute arbitrary Python code. Use for data processing, file parsing, or small scripts. Output truncated to 1000 lines.",
    parameters={"code": {"type": "string", "description": "The Python code to execute", "required": True}},
    execute_function=_run_python_executor,
)

get_v8_path_tool = IkaTools(
    name="get_v8_path",
    description="Return the absolute path to the V8 source root (v8/src). Use this before path-based tools to understand the base directory.",
    parameters={"input": {"type": "string", "description": "No input required", "required": False}},
    execute_function=_get_v8_path_executor,
)

get_realpath_tool = IkaTools(
    name="get_realpath",
    description="Resolve a path to its absolute canonical form. Useful for symlinks or relative paths. Output truncated to 1000 lines.",
    parameters={"path": {"type": "string", "description": "The path to get the realpath of", "required": True}},
    execute_function=_get_realpath_executor,
)

list_dir_tool = IkaTools(
    id="list_dir",
    name="list_dir",
    description="List entries in a V8 source directory. Use targeted relative paths like '.' or 'compiler'. Returns JSON names only.",
    parameters={
        "type": "object",
        "properties": {
            "target_directory": {
                "type": "string",
                "description": "Directory relative to V8_PATH to inspect.",
            }
        },
        "required": [],
    },
    execute_function=_list_dir_executor,
)

glob_search_tool = IkaTools(
    id="glob_search",
    name="glob_search",
    description="Searches for files matching a glob pattern under V8_PATH.",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "The glob pattern to search for relative to V8_PATH (for example, '**/*.cc').",
            }
        },
        "required": ["pattern"],
    },
    limit_calls=12,
    execute_function=_glob_search_executor,
)

grep_search_tool = IkaTools(
    id="grep_search",
    name="grep_search",
    description="Searches for a regex pattern in files under V8_PATH.",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "The regex pattern to search for.",
            }
        },
        "required": ["pattern"],
    },
    execute_function=_grep_search_executor,
)

read_file_tool = IkaTools(
    id="read_file",
    name="read_file",
    description=(
        "Reads file contents under V8_PATH. Small files: omit line_start/line_end to read the whole file. "
        "Files larger than the configured byte limit cannot be read in full; use line_start and line_end "
        f"(1-based inclusive line numbers). Each paged read returns at most {READ_FILE_MAX_LINES_IN_SLICE} lines per call."
    ),
    parameters={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to the file relative to V8_PATH. An optional 'v8/' prefix is also accepted; absolute paths are allowed only when still under V8_PATH.",
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
    execute_function=_read_file_executor,
)
