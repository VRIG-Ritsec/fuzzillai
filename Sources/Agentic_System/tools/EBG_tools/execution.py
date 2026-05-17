"""
Execution tools: trace_v8_analysis, list_v8_trace_options, write_and_execute_js,
and crash-flag minimization helpers.
"""

import os
import json
import math
import re
from pathlib import Path

from IkaCore.tools import IkaTools

from .db import fetch_program_js_from_db
from ._shared import _RUNTIME_DATA_DIR, _get_varianal_folder, json_serial
from tools._shared import run_d8

V8_TRACE_PRESETS = {
    "tiering": [
        "--trace-opt",
        "--trace-opt-status",
        "--trace-deopt",
        "--trace-osr",
        "--trace-file-names"
    ],
    "tiering_verbose": [
        "--trace-opt-verbose",
        "--trace-opt-status",
        "--trace-deopt-verbose",
        "--trace-osr",
        "--trace-file-names"
    ],
    "turbofan": [
        "--trace-turbo-graph",
        "--trace-turbo-types"
    ],
    "turbofan_full": [
        "--trace-turbo",
        "--trace-turbo-graph",
        "--trace-turbo-scheduled",
        "--trace-turbo-types",
        "--trace-turbo-reduction",
        "--trace-turbo-inlining",
        "--trace-turbo-alloc"
    ],
    "maglev": [
        "--trace-maglev-graph-building",
        "--maglev-print-feedback",
        "--maglev-print-provenance"
    ],
    "maglev_full": [
        "--trace-maglev-graph-building",
        "--trace-maglev-inlining",
        "--trace-maglev-regalloc",
        "--print-maglev-graph",
        "--print-maglev-code",
        "--maglev-print-bytecode",
        "--maglev-print-feedback",
        "--maglev-print-inlined",
        "--maglev-print-provenance"
    ],
    "ignition": [
        "--print-bytecode"
    ],
    "ignition_full": [
        "--print-bytecode",
        "--trace-ignition-codegen"
    ],
    "gc": [
        "--trace-gc",
        "--trace-gc-nvp",
        "--trace-incremental-marking"
    ],
    "gc_full": [
        "--trace-gc",
        "--trace-gc-nvp",
        "--trace-gc-verbose",
        "--trace-gc-freelists",
        "--trace-gc-heap-layout",
        "--trace-incremental-marking",
        "--trace-concurrent-marking",
        "--trace-fragmentation"
    ],
    "ic_maps": [
        "--log-ic",
        "--log-maps",
        "--trace-generalization",
        "--trace-prototype-users"
    ],
    "ic_maps_full": [
        "--log-ic",
        "--log-maps",
        "--log-maps-details",
        "--trace-generalization",
        "--trace-prototype-users"
    ],
    "wasm": [
        "--trace-wasm",
        "--trace-wasm-decoder",
        "--trace-wasm-compiler",
        "--trace-liftoff"
    ],
    "regexp": [
        "--trace-regexp-bytecodes",
        "--trace-regexp-parser",
        "--trace-regexp-tier-up"
    ],
    "serialization": [
        "--trace-serializer",
        "--trace-deserialization",
        "--profile-deserialization"
    ]
}

V8_AVAILABLE_FLAGS = [
    "--trace-opt", "--trace-opt-verbose", "--trace-opt-status",
    "--trace-deopt", "--trace-deopt-verbose",
    "--trace-osr", "--trace-file-names",
    "--trace-turbo", "--trace-turbo-graph", "--trace-turbo-scheduled",
    "--trace-turbo-types", "--trace-turbo-reduction", "--trace-turbo-inlining",
    "--trace-turbo-alloc",
    "--trace-maglev-graph-building", "--trace-maglev-inlining", "--trace-maglev-regalloc",
    "--print-maglev-graph", "--print-maglev-graphs", "--print-maglev-code",
    "--maglev-print-bytecode", "--maglev-print-feedback", "--maglev-print-inlined",
    "--maglev-print-provenance",
    "--print-bytecode", "--trace-ignition-codegen",
    "--trace-gc", "--trace-gc-nvp", "--trace-gc-verbose",
    "--trace-gc-freelists", "--trace-gc-freelists-verbose", "--trace-gc-heap-layout",
    "--trace-incremental-marking", "--trace-concurrent-marking",
    "--trace-fragmentation", "--trace-fragmentation-verbose",
    "--log-ic", "--log-maps", "--log-maps-details",
    "--trace-generalization", "--trace-prototype-users",
    "--trace-wasm", "--trace-wasm-decoder", "--trace-wasm-compiler", "--trace-liftoff",
    "--trace-regexp-bytecodes", "--trace-regexp-parser", "--trace-regexp-tier-up",
    "--trace-serializer", "--trace-deserialization", "--profile-deserialization"
]

DEFAULT_CRASH_TRIAGE_FLAGS = [
    "--expose-gc",
    "--expose-externalize-string",
    "--omit-quit",
    "--allow-natives-syntax",
    "--fuzzing",
    "--jit-fuzzing",
    "--future",
    "--harmony",
    "--experimental-fuzzing",
    "--js-staging",
    "--wasm-staging",
    "--wasm-fast-api",
    "--expose-fast-api",
    "--wasm-test-streaming",
]

DEFAULT_CRASH_SIGNATURES = [
    "Bytecode mismatch",
]

DEFAULT_CRASH_RETURNCODES = [134, 139, 6, -6, -11]


def _normalize_runtime_output_dir(requested_path: str | None, default_folder: str) -> str:
    runtime_root = _RUNTIME_DATA_DIR.resolve()
    default_dir = Path(default_folder).resolve()

    if requested_path:
        raw_path = Path(requested_path).expanduser()
        if raw_path.is_absolute():
            target = default_dir / raw_path.name
        else:
            target = default_dir / raw_path
    else:
        target = default_dir / "turbofan_ir"

    target = target.resolve()
    try:
        target.relative_to(runtime_root)
    except ValueError:
        target = default_dir / target.name

    target.mkdir(parents=True, exist_ok=True)
    return str(target)


def trace_v8_analysis(
    program_hash: str,
    presets: list = None,
    custom_flags: list = None,
    function_filter: str = None,
    turbo_path: str = "",
    timeout_seconds: int = 60
) -> str:
    if presets is not None and len(presets) == 0:
        presets = None
    if custom_flags is not None and len(custom_flags) == 0:
        custom_flags = None
    if function_filter == "":
        function_filter = None
    js_code = fetch_program_js_from_db(program_hash)
    if js_code is None:
        return json.dumps({"error": f"Program with hash {program_hash} not found in database"})
    if js_code.startswith("Error"):
        return json.dumps({"error": js_code})

    artifact_dir = str(Path(_get_varianal_folder()).resolve())
    os.makedirs(artifact_dir, exist_ok=True)
    filepath_js = str((Path(artifact_dir) / f"{program_hash}.js").resolve())
    turbo_path = _normalize_runtime_output_dir(turbo_path, artifact_dir)
    with open(filepath_js, "w") as f:
        f.write(js_code)

    if presets is None and custom_flags is None:
        presets = ["tiering", "maglev", "ignition"]

    flags = ["--allow-natives-syntax"]

    if presets:
        for preset in presets:
            if preset in V8_TRACE_PRESETS:
                flags.extend(V8_TRACE_PRESETS[preset])
            else:
                return json.dumps({"error": f"Unknown preset: {preset}", "available_presets": list(V8_TRACE_PRESETS.keys())})

    if custom_flags:
        for flag in custom_flags:
            if flag.startswith("--"):
                flags.append(flag)
            else:
                flags.append(f"--{flag}")

    if function_filter:
        filter_flags = [
            f"--trace-turbo-filter={function_filter}",
            f"--maglev-filter={function_filter}",
            f"--maglev-print-filter={function_filter}",
            f"--print-bytecode-filter={function_filter}"
        ]
        flags.extend(filter_flags)

    flags.append(f"--trace-turbo-path={turbo_path}")

    flags = list(dict.fromkeys(flags))

    try:
        result = run_d8(filepath_js, flags=flags, timeout=timeout_seconds)

        output_data = {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "return_code": result.returncode,
            "flags_used": flags,
            "presets_applied": presets or [],
            "custom_flags_applied": custom_flags or [],
            "function_filter": function_filter,
            "program_hash": program_hash,
            "js_file": filepath_js
        }
        if result.returncode == 127 and result.stderr.startswith("Error:"):
            output_data["infrastructure_error"] = result.stderr

        if turbo_path and os.path.isdir(turbo_path):
            turbo_files = os.listdir(turbo_path)
            output_data["turbo_ir_files"] = turbo_files
            if turbo_files:
                output_data["turbo_ir_contents"] = {}
                for tf in turbo_files[:5]:
                    tf_path = os.path.join(turbo_path, tf)
                    if os.path.isfile(tf_path):
                        with open(tf_path, "r") as f:
                            content = f.read()
                            if len(content) > 50000:
                                content = content[:50000] + "\n... [truncated]"
                            output_data["turbo_ir_contents"][tf] = content

        return json.dumps(output_data, default=json_serial)

    except Exception as e:
        return json.dumps({"error": f"Execution error: {e}", "flags_used": flags})
    finally:
        if os.path.exists(filepath_js):
            os.remove(filepath_js)


def list_v8_trace_options() -> str:
    return json.dumps({
        "presets": V8_TRACE_PRESETS,
        "available_individual_flags": V8_AVAILABLE_FLAGS,
        "usage_examples": {
            "tiering_deopt_analysis": {
                "presets": ["tiering"],
                "description": "Track tier-ups, optimization status, and deopts"
            },
            "turbofan_deep_dive": {
                "presets": ["turbofan"],
                "custom_flags": ["--trace-turbo-reduction"],
                "function_filter": "target*",
                "description": "Analyze TurboFan IR for specific functions"
            },
            "maglev_analysis": {
                "presets": ["maglev"],
                "description": "Modern tier graph building and feedback analysis"
            },
            "bytecode_inspection": {
                "presets": ["ignition"],
                "function_filter": "f*",
                "description": "View Ignition bytecode for specific functions"
            },
            "gc_investigation": {
                "presets": ["gc"],
                "description": "Track GC events and memory behavior"
            },
            "hidden_class_churn": {
                "presets": ["ic_maps"],
                "description": "Track inline caches and map transitions"
            },
            "full_jit_analysis": {
                "presets": ["tiering", "maglev", "turbofan"],
                "description": "Comprehensive JIT pipeline visibility"
            }
        }
    }, indent=2)


def write_and_execute_js(js_code: str, file_name: str = None, d8_flags: str = "") -> str:
    import uuid

    if not js_code or not js_code.strip():
        return json.dumps({"error": "js_code is required and cannot be empty"}, indent=2)

    if file_name is None:
        file_name = f"generated_{uuid.uuid4().hex[:8]}.js"

    if not file_name.endswith(".js"):
        file_name += ".js"

    folder = _get_varianal_folder()
    os.makedirs(folder, exist_ok=True)
    file_path = os.path.join(folder, file_name)

    try:
        with open(file_path, "w") as f:
            f.write(js_code)

        abs_path = os.path.abspath(file_path)
        if "--allow-natives-syntax" not in d8_flags:
            d8_flags += " --allow-natives-syntax"
        d8_flags = d8_flags.strip()
        flags = d8_flags.split() if d8_flags else []
        result = run_d8(abs_path, flags=flags)
        execution_result = f"Program execution result:\n{result.stderr}\n{result.stdout}"
        payload = {
            "file_path": abs_path,
            "file_name": file_name,
            "execution_result": execution_result,
        }
        if result.returncode == 127 and result.stderr.startswith("Error:"):
            payload["error"] = result.stderr
        return json.dumps(payload, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Failed to write or execute JS: {str(e)}"}, indent=2)


def _matches_crash_signature(
    result,
    crash_signatures: list[str] | None = None,
    expected_return_codes: list[int] | None = None,
) -> bool:
    output = f"{result.stdout}\n{result.stderr}"
    if crash_signatures:
        for pattern in crash_signatures:
            if not pattern:
                continue
            try:
                if re.search(pattern, output, re.MULTILINE):
                    return True
            except re.error:
                if pattern in output:
                    return True
    return result.returncode in set(expected_return_codes or [])


def _serialize_d8_result(result, flags: list[str]) -> dict:
    return {
        "flags_used": list(flags),
        "return_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def minimize_crash_flags(
    js_path: str,
    candidate_flags: list[str] | None = None,
    crash_signatures: list[str] | None = None,
    expected_return_codes: list[int] | None = None,
    timeout_seconds: int = 60,
) -> str:
    flags = list(dict.fromkeys(candidate_flags or DEFAULT_CRASH_TRIAGE_FLAGS))
    crash_signatures = crash_signatures or DEFAULT_CRASH_SIGNATURES
    expected_return_codes = expected_return_codes or DEFAULT_CRASH_RETURNCODES

    baseline = run_d8(js_path, flags=flags, timeout=timeout_seconds)
    baseline_reproduces = _matches_crash_signature(
        baseline,
        crash_signatures=crash_signatures,
        expected_return_codes=expected_return_codes,
    )

    payload = {
        "js_path": str(Path(js_path).expanduser()),
        "candidate_flags": flags,
        "crash_signatures": crash_signatures,
        "expected_return_codes": expected_return_codes,
        "baseline_reproduces": baseline_reproduces,
        "baseline_result": _serialize_d8_result(baseline, flags),
    }

    if not baseline_reproduces:
        payload["error"] = "Crash did not reproduce with the full candidate flag set"
        payload["minimal_flags"] = []
        payload["attempt_log"] = []
        return json.dumps(payload, default=json_serial)

    current = list(flags)
    attempt_log: list[dict] = []
    granularity = 2

    while current:
        subset_size = max(1, math.ceil(len(current) / granularity))
        removed_chunk = False

        for start in range(0, len(current), subset_size):
            complement = current[:start] + current[start + subset_size :]
            result = run_d8(js_path, flags=complement, timeout=timeout_seconds)
            reproduces = _matches_crash_signature(
                result,
                crash_signatures=crash_signatures,
                expected_return_codes=expected_return_codes,
            )
            attempt_log.append(
                {
                    "dropped_flags": current[start : start + subset_size],
                    "trial_flags": complement,
                    "return_code": result.returncode,
                    "reproduces": reproduces,
                }
            )
            if reproduces:
                current = complement
                granularity = max(2, granularity - 1)
                removed_chunk = True
                break

        if removed_chunk:
            continue
        if granularity >= len(current):
            break
        granularity = min(len(current), granularity * 2)

    final_result = run_d8(js_path, flags=current, timeout=timeout_seconds)
    payload["minimal_flags"] = current
    payload["dropped_flags"] = [flag for flag in flags if flag not in current]
    payload["attempt_log"] = attempt_log
    payload["final_reproduces"] = _matches_crash_signature(
        final_result,
        crash_signatures=crash_signatures,
        expected_return_codes=expected_return_codes,
    )
    payload["final_result"] = _serialize_d8_result(final_result, current)
    return json.dumps(payload, default=json_serial)


trace_v8_analysis_tool = IkaTools(
    name="trace_v8_analysis",
    description="Run V8 tracing on a program by hash. Fetches program from DB, converts to JS, runs d8 with trace presets (tiering, turbofan, maglev, ignition, gc, ic_maps, etc.) or custom flags. Use list_v8_trace_options first to see presets.",
    parameters={
        "program_hash": {"type": "string", "description": "Program hash from database (e.g. from db_list_programs)", "required": True},
        "presets": {"type": "array", "items": {"type": "string"}, "description": "Preset names such as tiering, maglev, turbofan", "required": False},
        "custom_flags": {"type": "array", "items": {"type": "string"}, "description": "Additional d8 trace flags", "required": False},
        "function_filter": {"type": "string", "description": "Filter trace output to specific function names", "required": False},
        "turbo_path": {"type": "string", "description": "Directory name under runtime_data for TurboFan IR dumps (defaults to the current variant-analysis folder)", "required": False},
        "timeout_seconds": {"type": "number", "description": "Max execution time in seconds (default 60)", "required": False},
    },
    execute_function=lambda x: trace_v8_analysis(
        program_hash=x["program_hash"],
        presets=x.get("presets"),
        custom_flags=x.get("custom_flags"),
        function_filter=x.get("function_filter"),
        turbo_path=x.get("turbo_path", ""),
        timeout_seconds=int(x.get("timeout_seconds", 60)),
    ),
)

list_v8_trace_options_tool = IkaTools(
    name="list_v8_trace_options",
    description="List all V8 trace presets, individual flags, and usage examples. Call before trace_v8_analysis to choose the right presets.",
    parameters={"N/A": "N/A"},
    execute_function=lambda _: list_v8_trace_options(),
)

write_and_execute_js_tool = IkaTools(
    name="write_and_execute_js",
    description="Write JavaScript to the variant analysis folder and run it with d8. Use for testing crash variants or snippets. File is stored in the generate folder.",
    parameters={
        "js_code": {"type": "string", "description": "JavaScript code to execute", "required": True},
        "file_name": {"type": "string", "description": "Filename in generate folder (auto-generated if omitted)", "required": False},
        "d8_flags": {"type": "string", "description": "d8 flags (e.g. --trace-opt --print-bytecode)", "required": False},
    },
    execute_function=lambda x: write_and_execute_js(
        js_code=x["js_code"],
        file_name=x.get("file_name"),
        d8_flags=x.get("d8_flags", ""),
    ),
)

minimize_crash_flags_tool = IkaTools(
    name="minimize_crash_flags",
    description=(
        "Run a delta-debugging / binary-search-style flag minimization pass against a crash JS file "
        "to find the smallest reproducing d8 flag subset. Use this during initial crash triage."
    ),
    parameters={
        "js_path": {"type": "string", "description": "Path to the crash JS file to execute", "required": True},
        "candidate_flags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Candidate d8 flags to minimize. Defaults to the standard Fuzzilli crash-triage flag set.",
            "required": False,
        },
        "crash_signatures": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Regex or substring signatures that count as reproducing the crash, e.g. Bytecode mismatch.",
            "required": False,
        },
        "expected_return_codes": {
            "type": "array",
            "items": {"type": "number"},
            "description": "Return codes that also count as a crash if signatures are absent.",
            "required": False,
        },
        "timeout_seconds": {"type": "number", "description": "Per-run timeout in seconds", "required": False},
    },
    execute_function=lambda x: minimize_crash_flags(
        js_path=x["js_path"],
        candidate_flags=x.get("candidate_flags"),
        crash_signatures=x.get("crash_signatures"),
        expected_return_codes=x.get("expected_return_codes"),
        timeout_seconds=int(x.get("timeout_seconds", 60)),
    ),
)
