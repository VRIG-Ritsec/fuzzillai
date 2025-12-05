from smolagents import tool
from tools.common_tools import *
from tools.FoG_tools import *
from pathlib import Path
from fuzzywuzzy import fuzz
from functools import wraps
from decimal import Decimal

import psycopg2
import psycopg2.extras
import datetime
import base64

# Environment variables (optional, for remote PostgreSQL):
#   - POSTGRES_HOST: Remote PostgreSQL host/IP (if set, connects to remote instead of local container)
#   - POSTGRES_PORT: PostgreSQL port (default: 5432)
#   - POSTGRES_DB: Database name (default: fuzzilli_master)
#   - POSTGRES_USER: Database user (default: fuzzilli)
#   - POSTGRES_PASSWORD: PostgreSQL password (default: fuzzilli123)

if not os.getenv('POSTGRES_HOST'):
    print("POSTGRES_HOST environment variable not set. Do export POSTGRES_HOST='remote PostgresSQL host/IP or localhost'")
    print("     Example: export POSTGRES_HOST=localhost")
    sys.exit(0)
if not os.getenv('POSTGRES_PORT'):
    print("Using default POSTGRES_PORT => 5432")
if not os.getenv('POSTGRES_DB'):
    print("Using default POSTGRES_DB => 'fuzzilli_master'")
if not os.getenv('POSTGRES_USER'):
    print("Using default POSTGRES_USER => 'fuzzilli'")
if not os.getenv('POSTGRES_PASSWORD'):
    print("Using default POSTGRES_PASSWORD => 'fuzzilli123'")
if not os.getenv('DB_CONTAINER '):
    print("Using default DB_CONTAINER => fuzzilli-postgres-master")
POSTGRES_HOST = os.getenv('POSTGRES_HOST')
POSTGRES_PORT = 5432 if not os.getenv('POSTGRES_PORT') else os.getenv('POSTGRES_PORT')
POSTGRES_DB = 'fuzzilli_master' if not os.getenv('POSTGRES_DB') else os.getenv('POSTGRES_DB')
POSTGRES_USER = 'fuzzilli' if not os.getenv('POSTGRES_USER') else os.getenv('POSTGRES_USER')
POSTGRES_PASSWORD = 'fuzzilli123' if not os.getenv('POSTGRES_PASSWORD') else os.getenv('POSTGRES_PASSWORD')
DB_CONTAINER = 'fuzzilli-postgres-master' if not os.getenv('DB_CONTAIER') else os.getenv('DB_CONTAINER')

TEMP_FUZZIL_PATH = "/tmp/temp-base64-fuzzil.fzil"

def json_serial(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")



@tool
def db_query(query: str, params: list = []) -> str:
    """
    Perform and arbitrary user specified query

    Args:
        query (str): The SQL query to perform.
        params (list): The parameters to pass to the query.

    Returns:
        str: A JSON string containing the query results.
    """ 
    conn = None
    try:
        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            dbname=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD
        )
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute(query, params)
        rows = cursor.fetchall()
        result_json = json.dumps(rows, default=json_serial, indent=2)
        return result_json
    except psycopg2.Error as e:
        return f"Database error: {e}"
    except Exception as e:
        return f"Unexpected error: {e}"
    finally:
        if conn:
            conn.close()

@tool
def db_list_programs(limit: int = 10, offset: int = 0, fuzzer_id: int = None, include_source: bool = False) -> str:
    """
    List programs in the database for a specific fuzzer either including the base64 program or not

    Args:
        limit (int): The maximum number of programs to return.
        offset (int): The offset to start from.
        fuzzer_id (int): The ID of the fuzzer to list programs for.
        include_source (bool): Whether to include the source code of the programs.

    Returns:
        str: A JSON string containing the list of programs.
    """
    conn = None
    try:
        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            dbname=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD
        )
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute("SELECT program_hash, fuzzer_id, inserted_at FROM fuzzer WHERE fuzzer_id = %s LIMIT %s OFFSET %s", (fuzzer_id, limit, offset))
        rows = cursor.fetchall()
        if include_source:
            cursor.execute("SELECT program_hash, fuzzer_id, inserted_at, program_base64 FROM fuzzer WHERE fuzzer_id = %s LIMIT %s OFFSET %s", (fuzzer_id, limit, offset))
            rows = cursor.fetchall()
        result_json = json.dumps(rows, default=json_serial, indent=2)
        return result_json
    except psycopg2.Error as e:
        return f"Database error: {e}"
    except Exception as e:
        return f"Unexpected error: {e}"
    finally:
        if conn:
            conn.close()

        
@tool
def db_get_fuzzer_performance_summary(fuzzer_id: int) -> str:
    """
    Get performance from fuzzer_dashboard materialized view using index idx_fuzzer_dashboard_id
    
    Args:
        fuzzer_id (int): The ID of the fuzzer to get performance for.
    
    Returns:
        str: A JSON string containing the performance data.
    """
    conn = None
    try:
        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            dbname=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD
        )
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        cursor.execute("SELECT * FROM fuzzer_dashboard WHERE fuzzer_id = %s", (fuzzer_id,))
        rows = cursor.fetchall()
        result_json = json.dumps(rows, default=json_serial, indent=2)
        return result_json
    except psycopg2.Error as e:
        return f"Database error: {e}"
    except Exception as e:
        return f"Unexpected error: {e}"
    finally:
        if conn:
            conn.close()
    


@tool
def base64_program_to_js(base64_program: str) -> str:
    """
    Converts a base64 string using base64 decode -> FZIL Tool and returns the JS code

    Args:
        base64_program (str): The base64 string to convert.
    
    Returns:
        str: The JS code.
    """
    try:
        decoded_program = base64.b64decode(base64_program)
    except Exception as e:
        return json.dumps(f"Error decoding base64: {e}")

    with open(TEMP_FUZZIL_PATH, "wb") as f:
        f.write(decoded_program)

    cmd = f"{FUZZILLI_TOOL_BIN} --liftToJS {TEMP_FUZZIL_PATH}"
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        output = get_output(result)
        return json.dumps(output)
    except Exception as e:
        return json.dumps(f"Error running FuzzILTool: {e}")
    


@tool
def db_list_fuzzers() -> str:
    """
    List all fuzzers in the database
    
    Returns:
        str: A JSON string containing the list of fuzzers.
    """
    conn = None
    try:
        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            dbname=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD
        )
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute("SELECT * FROM main")
        rows = cursor.fetchall()
        result_json = json.dumps(rows, default=json_serial, indent=2)
        return result_json
    except psycopg2.Error as e:
        return f"Database error: {e}"
    except Exception as e:
        return f"Unexpected error: {e}"
    finally:
        if conn:
            conn.close()


@tool
def db_get_crash_diversity(fuzzer_id: int) -> str:
    """
    Use crash_analysis materialized view to get crash diversity for a specific fuzzer

    Args:
        fuzzer_id (int): The ID of the fuzzer to get crash diversity for.
    
    Returns:
        str: A JSON string containing the crash diversity data.
    """
    conn = None
    try:
        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            dbname=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD
        )
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute("SELECT * FROM crash_analysis WHERE fuzzer_id = %s", (fuzzer_id,))
        rows = cursor.fetchall()
        result_json = json.dumps(rows, default=json_serial, indent=2)
        return result_json
    except psycopg2.Error as e:
        return f"Database error: {e}"
    except Exception as e:
        return f"Unexpected error: {e}"
    finally:
        if conn:
            conn.close()
   

@tool
def db_get_mutator_effectiveness(fuzzer_id: int, time_window_hours: int = 1) -> str:
    """
    Use database materialized view for mutator effectiveness, mutator_effectiveness_per_fuzzer limited to time_window_hours

    Args:
        fuzzer_id (int): The ID of the fuzzer to get mutator effectiveness for.
        time_window_hours (int): The time window in hours to limit the mutator effectiveness to.
    
    Returns:
        str: A JSON string containing the mutator effectiveness data.
    """
    conn = None
    try:
        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            dbname=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD
        )
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        # Filter by fuzzer_id and time window using last_updated
        cursor.execute("""
            SELECT * FROM mutator_effectiveness_per_fuzzer 
            WHERE fuzzer_id = %s 
            AND last_updated > NOW() - INTERVAL '%s hours'
        """, (fuzzer_id, time_window_hours))
        
        rows = cursor.fetchall()
        result_json = json.dumps(rows, default=json_serial, indent=2)
        return result_json
        
    except psycopg2.Error as e:
        return f"Database error: {e}"
    except Exception as e:
        return f"Unexpected error: {e}"
    finally:
        if conn:
            conn.close()

@tool
def db_get_program_grouping(fuzzer_id: int, time_window_hours: int = 1, size_tolerance_bytes: int = 50) -> str:
    """
    Analyzes program convergence patterns by grouping similar-sized programs and their outcomes.
    Uses the program_convergence materialized view.
    
    Args:
        fuzzer_id: The fuzzer instance to analyze
        time_window_hours: How far back to look (default 1 hours)
        size_tolerance_bytes: Group programs within this size range together (default 50 bytes)

    Returns:
        str: A JSON string containing the program convergence data.
    """
    conn = None
    try:
        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            dbname=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD
        )
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        cursor.execute("""
            SELECT 
                fuzzer_id,
                time_bucket,
                FLOOR(program_size / %s) * %s as size_bucket,
                SUM(unique_programs) as total_unique_programs,
                SUM(total_executions) as total_executions,
                SUM(crashes) as total_crashes,
                SUM(failures) as total_failures,
                SUM(successes) as total_successes,
                SUM(timeouts) as total_timeouts,
                AVG(avg_coverage) as avg_coverage,
                MAX(max_coverage) as max_coverage,
                SUM(new_edges_found) as new_edges_found
            FROM program_convergence
            WHERE fuzzer_id = %s 
            AND time_bucket > NOW() - INTERVAL '%s hours'
            GROUP BY fuzzer_id, time_bucket, size_bucket
            ORDER BY time_bucket DESC, size_bucket
        """, (size_tolerance_bytes, size_tolerance_bytes, fuzzer_id, time_window_hours))
        
        rows = cursor.fetchall()
        result_json = json.dumps(rows, default=json_serial, indent=2)
        return result_json
        
    except psycopg2.Error as e:
        return f"Database error: {e}"
    except Exception as e:
        return f"Unexpected error: {e}"
    finally:
        if conn:
            conn.close()


@tool
def db_get_execution_outcome_distribution(fuzzer_id: int, time_window_hours: int = 1, sample_interval_minutes: int = 5) -> str:
    """
    Gets the distribution of execution outcomes over time for trend analysis.
    Uses the execution_outcome_distribution materialized view.
    
    Args:
        fuzzer_id: The fuzzer instance to analyze
        time_window_hours: How far back to look (default 1 hours)
        sample_interval_minutes: Aggregate data into this time interval (default 5 minutes)

    Returns:
        str: A JSON string containing the execution outcome distribution data.
    """
    conn = None
    try:
        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            dbname=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD
        )
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        cursor.execute("""
            SELECT 
                fuzzer_id,
                DATE_TRUNC('minute', time_bucket) - 
                    (EXTRACT(MINUTE FROM time_bucket)::INT % %s) * INTERVAL '1 minute' as sample_time,
                outcome,
                execution_outcome_id,
                SUM(execution_count) as total_executions,
                AVG(avg_coverage) as avg_coverage,
                SUM(new_edges_count) as new_edges_discovered
            FROM execution_outcome_distribution
            WHERE fuzzer_id = %s 
            AND time_bucket > NOW() - INTERVAL '%s hours'
            GROUP BY fuzzer_id, sample_time, outcome, execution_outcome_id
            ORDER BY sample_time DESC, outcome
        """, (sample_interval_minutes, fuzzer_id, time_window_hours))
        
        rows = cursor.fetchall()
        result_json = json.dumps(rows, default=json_serial, indent=2)
        return result_json
        
    except psycopg2.Error as e:
        return f"Database error: {e}"
    except Exception as e:
        return f"Unexpected error: {e}"
    finally:
        if conn:
            conn.close()


@tool
def db_get_program_coverage_mapping(fuzzer_id: int, limit: int = 50, min_coverage: float = None, sort_by: str = "max_coverage") -> str:
    """
    Gets programs mapped to their coverage statistics and execution outcomes.
    Uses the program_coverage_mapping materialized view.
    
    Args:
        fuzzer_id: The fuzzer instance to analyze
        limit: Maximum number of programs to return (default 50)
        min_coverage: Filter programs with at least this coverage percentage (optional)
        sort_by: Sort results by this column - options: max_coverage, new_edges_discovered, 
                 max_edges_found, execution_count (default: max_coverage)

    Returns:
        str: A JSON string containing the program coverage mapping data.
    """
    conn = None
    try:
        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            dbname=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD
        )
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        # Validate sort_by to prevent SQL injection
        valid_sort_columns = ['max_coverage', 'new_edges_discovered', 'max_edges_found', 'execution_count']
        if sort_by not in valid_sort_columns:
            sort_by = 'max_coverage'
        
        # Build query with optional min_coverage filter
        query = f"""
            SELECT 
                fuzzer_id,
                program_hash,
                created_at,
                source_mutators,
                contributors,
                execution_count,
                max_coverage,
                avg_coverage,
                max_edges_found,
                avg_edges_found,
                new_edges_discovered,
                crash_count,
                success_count,
                timeout_count,
                program_size,
                first_execution,
                last_execution
            FROM program_coverage_mapping
            WHERE fuzzer_id = %s
        """
        
        params = [fuzzer_id]
        
        if min_coverage is not None:
            query += " AND max_coverage >= %s"
            params.append(min_coverage)
        
        query += f" ORDER BY {sort_by} DESC NULLS LAST LIMIT %s"
        params.append(limit)
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        result_json = json.dumps(rows, default=json_serial, indent=2)
        return result_json
        
    except psycopg2.Error as e:
        return f"Database error: {e}"
    except Exception as e:
        return f"Unexpected error: {e}"
    finally:
        if conn:
            conn.close()
    


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


def fetch_program_js_from_db(program_hash: str) -> str:
    """Fetch program from database and convert to JS using FuzzILTool"""
    conn = None
    try:
        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            dbname=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD
        )
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute(
            "SELECT program_base64 FROM fuzzer WHERE program_hash = %s LIMIT 1",
            (program_hash,)
        )
        row = cursor.fetchone()
        if not row:
            return None
        
        program_b64 = row['program_base64']
        decoded = base64.b64decode(program_b64)
        
        with open(TEMP_FUZZIL_PATH, "wb") as f:
            f.write(decoded)
        
        cmd = f"{FUZZILLI_TOOL_BIN} --liftToJS {TEMP_FUZZIL_PATH}"
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        return get_output(result)
        
    except Exception as e:
        return f"Error fetching program: {e}"
    finally:
        if conn:
            conn.close()


@tool
def trace_v8_analysis(
    program_hash: str,
    presets: list = None,
    custom_flags: list = None,
    function_filter: str = None,
    turbo_path: str = "/tmp/turbofan_ir",
    timeout_seconds: int = 60
) -> str:
    """
    Unified V8 tracing tool supporting all trace/log flags for deep analysis.
    Fetches program from database by hash, converts to JS, and runs d8 with selected flags.

    If your input here is empty = {"",[]}, the tool will run with the default flags for the following options:
        presets: list = None,
        custom_flags: list = None,
        function_filter: str = None,
        turbo_path: str = "/tmp/turbofan_ir"
    
    Available presets:
        - tiering: trace-opt, trace-opt-status, trace-deopt, trace-osr, trace-file-names
        - tiering_verbose: verbose versions of tiering flags
        - turbofan: trace-turbo-graph, trace-turbo-types
        - turbofan_full: all turbofan tracing flags
        - maglev: trace-maglev-graph-building, maglev-print-feedback, maglev-print-provenance
        - maglev_full: all maglev tracing and print flags
        - ignition: print-bytecode
        - ignition_full: print-bytecode + trace-ignition-codegen
        - gc: trace-gc, trace-gc-nvp, trace-incremental-marking
        - gc_full: all gc tracing flags
        - ic_maps: log-ic, log-maps, trace-generalization, trace-prototype-users
        - ic_maps_full: all IC and maps flags with details
        - wasm: trace-wasm, trace-wasm-decoder, trace-wasm-compiler, trace-liftoff
        - regexp: trace-regexp-bytecodes, trace-regexp-parser, trace-regexp-tier-up
        - serialization: trace-serializer, trace-deserialization, profile-deserialization
    
    Args:
        program_hash: The hash of the program to analyze (from fuzzer table)
        presets: List of preset names to enable (e.g. ["tiering", "maglev"])
        custom_flags: List of individual flags to add (e.g. ["--trace-turbo-inlining"])
        function_filter: Filter pattern for function-specific tracing (applied to turbo/maglev/bytecode filters)
        turbo_path: Directory path for turbofan IR dumps (enables --trace-turbo-path)
        timeout_seconds: Max execution time in seconds (default 60)
    
    Returns:
        JSON string with trace output, any errors, and metadata about flags used
    """
    if len(presets) == 0:
        presets = None
    if len(custom_flags) == 0:
        custom_flags = None
    if function_filter == "":
        function_filter = None
    if turbo_path == "":
        turbo_path = "/tmp/turbofan_ir"


    js_code = fetch_program_js_from_db(program_hash)
    if js_code is None:
        return json.dumps({"error": f"Program with hash {program_hash} not found in database"})
    if js_code.startswith("Error"):
        return json.dumps({"error": js_code})
    
    filepath_js = f"/tmp/{program_hash}.js"
    with open(filepath_js, "w") as f:
        f.write(js_code)
    
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
    
    if turbo_path:
        os.makedirs(turbo_path, exist_ok=True)
        flags.append(f"--trace-turbo-path={turbo_path}")
    
    flags = list(dict.fromkeys(flags))
    
    cmd_parts = [D8_PATH] + flags + [filepath_js]
    cmd = " ".join(cmd_parts)
    
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds
        )
        
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
        
    except subprocess.TimeoutExpired:
        return json.dumps({
            "error": f"Execution timed out after {timeout_seconds} seconds",
            "flags_used": flags,
            "program_hash": program_hash
        })
    except Exception as e:
        return json.dumps({"error": f"Execution error: {e}", "flags_used": flags})
    finally:
        if os.path.exists(filepath_js):
            os.remove(filepath_js)


@tool
def list_v8_trace_options() -> str:
    """
    List all available V8 trace presets and individual flags for trace_v8_analysis tool.
    Use this to understand what tracing options are available before calling trace_v8_analysis.
    
    Returns:
        JSON string with all presets, their flags, and available individual flags
    """
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
