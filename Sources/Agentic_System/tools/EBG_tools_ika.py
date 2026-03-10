from IkaCore.tools import IkaTools

from tools.EBG_tools import (
    base64_program_to_js,
    db_query,
    db_list_programs,
    db_get_fuzzer_performance_summary,
    db_list_fuzzers,
    db_get_crash_diversity,
    db_get_mutator_effectiveness,
    db_get_program_grouping,
    db_get_execution_outcome_distribution,
    create_generate_folder,
    write_to_generate_folder,
    read_from_generate_folder,
    list_generate_folder,
    delete_files_from_generate_folder,
    get_program_js_from_hash,
    trace_v8_analysis,
    list_v8_trace_options,
    write_and_execute_js,
    db_store_generated_program,
)
from tools.common_tools import (
    read_file,
    start_mi_debug_session,
    stop_mi_debug_session,
    mi_exec,
    mi_run,
    mi_step,
    mi_next,
    mi_continue,
    gdb_run_command,
    gdb_set_breakpoint,
    gdb_print_value,
    pwndbg_context,
    pwndbg_vmmap,
    pwndbg_regs,
    pwndbg_nearpc,
)

# --- DB tools ---

db_query_tool = IkaTools(
    name="db_query",
    description="Perform an arbitrary SQL query on the fuzzilli database.",
    parameters={
        "query": {"type": "string", "description": "The SQL query to perform", "required": True},
        "params": {"type": "array", "items": {"type": "string"}, "description": "Query parameters", "required": False},
    },
    execute_function=lambda x: db_query(x["query"], x.get("params", [])),
)

db_list_programs_tool = IkaTools(
    name="db_list_programs",
    description="List programs in the database for a specific fuzzer.",
    parameters={
        "limit": {"type": "number", "description": "Max programs", "required": False},
        "offset": {"type": "number", "description": "Offset", "required": False},
        "fuzzer_id": {"type": "number", "description": "Fuzzer ID", "required": False},
        "include_source": {"type": "boolean", "description": "Include base64 program", "required": False},
    },
    execute_function=lambda x: db_list_programs(
        limit=int(x.get("limit", 10)),
        offset=int(x.get("offset", 0)),
        fuzzer_id=x.get("fuzzer_id"),
        include_source=bool(x.get("include_source", False)),
    ),
)

db_get_fuzzer_performance_summary_tool = IkaTools(
    name="db_get_fuzzer_performance_summary",
    description="Get performance summary from fuzzer_dashboard for a fuzzer.",
    parameters={
        "fuzzer_id": {"type": "number", "description": "Fuzzer ID", "required": True},
    },
    execute_function=lambda x: db_get_fuzzer_performance_summary(int(x["fuzzer_id"])),
)

base64_program_to_js_tool = IkaTools(
    name="base64_program_to_js",
    description="Convert base64 program to JavaScript using FuzzILTool.",
    parameters={
        "base64_program": {"type": "string", "description": "Base64 program", "required": True},
    },
    execute_function=lambda x: base64_program_to_js(x["base64_program"]),
)

db_list_fuzzers_tool = IkaTools(
    name="db_list_fuzzers",
    description="List all fuzzers in the database.",
    parameters={"N/A": "N/A"},
    execute_function=lambda _: db_list_fuzzers(),
)

db_get_crash_diversity_tool = IkaTools(
    name="db_get_crash_diversity",
    description="Get crash diversity metrics for a fuzzer.",
    parameters={
        "fuzzer_id": {"type": "number", "description": "Fuzzer ID", "required": True},
    },
    execute_function=lambda x: db_get_crash_diversity(int(x["fuzzer_id"])),
)

db_get_mutator_effectiveness_tool = IkaTools(
    name="db_get_mutator_effectiveness",
    description="Get mutator effectiveness metrics for a fuzzer.",
    parameters={
        "fuzzer_id": {"type": "number", "description": "Fuzzer ID", "required": True},
        "time_window_hours": {"type": "number", "description": "Time window in hours", "required": False},
    },
    execute_function=lambda x: db_get_mutator_effectiveness(
        int(x["fuzzer_id"]), int(x.get("time_window_hours", 1))
    ),
)

db_get_program_grouping_tool = IkaTools(
    name="db_get_program_grouping",
    description="Group programs by size for a fuzzer.",
    parameters={
        "fuzzer_id": {"type": "number", "description": "Fuzzer ID", "required": True},
        "time_window_hours": {"type": "number", "description": "Time window in hours", "required": False},
        "size_tolerance_bytes": {"type": "number", "description": "Size tolerance in bytes", "required": False},
    },
    execute_function=lambda x: db_get_program_grouping(
        int(x["fuzzer_id"]),
        int(x.get("time_window_hours", 1)),
        int(x.get("size_tolerance_bytes", 50)),
    ),
)

db_get_execution_outcome_distribution_tool = IkaTools(
    name="db_get_execution_outcome_distribution",
    description="Get execution outcome distribution for a fuzzer.",
    parameters={
        "fuzzer_id": {"type": "number", "description": "Fuzzer ID", "required": True},
        "time_window_hours": {"type": "number", "description": "Time window in hours", "required": False},
        "sample_interval_minutes": {"type": "number", "description": "Sample interval in minutes", "required": False},
    },
    execute_function=lambda x: db_get_execution_outcome_distribution(
        int(x["fuzzer_id"]),
        int(x.get("time_window_hours", 1)),
        int(x.get("sample_interval_minutes", 5)),
    ),
)

# --- Generate folder tools ---

create_generate_folder_tool = IkaTools(
    name="create_generate_folder",
    description="Create the variant analysis folder for storing temporary analysis files.",
    parameters={"N/A": "N/A"},
    execute_function=lambda x: create_generate_folder(),
)

write_to_generate_folder_tool = IkaTools(
    name="write_to_generate_folder",
    description="Write a file in the variant analysis folder. Use this to store: (1) crash programs fetched from database, (2) generated program variants, (3) analysis results. Typical workflow: fetch program with get_program_js_from_hash(), then write it here.",
    parameters={
        "file_name": {"type": "string", "description": "File name (e.g., 'crash_original.js', 'variant_1.js')", "required": True},
        "content": {"type": "string", "description": "File content (JavaScript code or analysis results)", "required": True},
    },
    execute_function=lambda x: write_to_generate_folder(x["file_name"], x["content"]),
)

read_from_generate_folder_tool = IkaTools(
    name="read_from_generate_folder",
    description="Read a file from the variant analysis folder. NOTE: Programs from the database should be retrieved using get_program_js_from_hash() instead. Always call list_generate_folder() first to verify the file exists.",
    parameters={
        "file_name": {"type": "string", "description": "File name", "required": True},
    },
    execute_function=lambda x: read_from_generate_folder(x["file_name"]),
)

list_generate_folder_tool = IkaTools(
    name="list_generate_folder",
    description="List files in the variant analysis folder. Always call this before attempting to read files to verify they exist.",
    parameters={"N/A": "N/A"},
    execute_function=lambda x: list_generate_folder(),
)

delete_files_from_generate_folder_tool = IkaTools(
    name="delete_files_from_generate_folder",
    description="Delete a file from the variant analysis folder. Use this to clean up after analysis: (1) delete variants that don't lead to crashes, (2) delete files no longer needed.",
    parameters={
        "file_name": {"type": "string", "description": "File name", "required": True},
    },
    execute_function=lambda x: delete_files_from_generate_folder(x["file_name"]),
)

# --- Program/tools ---

get_program_js_from_hash_tool = IkaTools(
    name="get_program_js_from_hash",
    description="Fetch a crash program from database and convert to JavaScript. WORKFLOW: (1) Use this to get the JS code, (2) Write it to variant analysis folder with write_to_generate_folder(), (3) Create and write variants to the same folder, (4) Read/analyze variants with read_from_generate_folder().",
    parameters={
        "program_hash": {"type": "string", "description": "Program hash from database", "required": True},
    },
    execute_function=lambda x: get_program_js_from_hash(x["program_hash"]),
)

trace_v8_analysis_tool = IkaTools(
    name="trace_v8_analysis",
    description="Run V8 tracing on a program hash with presets/flags.",
    parameters={
        "program_hash": {"type": "string", "description": "Program hash", "required": True},
        "presets": {"type": "array", "items": {"type": "string"}, "description": "Preset names", "required": False},
        "custom_flags": {"type": "array", "items": {"type": "string"}, "description": "Custom flags", "required": False},
        "function_filter": {"type": "string", "description": "Function filter", "required": False},
        "turbo_path": {"type": "string", "description": "Turbo path", "required": False},
        "timeout_seconds": {"type": "number", "description": "Timeout seconds", "required": False},
    },
    execute_function=lambda x: trace_v8_analysis(
        program_hash=x["program_hash"],
        presets=x.get("presets"),
        custom_flags=x.get("custom_flags"),
        function_filter=x.get("function_filter"),
        turbo_path=x.get("turbo_path", "/tmp/turbofan_ir"),
        timeout_seconds=int(x.get("timeout_seconds", 60)),
    ),
)

list_v8_trace_options_tool = IkaTools(
    name="list_v8_trace_options",
    description="List available V8 trace presets and flags.",
    parameters={"N/A": "N/A"},
    execute_function=lambda _: list_v8_trace_options(),
)

write_and_execute_js_tool = IkaTools(
    name="write_and_execute_js",
    description="Write JS to a file and execute with d8.",
    parameters={
        "js_code": {"type": "string", "description": "JavaScript code", "required": True},
        "file_name": {"type": "string", "description": "Optional filename", "required": False},
        "d8_flags": {"type": "string", "description": "D8 flags", "required": False},
    },
    execute_function=lambda x: write_and_execute_js(
        js_code=x["js_code"],
        file_name=x.get("file_name"),
        d8_flags=x.get("d8_flags", ""),
    ),
)

db_store_generated_program_tool = IkaTools(
    name="db_store_generated_program",
    description="Store a generated JS program in the database.",
    parameters={
        "js_program": {"type": "string", "description": "JavaScript program", "required": True},
        "fuzzer_id": {"type": "number", "description": "Fuzzer ID", "required": True},
    },
    execute_function=lambda x: db_store_generated_program(x["js_program"], int(x["fuzzer_id"])),
)

# --- Debugger/MI/GDB/Pwndbg tools ---

read_file_tool = IkaTools(
    name="read_file",
    description="Read file content (optionally a section).",
    parameters={
        "file_path": {"type": "string", "description": "File path", "required": True},
        "section": {"type": "number", "description": "Optional section", "required": False},
    },
    execute_function=lambda x: read_file(x["file_path"], x.get("section")),
)

start_mi_debug_session_tool = IkaTools(
    name="start_mi_debug_session",
    description="Start MI debug session for a JS path.",
    parameters={
        "js_path": {"type": "string", "description": "JS file path", "required": True},
        "d8_args": {"type": "string", "description": "D8 args", "required": False},
    },
    parallel=False,
    limit_calls=2,
    execute_function=lambda x: start_mi_debug_session(x["js_path"], x.get("d8_args", "")),
)

stop_mi_debug_session_tool = IkaTools(
    name="stop_mi_debug_session",
    description="Stop MI debug session.",
    parameters={"N/A": "N/A"},
    parallel=False,
    execute_function=lambda _: stop_mi_debug_session(),
)

mi_exec_tool = IkaTools(
    name="mi_exec",
    description="Execute a MI command.",
    parameters={
        "command": {"type": "string", "description": "MI command", "required": True},
    },
    parallel=False,
    limit_calls=12,
    execute_function=lambda x: mi_exec(x["command"]),
)

mi_run_tool = IkaTools(
    name="mi_run",
    description="Run MI session.",
    parameters={"N/A": "N/A"},
    parallel=False,
    limit_calls=2,
    execute_function=lambda _: mi_run(),
)

mi_continue_tool = IkaTools(
    name="mi_continue",
    description="Continue MI session.",
    parameters={"N/A": "N/A"},
    parallel=False,
    limit_calls=2,
    execute_function=lambda _: mi_continue(),
)

mi_next_tool = IkaTools(
    name="mi_next",
    description="MI next.",
    parameters={"N/A": "N/A"},
    parallel=False,
    limit_calls=8,
    execute_function=lambda _: mi_next(),
)

mi_step_tool = IkaTools(
    name="mi_step",
    description="MI step.",
    parameters={"N/A": "N/A"},
    parallel=False,
    limit_calls=8,
    execute_function=lambda _: mi_step(),
)

gdb_run_command_tool = IkaTools(
    name="gdb_run_command",
    description="Run a GDB/pwndbg debugging command (e.g., 'info registers', 'context', 'vmmap', 'backtrace'). DO NOT use for shell commands like pip, apt, or cd.",
    parameters={
        "command": {"type": "string", "description": "GDB command", "required": True},
    },
    parallel=False,
    limit_calls=12,
    execute_function=lambda x: gdb_run_command(x["command"]),
)

gdb_set_breakpoint_tool = IkaTools(
    name="gdb_set_breakpoint",
    description="Set a GDB breakpoint by file and line.",
    parameters={
        "source_file": {"type": "string", "description": "Source file", "required": True},
        "line": {"type": "number", "description": "Line number", "required": True},
    },
    parallel=False,
    limit_calls=6,
    execute_function=lambda x: gdb_set_breakpoint(x["source_file"], int(x["line"])),
)

gdb_print_value_tool = IkaTools(
    name="gdb_print_value",
    description="Print a value/expression in GDB.",
    parameters={
        "expression": {"type": "string", "description": "Expression", "required": True},
    },
    parallel=False,
    limit_calls=6,
    execute_function=lambda x: gdb_print_value(x["expression"]),
)

pwndbg_context_tool = IkaTools(
    name="pwndbg_context",
    description="Pwndbg context.",
    parameters={"N/A": "N/A"},
    parallel=False,
    limit_calls=3,
    execute_function=lambda _: pwndbg_context(),
)

pwndbg_vmmap_tool = IkaTools(
    name="pwndbg_vmmap",
    description="Pwndbg vmmap.",
    parameters={"N/A": "N/A"},
    parallel=False,
    limit_calls=3,
    execute_function=lambda _: pwndbg_vmmap(),
)

pwndbg_regs_tool = IkaTools(
    name="pwndbg_regs",
    description="Pwndbg regs.",
    parameters={"N/A": "N/A"},
    parallel=False,
    limit_calls=3,
    execute_function=lambda _: pwndbg_regs(),
)

pwndbg_nearpc_tool = IkaTools(
    name="pwndbg_nearpc",
    description="Pwndbg nearpc.",
    parameters={
        "count": {"type": "number", "description": "Instruction count", "required": False},
    },
    parallel=False,
    limit_calls=4,
    execute_function=lambda x: pwndbg_nearpc(int(x.get("count", 10))),
)
