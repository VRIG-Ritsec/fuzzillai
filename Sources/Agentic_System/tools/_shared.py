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

V8_PATH = os.getenv("V8_PATH", "")
D8_PATH = os.getenv("D8_PATH", "")
FUZZILLI_PATH = os.getenv("FUZZILLI_PATH", "")
FUZZILLI_TOOL_BIN = os.getenv("FUZZILLI_TOOL_BIN", "")
SWIFT_PATH = os.path.join(FUZZILLI_PATH, "Sources", "Fuzzilli") if FUZZILLI_PATH else ""
D8_COMMON_FLAGS = "--allow-natives-syntax --experimental-fuzzing --expose-gc"

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


def read_file(file_path: str, section: int = None) -> str:
    if file_path.startswith("v8/"):
        resolved = os.path.join(V8_PATH, file_path[3:])
    elif not os.path.isabs(file_path):
        resolved = os.path.join(V8_PATH, file_path)
    else:
        resolved = file_path
    lc = get_output(run_command(f"cd {V8_PATH} && wc -l '{resolved}'"))
    try:
        line_count = int(lc.strip().split()[0])
    except Exception:
        return f"Could not determine lines. wc output: {lc}"
    lines_per_section = 3000
    num_sections = (line_count + lines_per_section - 1) // lines_per_section
    if line_count <= lines_per_section:
        return get_output(run_command(f"cd {V8_PATH} && cat '{resolved}'"))
    if section is None or section < 1 or section > num_sections:
        return (
            f"File has {line_count} lines, {num_sections} sections. "
            f"Specify section 1-{num_sections}."
        )
    start = 1 + (section - 1) * lines_per_section
    end = min(start + lines_per_section - 1, line_count)
    content = get_output(run_command(f"cd {V8_PATH} && sed -n '{start},{end}p' '{resolved}'"))
    return f"Section {section}/{num_sections} (lines {start}-{end}):\n{content}"


DEBUG_SESSION = {"js_path": "", "d8_args": ""}
MI_CONTROLLER = None


def _check_v8_binary():
    if not D8_PATH:
        return "Error: D8_PATH is not set"
    if not os.path.exists(D8_PATH):
        return f"Error: D8 not found at '{D8_PATH}'"
    return None


def _check_js_path(js_path: str):
    if not js_path:
        return "Error: js_path required"
    if not os.path.isabs(js_path):
        return f"Error: js_path must be absolute: {js_path}"
    if not os.path.exists(js_path):
        return f"Error: JS file not found: {js_path}"
    return None


def _format_args(js_path, d8_args):
    user = (d8_args or "").strip()
    if js_path:
        return f"{D8_COMMON_FLAGS} {user} {js_path}".strip()
    return f"{D8_COMMON_FLAGS} {user}".strip()


def _format_mi_responses(resp):
    try:
        return json.dumps(resp, indent=2)
    except Exception:
        return str(resp)


def start_mi_debug_session(js_path: str, d8_args: str = "") -> str:
    global MI_CONTROLLER
    if PygdbmiController is None:
        return "Error: pygdbmi not installed"
    err = _check_v8_binary()
    if err:
        return err
    err = _check_js_path(js_path)
    if err:
        return err
    DEBUG_SESSION["js_path"] = js_path
    DEBUG_SESSION["d8_args"] = d8_args or ""
    if MI_CONTROLLER is not None:
        try:
            MI_CONTROLLER.exit()
        except Exception:
            pass
        MI_CONTROLLER = None
    try:
        MI_CONTROLLER = PygdbmiController(command=["gdb", "--interpreter=mi4", "--quiet"])
        init_cmds = [
            "-gdb-set pagination off",
            "-gdb-set confirm off",
            "-gdb-set mi-async on",
            f"-file-exec-and-symbols {D8_PATH}",
        ]
        args = _format_args(js_path, d8_args)
        init_cmds.append(f"set args {args}")
        results = []
        for cmd in init_cmds:
            res = MI_CONTROLLER.write(cmd, timeout_sec=7.0)
            results.append({"cmd": cmd, "resp": res})
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
    r = gdb_run_command(f"break {source_file}:{line}")
    if r.startswith("Error"):
        return r
    return gdb_run_command("info breakpoints")


def gdb_print_value(expression: str) -> str:
    if not expression:
        return "Error: expression required"
    r = mi_run()
    if "Error" in r:
        return r
    return gdb_run_command(f"print {expression}")


def pwndbg_context() -> str:
    r = mi_run()
    if "Error" in r:
        return r
    return gdb_run_command("context")


def pwndbg_vmmap() -> str:
    r = mi_run()
    if "Error" in r:
        return r
    return gdb_run_command("vmmap")


def pwndbg_regs() -> str:
    r = mi_run()
    if "Error" in r:
        return r
    return gdb_run_command("regs")


def pwndbg_nearpc(count: int = 10) -> str:
    if count <= 0:
        return "Error: count must be positive"
    r = mi_run()
    if "Error" in r:
        return r
    return gdb_run_command(f"nearpc {count}")


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
    description="Search the web for V8, JavaScript, or fuzzing topics. Use for blog posts, papers, and external docs. Do NOT use to search V8 source; use ripgrep or v8_source_rag instead.",
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
    name="read_file",
    description="Read file contents. Supports large files via sectioned output (3000 lines per section). Use paths relative to V8_PATH or absolute paths.",
    parameters={
        "file_path": {"type": "string", "description": "Absolute or V8-relative path", "required": True},
        "section": {"type": "number", "description": "Section index (1-based) for chunked output", "required": False},
    },
    execute_function=lambda x: read_file(x["file_path"], x.get("section")),
)

start_mi_debug_session_tool = IkaTools(
    name="start_mi_debug_session",
    description="Start a GDB/MI debug session for a JavaScript file. Launches d8 under GDB for breakpoint debugging.",
    parameters={
        "js_path": {"type": "string", "description": "Path to the JS file to debug", "required": True},
        "d8_args": {"type": "string", "description": "Optional d8 flags (e.g. --allow-natives-syntax)", "required": False},
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
    description="Execute a GDB Machine Interface (MI) command in the active debug session",
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
    description="Run a GDB or pwndbg command (e.g. info registers, context, vmmap, backtrace). Do NOT run shell commands (pip, apt, cd).",
    parameters={"command": {"type": "string", "description": "GDB/pwndbg command", "required": True}},
    parallel=False,
    limit_calls=12,
    execute_function=lambda x: gdb_run_command(x["command"]),
)

gdb_set_breakpoint_tool = IkaTools(
    name="gdb_set_breakpoint",
    description="Set a GDB breakpoint at a specific source file and line number",
    parameters={
        "source_file": {"type": "string", "description": "Path to source file", "required": True},
        "line": {"type": "number", "description": "1-based line number", "required": True},
    },
    parallel=False,
    limit_calls=6,
    execute_function=lambda x: gdb_set_breakpoint(x["source_file"], int(x["line"])),
)

gdb_print_value_tool = IkaTools(
    name="gdb_print_value",
    description="Evaluate and print a variable or expression in GDB",
    parameters={"expression": {"type": "string", "description": "Variable name or C expression", "required": True}},
    parallel=False,
    limit_calls=6,
    execute_function=lambda x: gdb_print_value(x["expression"]),
)

pwndbg_context_tool = IkaTools(
    name="pwndbg_context",
    description="Display pwndbg context (registers, stack, disassembly around current instruction)",
    parameters={"N/A": "N/A"},
    parallel=False,
    limit_calls=3,
    execute_function=lambda _: pwndbg_context(),
)

pwndbg_vmmap_tool = IkaTools(
    name="pwndbg_vmmap",
    description="Display virtual memory map (mappings, permissions, addresses)",
    parameters={"N/A": "N/A"},
    parallel=False,
    limit_calls=3,
    execute_function=lambda _: pwndbg_vmmap(),
)

pwndbg_regs_tool = IkaTools(
    name="pwndbg_regs",
    description="Display CPU register values",
    parameters={"N/A": "N/A"},
    parallel=False,
    limit_calls=3,
    execute_function=lambda _: pwndbg_regs(),
)

pwndbg_nearpc_tool = IkaTools(
    name="pwndbg_nearpc",
    description="Disassemble instructions near the program counter",
    parameters={"count": {"type": "number", "description": "Number of instructions to show before/after PC", "required": False}},
    parallel=False,
    limit_calls=4,
    execute_function=lambda x: pwndbg_nearpc(int(x.get("count", 10))),
)
