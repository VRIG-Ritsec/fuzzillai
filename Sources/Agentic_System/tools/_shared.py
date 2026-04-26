"""
Shared tools: CFG, web search, read_file, GDB/MI/pwndbg.
Used by both FoG and EBG agents. No @tool decorator, IkaCore only.
"""

import os
import sys
import json
import re
import subprocess
from pathlib import Path

_tools_dir = Path(__file__).resolve().parent
_agentic_dir = _tools_dir.parent
_ikacore_src = _agentic_dir / "IkaCore" / "src"
if str(_ikacore_src) not in sys.path:
    sys.path.insert(0, str(_ikacore_src))

from IkaCore.tools import IkaTools
from tools.fs_tools import (
    read_file_from_base,
    MAX_TOOL_RESULT_BYTES,
    READ_FILE_MAX_LINES_IN_SLICE,
)
from config_loader import (
    apply_runtime_paths,
    get_d8_path,
    get_fuzzilli_path,
    get_fuzzilli_tool_bin,
    get_v8_path,
)

apply_runtime_paths()
V8_PATH = get_v8_path()
D8_PATH = get_d8_path()
FUZZILLI_PATH = get_fuzzilli_path()
FUZZILLI_TOOL_BIN = get_fuzzilli_tool_bin()
SWIFT_PATH = os.path.join(FUZZILLI_PATH, "Sources", "Fuzzilli") if FUZZILLI_PATH else ""
D8_COMMON_FLAGS = "--allow-natives-syntax --experimental-fuzzing --expose-gc"
_RUNTIME_DATA_DIR = _agentic_dir / "runtime_data"
_DEFAULT_D8_OUTPUT_DIR = _RUNTIME_DATA_DIR / "d8_artifacts"

try:
    from pygdbmi.gdbcontroller import GdbController as PygdbmiController
except Exception:
    PygdbmiController = None

try:
    from duckduckgo_search import DDGS
    DDGS_AVAILABLE = True
except Exception:
    DDGS_AVAILABLE = False
    DDGS = None

cfg_builder = None
if V8_PATH and "src" in V8_PATH:
    try:
        from tools.cfg_tool import CFGBuilder
        cfg_builder = CFGBuilder(V8_PATH)
        cfg_builder.parse_directory(V8_PATH, pattern="*.cc")
        if not getattr(cfg_builder, "_finalized", False):
            cfg_builder.finalize_call_graph()
            cfg_builder._finalized = True
    except Exception as e:
        cfg_builder = None


def run_command(command: str, timeout: int = 90):
    try:
        return subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        class TimeoutResult:
            def __init__(self, timeout_sec, cmd):
                self.stdout = ""
                self.stderr = f"Command timed out after {timeout_sec} seconds: {cmd}"
                self.returncode = -1
                self.args = cmd
        return TimeoutResult(timeout, command)


def _error_process(args, message: str, returncode: int = 127):
    return subprocess.CompletedProcess(args=args, returncode=returncode, stdout="", stderr=message)


def run_process(args: list[str], timeout: int = 90, cwd: str | None = None, env: dict | None = None):
    if not args:
        return _error_process(args, "Error: no command provided")
    try:
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return _error_process(args, f"Command timed out after {timeout} seconds: {' '.join(args)}", returncode=-1)


def get_output(completed_process) -> str:
    if not completed_process:
        return ""
    out = completed_process.stdout if completed_process.stdout else None
    err = completed_process.stderr if completed_process.stderr else None
    return out if out else (err if err else "")


def _build_call_graph_hashmap():
    cg = {}
    if cfg_builder is None:
        return cg
    for full_name, info in cfg_builder.call_graph.items():
        entry_id = str(info.get("entry")) if info.get("entry") is not None else None
        exit_id = str(info.get("exit")) if info.get("exit") is not None else None
        file_path = None
        line_number = None
        if info.get("location"):
            file_path = info["location"].get("file")
            line_number = info["location"].get("line")
        simple_name = info.get("function_name") or (full_name.split("::")[-1] if "::" in full_name else full_name)
        cg[full_name] = {
            "function_name": simple_name,
            "entry_nodes": [entry_id] if entry_id else [],
            "exit_nodes": [exit_id] if exit_id else [],
            "file_path": file_path,
            "line_number": line_number,
        }
    return cg


CALL_GRAPH_HASHMAP = _build_call_graph_hashmap()

DEFAULT_CFG_JSON = _tools_dir / "cfg" / "v8_cfg_output.json"
CFG_JSON_PATH = Path(os.getenv("V8_CFG_JSON_PATH", str(DEFAULT_CFG_JSON)))


def _load_cfg_json():
    if not CFG_JSON_PATH.exists():
        return {}
    try:
        with open(CFG_JSON_PATH, "r") as f:
            data = json.load(f)
        return data.get("cfgs", {})
    except Exception:
        return {}


CFG_MAP = _load_cfg_json()
CALL_GRAPH_MAP = CALL_GRAPH_HASHMAP


def read_file(file_path: str, line_start: int = None, line_end: int = None) -> str:
    params = {
        "file_path": file_path[3:] if file_path.startswith("v8/") else file_path,
        "line_start": line_start,
        "line_end": line_end,
    }
    return read_file_from_base(params, V8_PATH)

DEBUG_SESSION = {"js_path": "", "d8_args": ""}
MI_CONTROLLER = None


def resolve_js_path(js_path: str) -> str:
    candidate = (js_path or "").strip()
    if not candidate:
        return ""

    raw_path = Path(candidate).expanduser()
    search_paths: list[Path] = []

    if raw_path.is_absolute():
        search_paths.append(raw_path)
    else:
        search_paths.append(Path.cwd() / raw_path)
        try:
            from tools.EBG_tools._shared import _get_varianal_folder

            search_paths.append(Path(_get_varianal_folder()) / raw_path)
        except Exception:
            pass
        try:
            from tools.FoG_tools._shared import GENERATED_TEMPLATE_DIR

            if GENERATED_TEMPLATE_DIR:
                search_paths.append(Path(GENERATED_TEMPLATE_DIR) / raw_path)
        except Exception:
            pass

    seen = set()
    for path in search_paths:
        resolved = str(path.resolve())
        if resolved in seen:
            continue
        seen.add(resolved)
        if path.exists():
            return resolved

    return str(raw_path.resolve()) if raw_path.is_absolute() else ""


def _check_v8_binary():
    if not D8_PATH:
        return "Error: D8_PATH is not set"
    if not os.path.exists(D8_PATH):
        return f"Error: D8 not found at '{D8_PATH}'"
    return None


def _check_fuzzilli_tool_bin():
    if not FUZZILLI_TOOL_BIN:
        return "Error: FUZZILLI_TOOL_BIN is not set"
    if not os.path.exists(FUZZILLI_TOOL_BIN):
        return f"Error: FuzzILTool not found at '{FUZZILLI_TOOL_BIN}'"
    return None


def _prepare_js_path(js_path: str):
    if not js_path:
        return None, "Error: js_path required"
    resolved_js_path = resolve_js_path(js_path)
    if not resolved_js_path or not os.path.exists(resolved_js_path):
        return None, f"Error: JS file not found: {js_path}"
    return resolved_js_path, None


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _runtime_artifact_dir(js_path: str | None = None) -> str:
    runtime_root = _RUNTIME_DATA_DIR.resolve()
    default_dir = _DEFAULT_D8_OUTPUT_DIR.resolve()

    if js_path:
        try:
            candidate = Path(js_path).expanduser().resolve().parent
            if _is_relative_to(candidate, runtime_root):
                candidate.mkdir(parents=True, exist_ok=True)
                return str(candidate)
        except Exception:
            pass

    default_dir.mkdir(parents=True, exist_ok=True)
    return str(default_dir)


def _format_args(js_path, d8_args):
    user = (d8_args or "").strip()
    if js_path:
        return f"{D8_COMMON_FLAGS} {user} {js_path}".strip()
    return f"{D8_COMMON_FLAGS} {user}".strip()


def run_d8_command(extra_args: list[str], timeout: int = 90, cwd: str | None = None):
    err = _check_v8_binary()
    if err:
        return _error_process([D8_PATH, *extra_args], err)
    return run_process([D8_PATH, *extra_args], timeout=timeout, cwd=cwd or _runtime_artifact_dir())


def run_d8(js_path: str, flags: list[str] | None = None, timeout: int = 90):
    resolved_js_path, err = _prepare_js_path(js_path)
    if err:
        return _error_process([D8_PATH, *(flags or []), js_path], err)
    return run_d8_command(
        [*(flags or []), resolved_js_path],
        timeout=timeout,
        cwd=_runtime_artifact_dir(resolved_js_path),
    )


def run_fuzzilli_tool(extra_args: list[str], timeout: int = 90, cwd: str | None = None, env: dict | None = None):
    err = _check_fuzzilli_tool_bin()
    if err:
        return _error_process([FUZZILLI_TOOL_BIN, *extra_args], err)
    return run_process([FUZZILLI_TOOL_BIN, *extra_args], timeout=timeout, cwd=cwd, env=env)


def _format_mi_responses(resp):
    try:
        return json.dumps(resp, indent=2)
    except Exception:
        return str(resp)


def _gdb_dwarf_src_prefix() -> str:
    """Path prefix stored in V8/d8 DWARF (relative to GN out dir), e.g. ../../src."""
    p = (os.getenv("GDB_DWARF_SRC_PREFIX") or "../../src").strip()
    return p if p else "../../src"


def _resolve_break_location(source_file: str, line: int) -> tuple[str, list[str]]:
    """
    Build a GDB 'break location' (file:line) that matches DWARF paths.
    Chromium GN builds record sources as ../../src/<path-under-src>/file.cc.
    """
    notes: list[str] = []
    sf = (source_file or "").strip()
    if not sf:
        return "", ["empty source_file"]

    norm_sf = sf.replace("\\", "/")

    if norm_sf.startswith("../"):
        return f"{norm_sf}:{line}", notes

    dwarf_p = _gdb_dwarf_src_prefix().rstrip("/")
    vp = get_v8_path().strip()
    if not vp:
        notes.append(
            "V8_PATH is not set: cannot rewrite to ../../src/...; use a DWARF path "
            "(e.g. ../../src/d8/d8.cc) or set V8_PATH to your V8 src root and use an absolute file path."
        )
        return f"{norm_sf}:{line}", notes

    v8_root = Path(vp).expanduser().resolve()
    if not v8_root.is_dir():
        notes.append("V8_PATH is not a directory; breakpoint path not rewritten.")
        return f"{norm_sf}:{line}", notes

    in_path = Path(norm_sf)
    if in_path.is_absolute():
        try:
            rel = in_path.resolve().relative_to(v8_root)
        except ValueError:
            notes.append(
                "source_file is absolute but not under V8_PATH; passing through unchanged "
                "(likely to fail unless you use gdb_run_command with a matching DWARF path)."
            )
            return f"{norm_sf}:{line}", notes
    else:
        rel = Path(norm_sf)

    rel_posix = rel.as_posix().lstrip("./")
    loc_path = f"{dwarf_p}/{rel_posix}"
    notes.append(
        f"Using break {loc_path}:{line} (DWARF prefix {dwarf_p!r} + path under V8_PATH). "
        f"start_mi_debug_session runs set substitute-path {dwarf_p} <V8_PATH> when V8_PATH is set."
    )
    return f"{loc_path}:{line}", notes


def start_mi_debug_session(js_path: str, d8_args: str = "") -> str:
    global MI_CONTROLLER
    if PygdbmiController is None:
        return "Error: pygdbmi not installed"
    err = _check_v8_binary()
    if err:
        return err
    resolved_js_path, err = _prepare_js_path(js_path)
    if err:
        return err
    DEBUG_SESSION["js_path"] = resolved_js_path
    DEBUG_SESSION["d8_args"] = d8_args or ""
    if MI_CONTROLLER is not None:
        try:
            MI_CONTROLLER.exit()
        except Exception:
            pass
        MI_CONTROLLER = None
    try:
        MI_CONTROLLER = PygdbmiController(command=["gdb", "--interpreter=mi4", "--quiet"])
        session_cwd = _runtime_artifact_dir(resolved_js_path)
        init_cmds = [
            "-gdb-set pagination off",
            "-gdb-set confirm off",
            "-gdb-set mi-async on",
            f"-environment-cd {session_cwd}",
            f"-file-exec-and-symbols {D8_PATH}",
        ]
        args = _format_args(resolved_js_path, d8_args)
        init_cmds.append(f"set args {args}")
        results = []
        for cmd in init_cmds:
            res = MI_CONTROLLER.write(cmd, timeout_sec=7.0)
            results.append({"cmd": cmd, "resp": res})

        vp = get_v8_path().strip()
        if vp:
            vp_abs = str(Path(vp).expanduser().resolve())
            if os.path.isdir(vp_abs):
                prefix = _gdb_dwarf_src_prefix()
                for extra in (
                    f"set substitute-path {prefix} {vp_abs}",
                    f"directory {vp_abs}",
                ):
                    res = MI_CONTROLLER.write(extra, timeout_sec=7.0)
                    results.append({"cmd": extra, "resp": res})

        return "MI debug session started.\n" + _format_mi_responses(results)
    except Exception as e:
        MI_CONTROLLER = None
        return f"Error starting MI session: {e}"


def stop_mi_debug_session() -> str:
    global MI_CONTROLLER
    if MI_CONTROLLER is not None:
        try:
            MI_CONTROLLER.exit()
        except Exception as e:
            MI_CONTROLLER = None
            return f"Stopped with warning: {e}"
        MI_CONTROLLER = None
        return "MI debug session stopped."
    return "No active MI session."


def mi_exec(command: str) -> str:
    if PygdbmiController is None:
        return "Error: pygdbmi not installed"
    if MI_CONTROLLER is None:
        return "Error: No active MI session"
    if not command:
        return "Error: command required"
    try:
        resp = MI_CONTROLLER.write(command, timeout_sec=7.0)
        return _format_mi_responses(resp)
    except Exception as e:
        return f"Error: {e}"


def mi_run() -> str:
    return mi_exec("-exec-run")


def mi_continue() -> str:
    return mi_exec("-exec-continue")


def mi_next() -> str:
    return mi_exec("-exec-next")


def mi_step() -> str:
    return mi_exec("-exec-step")


def gdb_run_command(command: str) -> str:
    if PygdbmiController is None:
        return "Error: pygdbmi not installed"
    if MI_CONTROLLER is None:
        return "Error: No active MI session"
    if not command:
        return "Error: command required"
    invalid = ["pip", "apt", "apt-get", "yum", "npm", "cd", "ls", "cat", "echo", "mkdir", "rm", "cp", "mv"]
    first = command.strip().lower().split()[0] if command.strip() else ""
    if first in invalid:
        return json.dumps({"error": f"'{first}' is not a GDB command", "hint": "Use info, break, run, continue, etc."})
    try:
        resp = MI_CONTROLLER.write(command, timeout_sec=7.0)
        return _format_mi_responses(resp)
    except Exception as e:
        return f"Error: {e}"


def gdb_set_breakpoint(source_file: str, line: int) -> str:
    if not source_file:
        return "Error: source_file required"
    if line <= 0:
        return "Error: line must be positive"
    loc, notes = _resolve_break_location(source_file, line)
    if not loc:
        return "Error: could not resolve breakpoint location"
    br = gdb_run_command(f"break {loc}")
    if br.startswith("Error"):
        return br
    info = gdb_run_command("info breakpoints")
    if notes:
        hdr = "Breakpoint routing:\n" + "\n".join(f"- {n}" for n in notes) + "\n\n"
        return hdr + "break:\n" + br + "\ninfo breakpoints:\n" + info
    return br + "\n" + info


def _mi_quote_c_string(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _extract_eval_hex_address(mi_response: str) -> str | None:
    m = re.search(r'"value"\s*:\s*"(0x[0-9a-fA-F]+)"', mi_response)
    return m.group(1) if m else None


def _mi_disassemble_near_pc(count: int) -> str:
    pc_raw = mi_exec('-data-evaluate-expression "$pc"')
    if pc_raw.startswith("Error"):
        return pc_raw
    addr = _extract_eval_hex_address(pc_raw)
    if not addr:
        return (
            "Could not parse $pc from -data-evaluate-expression.\n"
            + pc_raw
        )
    try:
        start_i = int(addr, 16)
    except ValueError:
        return pc_raw
    span = max(32, min(512, count * 16))
    end_hex = hex(start_i + span)
    dis = mi_exec(f"-data-disassemble -s {addr} -e {end_hex}")
    return f"--- $pc eval ---\n{pc_raw}\n\n--- disassemble ---\n{dis}"


def _mi_inspection_join(parts: list[str]) -> str:
    return "\n\n".join(parts)


def gdb_print_value(expression: str) -> str:
    if not expression:
        return "Error: expression required"
    expr = expression.strip()
    return mi_exec("-data-evaluate-expression " + _mi_quote_c_string(expr))


def pwndbg_context() -> str:
    return _mi_inspection_join(
        [
            "--- stack-list-frames ---",
            mi_exec("-stack-list-frames"),
            "--- data-list-register-values x ---",
            mi_exec("-data-list-register-values x"),
            _mi_disassemble_near_pc(8),
        ]
    )


def pwndbg_vmmap() -> str:
    return mi_exec('-interpreter-exec console "info proc mappings"')


def pwndbg_regs() -> str:
    return mi_exec("-data-list-register-values x")


def pwndbg_nearpc(count: int = 10) -> str:
    if count <= 0:
        return "Error: count must be positive"
    return _mi_disassemble_near_pc(count)


def _web_search_executor(params: dict) -> str:
    query = params.get("query", "")
    if not query:
        return "Error: query required"
    if not DDGS_AVAILABLE or DDGS is None:
        return "Web search not configured. Install duckduckgo_search."
    try:
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=5):
                title = r.get("title", "")
                href = r.get("href", r.get("link", ""))
                body = r.get("body", r.get("snippet", ""))
                results.append(f"- {title}\n  {href}\n  {body}")
        return "\n\n".join(results) if results else "No results found."
    except Exception as e:
        return f"Error: {e}"


def _get_cfg_for_executor(params: dict) -> str:
    fn = params.get("function_name", "")
    if not fn:
        return json.dumps({"error": "function_name required"})
    cfg = CFG_MAP.get(fn)
    return json.dumps(cfg, indent=2) if cfg else json.dumps({"error": f"CFG not found for {fn}"})


def _get_call_graph_hashmap_executor(params: dict) -> str:
    try:
        return json.dumps(CALL_GRAPH_HASHMAP, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


def _find_functions_by_simple_name_executor(params: dict) -> str:
    name = params.get("simple_name", "")
    if not name:
        return json.dumps([])
    matches = []
    for full_name, info in CALL_GRAPH_MAP.items():
        if info.get("function_name") == name or name in full_name:
            matches.append(full_name)
    return json.dumps(matches)


def _find_functions_by_fully_qualified_name_executor(params: dict) -> str:
    name = params.get("fully_qualified_name", "")
    if not name:
        return json.dumps([])
    matches = [fn for fn in CALL_GRAPH_MAP if fn == name]
    return json.dumps(matches)


def _get_call_graph_node_executor(params: dict) -> str:
    fn = params.get("function_name", "")
    if not fn:
        return json.dumps({"error": "function_name required"})
    node = CALL_GRAPH_MAP.get(fn)
    return json.dumps(node, indent=2) if node else json.dumps({"error": f"Node not found for {fn}"})


web_search_tool = IkaTools(
    name="web_search",
    description="Search the web for V8, JavaScript, or fuzzing topics. Use for blog posts, papers, and external docs. Do NOT use to search V8 source; use grep_search or v8_source_rag instead.",
    parameters={"query": {"type": "string", "description": "Natural language search question", "required": True}},
    execute_function=_web_search_executor,
)

get_cfg_for_tool = IkaTools(
    name="get_cfg_for",
    description="Retrieve the control-flow graph for a V8 C++ function by its fully qualified name (e.g. v8::internal::JSFunction::Create).",
    parameters={"function_name": {"type": "string", "description": "Fully qualified C++ function name", "required": True}},
    execute_function=_get_cfg_for_executor,
)

get_call_graph_hashmap_tool = IkaTools(
    name="get_call_graph_hashmap",
    description="Return the full V8 call graph as a map of entry/exit nodes and source locations. Use to explore function call relationships.",
    parameters={"input": {"type": "string", "description": "Unused; no input required", "required": False}},
    execute_function=_get_call_graph_hashmap_executor,
)

find_functions_by_simple_name_tool = IkaTools(
    name="find_functions_by_simple_name",
    description="Find V8 C++ functions whose simple name matches (e.g. Create yields v8::internal::JSFunction::Create).",
    parameters={"simple_name": {"type": "string", "description": "Short function name or substring", "required": True}},
    execute_function=_find_functions_by_simple_name_executor,
)

find_functions_by_fully_qualified_name_tool = IkaTools(
    name="find_functions_by_fully_qualified_name",
    description="Find V8 C++ functions by fully qualified name or prefix (e.g. v8::internal::Builtins::).",
    parameters={"fully_qualified_name": {"type": "string", "description": "Full or partial qualified name", "required": True}},
    execute_function=_find_functions_by_fully_qualified_name_executor,
)

get_call_graph_node_tool = IkaTools(
    name="get_call_graph_node",
    description="Get the call graph node (callers/callees, location) for a V8 function.",
    parameters={"function_name": {"type": "string", "description": "Fully qualified function name", "required": True}},
    execute_function=_get_call_graph_node_executor,
)

read_file_tool = IkaTools(
    id="read_file",
    name="read_file",
    description=(
        "Reads file contents under V8_PATH. Small files: omit line_start/line_end to read the whole file. "
        f"Reads are capped at {MAX_TOOL_RESULT_BYTES} bytes. Files or slices beyond that limit cannot be read in full; use line_start and line_end "
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
    execute_function=lambda x: read_file(x["file_path"], x.get("line_start"), x.get("line_end")),
)

start_mi_debug_session_tool = IkaTools(
    name="start_mi_debug_session",
    description=(
        "GDB MI4 + d8: set args, load symbols. With V8_PATH set, applies set substitute-path (GDB_DWARF_SRC_PREFIX "
        "default ../../src) and directory so file:line breakpoints work. Needs D8_PATH, pygdbmi."
    ),
    parameters={
        "js_path": {"type": "string", "description": "Absolute .js path or resolvable generated file name", "required": True},
        "d8_args": {"type": "string", "description": "Extra d8 CLI flags after defaults", "required": False},
    },
    parallel=False,
    limit_calls=2,
    execute_function=lambda x: start_mi_debug_session(x["js_path"], x.get("d8_args", "")),
)

stop_mi_debug_session_tool = IkaTools(
    name="stop_mi_debug_session",
    description="Stop the active GDB/MI debug session",
    parameters={"N/A": "N/A"},
    parallel=False,
    execute_function=lambda _: stop_mi_debug_session(),
)

mi_exec_tool = IkaTools(
    name="mi_exec",
    description=(
        "GDB MI on active session. Use for stopped-state inspection; no implicit -exec-run unless you pass it."
    ),
    parameters={"command": {"type": "string", "description": "MI command string", "required": True}},
    parallel=False,
    limit_calls=12,
    execute_function=lambda x: mi_exec(x["command"]),
)

mi_run_tool = IkaTools(
    name="mi_run",
    description="Run the program in the active MI debug session",
    parameters={"N/A": "N/A"},
    parallel=False,
    limit_calls=2,
    execute_function=lambda _: mi_run(),
)

mi_continue_tool = IkaTools(
    name="mi_continue",
    description="Continue execution after a breakpoint in the MI debug session",
    parameters={"N/A": "N/A"},
    parallel=False,
    limit_calls=2,
    execute_function=lambda _: mi_continue(),
)

mi_next_tool = IkaTools(
    name="mi_next",
    description="Step over (next instruction) in the MI debug session",
    parameters={"N/A": "N/A"},
    parallel=False,
    limit_calls=8,
    execute_function=lambda _: mi_next(),
)

mi_step_tool = IkaTools(
    name="mi_step",
    description="Step into (single instruction) in the MI debug session",
    parameters={"N/A": "N/A"},
    parallel=False,
    limit_calls=8,
    execute_function=lambda _: mi_step(),
)

gdb_run_command_tool = IkaTools(
    name="gdb_run_command",
    description=(
        "GDB console command (not shell). Prefer **mi_exec**, **gdb_print_value**, **pwndbg_*** for post-stop inspection "
        "(MI-based, no implicit -exec-run). Use for gaps like **info breakpoints** or symbol-only **break**."
    ),
    parameters={"command": {"type": "string", "description": "GDB/pwndbg command", "required": True}},
    parallel=False,
    limit_calls=12,
    execute_function=lambda x: gdb_run_command(x["command"]),
)

gdb_set_breakpoint_tool = IkaTools(
    name="gdb_set_breakpoint",
    description=(
        "Source line breakpoint: rewrites absolute/relative paths under V8_PATH to ../../src/... for DWARF. "
        "Requires V8_PATH + prior start_mi_debug_session (applies substitute-path). "
        "Use gdb_run_command for symbol-only breaks."
    ),
    parameters={
        "source_file": {
            "type": "string",
            "description": "Absolute under V8_PATH, or relative to V8_PATH (d8/d8.cc), or DWARF path starting with ../",
            "required": True,
        },
        "line": {"type": "number", "description": "GDB line number (debug info), not always editor line", "required": True},
    },
    parallel=False,
    limit_calls=6,
    execute_function=lambda x: gdb_set_breakpoint(x["source_file"], int(x["line"])),
)

gdb_print_value_tool = IkaTools(
    name="gdb_print_value",
    description="MI -data-evaluate-expression when stopped; does not restart the inferior",
    parameters={"expression": {"type": "string", "description": "Variable name or C expression", "required": True}},
    parallel=False,
    limit_calls=6,
    execute_function=lambda x: gdb_print_value(x["expression"]),
)

pwndbg_context_tool = IkaTools(
    name="pwndbg_context",
    description="MI stack-list-frames + register list + disassemble near $pc; safe when stopped, no restart",
    parameters={"N/A": "N/A"},
    parallel=False,
    limit_calls=3,
    execute_function=lambda _: pwndbg_context(),
)

pwndbg_vmmap_tool = IkaTools(
    name="pwndbg_vmmap",
    description="MI -interpreter-exec console `info proc mappings`; safe when stopped, no restart",
    parameters={"N/A": "N/A"},
    parallel=False,
    limit_calls=3,
    execute_function=lambda _: pwndbg_vmmap(),
)

pwndbg_regs_tool = IkaTools(
    name="pwndbg_regs",
    description="MI -data-list-register-values x when stopped; does not restart",
    parameters={"N/A": "N/A"},
    parallel=False,
    limit_calls=3,
    execute_function=lambda _: pwndbg_regs(),
)

pwndbg_nearpc_tool = IkaTools(
    name="pwndbg_nearpc",
    description="MI disassemble window from $pc when stopped; does not restart",
    parameters={"count": {"type": "number", "description": "Number of instructions to show before/after PC", "required": False}},
    parallel=False,
    limit_calls=4,
    execute_function=lambda x: pwndbg_nearpc(int(x.get("count", 10))),
)
