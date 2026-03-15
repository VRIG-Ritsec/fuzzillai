"""
Debug tools: GDB/MI/pwndbg wrappers as IkaTools.
"""

from IkaCore.tools import IkaTools

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
    name="read_file",
    description="Read file contents for debugging. Returns up to 3000 lines per section. Use section parameter to paginate large files.",
    parameters={
        "file_path": {"type": "string", "description": "Absolute or V8-relative path to the file", "required": True},
        "section": {"type": "number", "description": "Section index for files over 3000 lines (1-based)", "required": False},
    },
    execute_function=lambda x: read_file(x["file_path"], x.get("section")),
)

start_mi_debug_session_tool = IkaTools(
    name="start_mi_debug_session",
    description="Start a GDB MI debug session with d8 loading a JavaScript file. Must be called before mi_run, mi_step, mi_next, or mi_continue.",
    parameters={
        "js_path": {"type": "string", "description": "Path to the JavaScript file to debug", "required": True},
        "d8_args": {"type": "string", "description": "Optional d8 flags (e.g., --allow-natives-syntax)", "required": False},
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
    description="Execute a GDB Machine Interface (MI) command. Use for MI-specific commands (e.g., -exec-next, -stack-list-frames).",
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
    description="Run a GDB or pwndbg command. Use for: info registers, context, vmmap, backtrace, x/10gx, disassemble. Do NOT use for shell commands (pip, apt, cd).",
    parameters={
        "command": {"type": "string", "description": "GDB/pwndbg command", "required": True},
    },
    parallel=False,
    limit_calls=12,
    execute_function=lambda x: gdb_run_command(x["command"]),
)

gdb_set_breakpoint_tool = IkaTools(
    name="gdb_set_breakpoint",
    description="Set a breakpoint at a source file and line. Execution stops when this line is hit.",
    parameters={
        "source_file": {"type": "string", "description": "Path to source file (e.g., runtime/runtime.cc)", "required": True},
        "line": {"type": "number", "description": "Line number where to break", "required": True},
    },
    parallel=False,
    limit_calls=6,
    execute_function=lambda x: gdb_set_breakpoint(x["source_file"], int(x["line"])),
)

gdb_print_value_tool = IkaTools(
    name="gdb_print_value",
    description="Evaluate and print a variable or expression in GDB (e.g., variable name, *ptr, obj->field).",
    parameters={
        "expression": {"type": "string", "description": "Variable name or C/C++ expression", "required": True},
    },
    parallel=False,
    limit_calls=6,
    execute_function=lambda x: gdb_print_value(x["expression"]),
)

pwndbg_context_tool = IkaTools(
    name="pwndbg_context",
    description="Display pwndbg context: registers, stack, backtrace, and disassembly around current instruction.",
    parameters={"N/A": "N/A"},
    parallel=False,
    limit_calls=3,
    execute_function=lambda _: pwndbg_context(),
)

pwndbg_vmmap_tool = IkaTools(
    name="pwndbg_vmmap",
    description="Display memory map (virtual address space layout) from pwndbg.",
    parameters={"N/A": "N/A"},
    parallel=False,
    limit_calls=3,
    execute_function=lambda _: pwndbg_vmmap(),
)

pwndbg_regs_tool = IkaTools(
    name="pwndbg_regs",
    description="Display CPU register values (rax, rsp, rip, etc.) from pwndbg.",
    parameters={"N/A": "N/A"},
    parallel=False,
    limit_calls=3,
    execute_function=lambda _: pwndbg_regs(),
)

pwndbg_nearpc_tool = IkaTools(
    name="pwndbg_nearpc",
    description="Disassemble instructions near the program counter. Shows execution context around current EIP/RIP.",
    parameters={
        "count": {"type": "number", "description": "Number of instructions to show (default 10)", "required": False},
    },
    parallel=False,
    limit_calls=4,
    execute_function=lambda x: pwndbg_nearpc(int(x.get("count", 10))),
)
