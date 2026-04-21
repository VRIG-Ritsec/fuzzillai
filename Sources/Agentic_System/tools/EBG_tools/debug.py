"""
Debug tools: GDB/MI/pwndbg wrappers as IkaTools.
"""

from IkaCore.tools import IkaTools
from tools.fs_tools import READ_FILE_MAX_LINES_IN_SLICE

from tools._shared import (
    read_file,
    start_mi_debug_session,
    stop_mi_debug_session,
    mi_exec,
    mi_run,
    mi_continue,
    mi_next,
    mi_step,
    gdb_run_command,
    gdb_set_breakpoint,
    gdb_print_value,
    pwndbg_context,
    pwndbg_vmmap,
    pwndbg_regs,
    pwndbg_nearpc,
)

read_file_tool = IkaTools(
    id="read_file",
    name="read_file",
    description=(
        "Reads file contents under V8_PATH for debugging. Small files: omit line_start/line_end "
        "to read the whole file. Files larger than the configured byte limit cannot be read in full; "
        f"use line_start and line_end (1-based inclusive line numbers). Each paged read returns at most {READ_FILE_MAX_LINES_IN_SLICE} lines per call."
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
        "Start a GDB MI4 session: loads D8_PATH under gdb, sets program args (default fuzz flags + js_path). "
        "If V8_PATH is set to the V8 **src** root (directory containing d8/, third_party/, etc.), runs "
        "`set substitute-path <prefix> <V8_PATH>` (prefix from GDB_DWARF_SRC_PREFIX, default ../../src) and "
        "`directory <V8_PATH>` so line breakpoints and source listings resolve. Requires pygdbmi and D8_PATH."
    ),
    parameters={
        "js_path": {
            "type": "string",
            "description": "Absolute path to the .js file, or a name resolvable via generate_folder / cwd",
            "required": True,
        },
        "d8_args": {
            "type": "string",
            "description": "Extra d8 flags after built-in defaults (allow-natives-syntax, experimental-fuzzing, expose-gc)",
            "required": False,
        },
    },
    parallel=False,
    limit_calls=2,
    execute_function=lambda x: start_mi_debug_session(x["js_path"], x.get("d8_args", "")),
)

stop_mi_debug_session_tool = IkaTools(
    name="stop_mi_debug_session",
    description="Stop the active GDB MI debug session and terminate d8.",
    parameters={"N/A": "N/A"},
    parallel=False,
    execute_function=lambda _: stop_mi_debug_session(),
)

mi_exec_tool = IkaTools(
    name="mi_exec",
    description=(
        "Execute a GDB MI command on the active session. Preferred for inspection when stopped "
        "(-data-evaluate-expression, -stack-list-frames, -data-list-register-values, -data-disassemble, etc.); "
        "does not implicitly restart the debuggee unless the command does (e.g. -exec-run)."
    ),
    parameters={
        "command": {"type": "string", "description": "MI command string (e.g., -exec-next)", "required": True},
    },
    parallel=False,
    limit_calls=12,
    execute_function=lambda x: mi_exec(x["command"]),
)

mi_run_tool = IkaTools(
    name="mi_run",
    description="Run the debuggee. Starts execution after start_mi_debug_session. Use mi_continue to resume after a breakpoint.",
    parameters={"N/A": "N/A"},
    parallel=False,
    limit_calls=2,
    execute_function=lambda _: mi_run(),
)

mi_continue_tool = IkaTools(
    name="mi_continue",
    description="Resume execution until the next breakpoint or program exit.",
    parameters={"N/A": "N/A"},
    parallel=False,
    limit_calls=2,
    execute_function=lambda _: mi_continue(),
)

mi_next_tool = IkaTools(
    name="mi_next",
    description="Step over: execute the current line and stop at the next line (steps over function calls).",
    parameters={"N/A": "N/A"},
    parallel=False,
    limit_calls=8,
    execute_function=lambda _: mi_next(),
)

mi_step_tool = IkaTools(
    name="mi_step",
    description="Step into: execute one instruction or line, entering function calls.",
    parameters={"N/A": "N/A"},
    parallel=False,
    limit_calls=8,
    execute_function=lambda _: mi_step(),
)

gdb_run_command_tool = IkaTools(
    name="gdb_run_command",
    description=(
        "Run a GDB console command (not shell). Prefer **mi_exec**, **gdb_print_value**, and **pwndbg_*** tools "
        "for inspection when stopped; they are MI-based and do not restart the debuggee. Use this for commands "
        "without a good MI mapping (e.g. info breakpoints, symbol-only break)."
    ),
    parameters={
        "command": {"type": "string", "description": "GDB/pwndbg command", "required": True},
    },
    parallel=False,
    limit_calls=12,
    execute_function=lambda x: gdb_run_command(x["command"]),
)

gdb_set_breakpoint_tool = IkaTools(
    name="gdb_set_breakpoint",
    description=(
        "Set a breakpoint on a C++ source line. Rewrites paths for GN/d8 DWARF: if V8_PATH is set, an absolute "
        "file under V8_PATH (e.g. .../src/d8/d8.cc) or a path relative to V8_PATH (e.g. d8/d8.cc) becomes "
        "break ../../src/<relpath>:line (prefix overridable via GDB_DWARF_SRC_PREFIX). "
        "Paths already starting with ../ are sent to GDB unchanged. "
        "Line numbers must match **GDB debug info** (often differ from editor); use break main + MI breakpoint line or gdb list. "
        "For symbol stops use gdb_run_command e.g. break v8::Shell::Main."
    ),
    parameters={
        "source_file": {
            "type": "string",
            "description": "Absolute path under V8_PATH, or V8-relative (d8/d8.cc), or DWARF path (../../src/d8/d8.cc)",
            "required": True,
        },
        "line": {"type": "number", "description": "1-based line number in GDB's view of that compilation unit", "required": True},
    },
    parallel=False,
    limit_calls=6,
    execute_function=lambda x: gdb_set_breakpoint(x["source_file"], int(x["line"])),
)

gdb_print_value_tool = IkaTools(
    name="gdb_print_value",
    description=(
        "MI **-data-evaluate-expression**: evaluate a C/C++ expression when the inferior is stopped. "
        "Does not call -exec-run or restart the program."
    ),
    parameters={
        "expression": {"type": "string", "description": "Variable name or C/C++ expression", "required": True},
    },
    parallel=False,
    limit_calls=6,
    execute_function=lambda x: gdb_print_value(x["expression"]),
)

pwndbg_context_tool = IkaTools(
    name="pwndbg_context",
    description=(
        "MI-backed snapshot: stack frames, register values (-data-list-register-values x), and disassembly near $pc. "
        "Safe when stopped; does not restart the debuggee."
    ),
    parameters={"N/A": "N/A"},
    parallel=False,
    limit_calls=3,
    execute_function=lambda _: pwndbg_context(),
)

pwndbg_vmmap_tool = IkaTools(
    name="pwndbg_vmmap",
    description=(
        "Process memory map via MI **-interpreter-exec console** `info proc mappings` (no standard MI vmmap). "
        "Safe when stopped; does not restart the debuggee."
    ),
    parameters={"N/A": "N/A"},
    parallel=False,
    limit_calls=3,
    execute_function=lambda _: pwndbg_vmmap(),
)

pwndbg_regs_tool = IkaTools(
    name="pwndbg_regs",
    description=(
        "MI **-data-list-register-values x**: general-purpose registers when stopped. Does not restart the debuggee."
    ),
    parameters={"N/A": "N/A"},
    parallel=False,
    limit_calls=3,
    execute_function=lambda _: pwndbg_regs(),
)

pwndbg_nearpc_tool = IkaTools(
    name="pwndbg_nearpc",
    description=(
        "MI disassembly from current $pc (-data-evaluate-expression + -data-disassemble). Safe when stopped; "
        "does not restart the debuggee."
    ),
    parameters={
        "count": {"type": "number", "description": "Number of instructions to show (default 10)", "required": False},
    },
    parallel=False,
    limit_calls=4,
    execute_function=lambda x: pwndbg_nearpc(int(x.get("count", 10))),
)
