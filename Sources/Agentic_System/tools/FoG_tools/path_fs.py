"""
FoG path and filesystem tools: run_python, get_v8_path, get_realpath, tree, ripgrep, fuzzy_finder, read_file.
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

from ._shared import (
    V8_PATH,
    D8_PATH,
    FUZZILLI_TOOL_BIN,
    run_command,
    get_output,
    is_valid_regex,
)


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
    return get_output(run_command(f"cd {V8_PATH} && realpath {path}"))


def _tree_executor(params: dict) -> str:
    options = params.get("options", "")
    depth = 2
    target_path = "."
    tokens = []
    if options:
        try:
            tokens = shlex.split(options)
        except ValueError:
            tokens = options.split()
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "-L":
            if i + 1 < len(tokens):
                try:
                    depth = int(tokens[i + 1])
                except ValueError:
                    depth = 2
                i += 2
                continue
        elif token.startswith("-L") and len(token) > 2:
            try:
                depth = int(token[2:])
            except ValueError:
                depth = 2
            i += 1
            continue
        elif not token.startswith("-"):
            target_path = token
        i += 1
    depth = max(1, min(depth, 2))
    candidate = os.path.join(V8_PATH, target_path) if not os.path.isabs(target_path) else target_path
    if not os.path.isdir(candidate):
        target_path = "."
    final_opts = f"-L {depth} -f {shlex.quote(target_path)}"
    return get_output(run_command(f"cd {shlex.quote(V8_PATH)} && tree {final_opts} | head -n 300"))


def _ripgrep_executor(params: dict) -> str:
    pattern = params.get("pattern", "")
    options = params.get("options", "")
    if not pattern:
        return "Error: pattern parameter is required"
    valid, error = is_valid_regex(pattern)
    if not valid:
        return f"Invalid regex passed in as pattern with error: {error}"
    if not options:
        return get_output(run_command(f"cd {V8_PATH} && rg '{pattern}' | head -n 10000", timeout=60))
    parts = options.split()
    flags = []
    i = 0
    while i < len(parts):
        part = parts[i]
        if part.startswith("-"):
            if part in ["--context", "-C", "--before-context", "-B", "--after-context", "-A"] and i + 1 < len(parts):
                try:
                    ctx_val = int(parts[i + 1])
                    flags.append(part)
                    flags.append(str(min(ctx_val, 5)))
                    i += 2
                    continue
                except ValueError:
                    pass
            elif part.startswith("--context="):
                try:
                    ctx_val = int(part.split("=", 1)[1])
                    flags.append(f"--context={min(ctx_val, 5)}")
                    i += 1
                    continue
                except ValueError:
                    pass
            elif part.startswith("--before-context="):
                try:
                    ctx_val = int(part.split("=", 1)[1])
                    flags.append(f"--before-context={min(ctx_val, 5)}")
                    i += 1
                    continue
                except ValueError:
                    pass
            elif part.startswith("--after-context="):
                try:
                    ctx_val = int(part.split("=", 1)[1])
                    flags.append(f"--after-context={min(ctx_val, 5)}")
                    i += 1
                    continue
                except ValueError:
                    pass
            elif part.startswith("-C") and len(part) > 2 and part[2:].isdigit():
                flags.append(f"-C{min(int(part[2:]), 5)}")
                i += 1
                continue
            elif part.startswith("-B") and len(part) > 2 and part[2:].isdigit():
                flags.append(f"-B{min(int(part[2:]), 5)}")
                i += 1
                continue
            elif part.startswith("-A") and len(part) > 2 and part[2:].isdigit():
                flags.append(f"-A{min(int(part[2:]), 5)}")
                i += 1
                continue
            flags.append(part)
            if part in ["--type", "--glob"] and i + 1 < len(parts):
                next_part = parts[i + 1]
                if not next_part.startswith("-") and not next_part.startswith("v8/"):
                    i += 1
                    flags.append(parts[i])
        else:
            flags.append(part)
        i += 1
    flags_str = " ".join(flags) if flags else ""
    return get_output(run_command(f"cd {V8_PATH} && rg '{pattern}' {flags_str} | head -n 1000", timeout=60))


def _fuzzy_finder_executor(params: dict) -> str:
    pattern = params.get("pattern", "")
    options = params.get("options", "")
    if not pattern:
        return "Error: pattern parameter is required"
    file_list_cmd = "rg --hidden --no-follow --no-ignore-vcs --files 2>/dev/null"
    options = options.strip() if options else ""
    quoted_pattern = shlex.quote(pattern)
    if "--filter" in options:
        fzf_cmd = f"fzf {options}"
    else:
        fzf_cmd = f"fzf {options} --filter {quoted_pattern}".strip()
    cmd = f"cd {shlex.quote(V8_PATH)} && {file_list_cmd} | {fzf_cmd} | head -n 1000"
    return get_output(run_command(cmd, timeout=60))


def _read_file_executor(params: dict) -> str:
    file_path = params.get("file_path", "")
    section = params.get("section")
    if not file_path:
        return "Error: file_path parameter is required"
    if file_path.startswith("v8/"):
        resolved_path = os.path.join(V8_PATH, file_path[3:])
    elif not os.path.isabs(file_path):
        resolved_path = os.path.join(V8_PATH, file_path)
    else:
        resolved_path = file_path
    line_count_result = get_output(run_command(f"cd {V8_PATH} && wc -l '{resolved_path}'"))
    try:
        line_count = int(line_count_result.strip().split()[0])
    except Exception:
        return f"Could not determine number of lines in file. wc -l output: {line_count_result}"
    lines_per_section = 3000
    num_sections = (line_count + lines_per_section - 1) // lines_per_section
    if line_count <= lines_per_section:
        return get_output(run_command(f"cd {V8_PATH} && cat '{resolved_path}'"))
    if section is None:
        try:
            section = int(params.get("section", 0))
        except (ValueError, TypeError):
            section = 0
    if section < 1 or section > num_sections:
        return (
            f"File '{file_path}' has {line_count} lines and is divided into {num_sections} sections "
            f"(each section is 3000 lines). Specify a section number between 1 and {num_sections}."
        )
    start_line = 1 + (section - 1) * lines_per_section
    end_line = min(start_line + lines_per_section - 1, line_count)
    content = get_output(run_command(f"cd {V8_PATH} && sed -n '{start_line},{end_line}p' '{resolved_path}'"))
    return f"Showing section {section}/{num_sections} (lines {start_line}-{end_line}) of '{file_path}':\n{content}"


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

tree_tool = IkaTools(
    name="tree",
    description="Show directory tree for V8 source. Scoped to v8/src. Use to explore folder layout. Max depth 2, max 300 lines.",
    parameters={"options": {"type": "string", "description": "Tree options: -L NUM, -f, PATH", "required": False}},
    execute_function=_tree_executor,
)

ripgrep_tool = IkaTools(
    name="ripgrep",
    description="Search V8 source for text or regex. Use for finding function names, macros, or specific code. Output truncated to 1000 lines.",
    parameters={
        "pattern": {"type": "string", "description": "The text or regex pattern", "required": True},
        "options": {"type": "string", "description": "Additional ripgrep options", "required": False},
    },
    execute_function=_ripgrep_executor,
)

fuzzy_finder_tool = IkaTools(
    name="fuzzy_finder",
    description="Fuzzy-search V8 files by partial filename. Use when you know part of a path (e.g. 'ic.cc'). Output truncated to 1000 lines.",
    parameters={
        "pattern": {"type": "string", "description": "The search pattern", "required": True},
        "options": {"type": "string", "description": "Additional fzf options", "required": False},
    },
    limit_calls=12,
    execute_function=_fuzzy_finder_executor,
)

read_file_tool = IkaTools(
    name="read_file",
    description="Read file contents from V8 source or absolute path. Use full path or path relative to v8/src. Max 3000 lines per section.",
    parameters={
        "file_path": {"type": "string", "description": "The path to the file", "required": True},
        "section": {"type": "integer", "description": "Section number for multi-section files", "required": False},
    },
    execute_function=_read_file_executor,
)
