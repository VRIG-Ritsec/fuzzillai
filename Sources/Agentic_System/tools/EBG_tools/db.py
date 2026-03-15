import os
import subprocess
import json
import base64
import hashlib
import psycopg2
import psycopg2.extras

from tools._shared import FUZZILLI_TOOL_BIN, get_output, run_command
from IkaCore.tools import IkaTools

from ._shared import (
    POSTGRES_HOST,
    POSTGRES_PORT,
    POSTGRES_DB,
    POSTGRES_USER,
    POSTGRES_PASSWORD,
    TEMP_FUZZIL_PATH,
    json_serial,
    _validate_and_prepare_sql,
    _is_read_only_sql,
    _normalize_sql_whitespace,
    _build_cache_key,
    _cache_get,
    _cache_set,
    _DB_QUERY_CACHE,
)


def db_query(query: str, params: list = None) -> str:
    conn = None
    try:
        normalized_query, exec_params, validation_error = _validate_and_prepare_sql(query, params)
        if validation_error:
            return validation_error

        read_only_query = _is_read_only_sql(normalized_query)
        cache_key = _build_cache_key(_normalize_sql_whitespace(normalized_query), exec_params)
        if read_only_query:
            cached = _cache_get(cache_key)
            if cached is not None:
                return cached
        else:
            _DB_QUERY_CACHE.clear()

        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            dbname=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD
        )
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute(normalized_query, exec_params)
        if cursor.description is None:
            conn.commit()
            return json.dumps(
                {"status": "ok", "rows_affected": cursor.rowcount},
                default=json_serial,
                indent=2,
            )
        rows = cursor.fetchall()
        result_json = json.dumps(rows, default=json_serial, indent=2)
        if read_only_query:
            _cache_set(cache_key, result_json)
        return result_json
    except psycopg2.Error as e:
        return f"Database error: {e}"
    except Exception as e:
        return f"Unexpected error: {e}"
    finally:
        if conn:
            conn.close()


def db_list_programs(limit: int = 10, offset: int = 0, fuzzer_id: int = None, include_source: bool = False) -> str:
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
        cursor.execute("SELECT program_hash, fuzzer_id, inserted_at FROM program WHERE fuzzer_id = %s LIMIT %s OFFSET %s", (fuzzer_id, limit, offset))
        rows = cursor.fetchall()
        if include_source:
            cursor.execute("SELECT program_hash, fuzzer_id, inserted_at, program_base64 FROM program WHERE fuzzer_id = %s LIMIT %s OFFSET %s", (fuzzer_id, limit, offset))
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


def db_get_fuzzer_performance_summary(fuzzer_id: int) -> str:
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


def base64_program_to_js(base64_program: str) -> str:
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


def db_list_fuzzers() -> str:
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


def db_get_crash_diversity(fuzzer_id: int) -> str:
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


def db_get_mutator_effectiveness(fuzzer_id: int, time_window_hours: int = 1) -> str:
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


def db_get_program_grouping(fuzzer_id: int, time_window_hours: int = 1, size_tolerance_bytes: int = 50) -> str:
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


def db_get_execution_outcome_distribution(fuzzer_id: int, time_window_hours: int = 1, sample_interval_minutes: int = 5) -> str:
    conn = None
    try:
        if sample_interval_minutes <= 0:
            return "Unexpected error: sample_interval_minutes must be > 0"

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
                    (EXTRACT(MINUTE FROM time_bucket)::INT %% %s) * INTERVAL '1 minute' as sample_time,
                outcome,
                execution_outcome_id,
                SUM(execution_count) as total_executions,
                AVG(avg_coverage) as avg_coverage,
                SUM(new_edges_count) as new_edges_discovered
            FROM execution_outcome_distribution
            WHERE fuzzer_id = %s
            AND time_bucket > NOW() - (%s * INTERVAL '1 hour')
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


def fetch_program_js_from_db(program_hash: str) -> str:
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
            "SELECT program_base64 FROM program WHERE program_hash = %s LIMIT 1",
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


def get_program_js_from_hash(program_hash: str) -> str:
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
            "SELECT program_base64 FROM program WHERE program_hash = %s LIMIT 1",
            (program_hash,)
        )
        row = cursor.fetchone()
        if not row:
            return json.dumps({"error": f"Program with hash {program_hash} not found in database"})

        program_b64 = row['program_base64']
        decoded = base64.b64decode(program_b64)

        with open(TEMP_FUZZIL_PATH, "wb") as f:
            f.write(decoded)

        cmd = f"{FUZZILLI_TOOL_BIN} --liftToJS {TEMP_FUZZIL_PATH}"
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        js_code = get_output(result)

        if js_code.startswith("Error"):
            return json.dumps({"error": js_code})

        return json.dumps({
            "program_hash": program_hash,
            "javascript_code": js_code
        }, indent=2)

    except psycopg2.Error as e:
        return json.dumps({"error": f"Database error: {e}"})
    except Exception as e:
        return json.dumps({"error": f"Error fetching program: {e}"})
    finally:
        if conn:
            conn.close()


def db_store_generated_program(js_program: str, fuzzer_id: int) -> str:
    conn = None
    try:
        if not js_program or not js_program.strip():
            return json.dumps({"error": "js_program is required and cannot be empty"}, indent=2)

        if not fuzzer_id or fuzzer_id <= 0:
            return json.dumps({"error": "fuzzer_id is required and must be a positive integer"}, indent=2)

        js_program_bytes = js_program.encode('utf-8')
        js_program_base64 = base64.b64encode(js_program_bytes).decode('utf-8')
        program_hash = hashlib.sha256(js_program_base64.encode('utf-8')).hexdigest()

        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            dbname=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD
        )
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        insert_query = """
            INSERT INTO fuzzer (program_hash, fuzzer_id, program_base64)
            VALUES (%s, %s, %s)
            ON CONFLICT (program_hash) DO NOTHING
            RETURNING program_hash
        """
        cursor.execute(insert_query, (program_hash, fuzzer_id, js_program_base64))
        row = cursor.fetchone()
        conn.commit()
        _DB_QUERY_CACHE.clear()

        if row is None:
            result = {"program_id": program_hash, "message": "Program already exists in database"}
        else:
            result = {"program_id": row['program_hash']}

        return json.dumps(result, default=json_serial, indent=2)

    except psycopg2.Error as e:
        return json.dumps({"error": f"Database error: {e}"}, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Error storing JavaScript program: {e}"}, indent=2)
    finally:
        if conn:
            conn.close()


db_query_tool = IkaTools(
    name="db_query",
    description="Execute a read-only or write SQL query on the Fuzzilli PostgreSQL database. Use SELECT/WITH/EXPLAIN for reads.",
    parameters={
        "query": {"type": "string", "description": "SQL query. Use $1, $2 for positional params", "required": True},
        "params": {"type": "array", "items": {"type": "string"}, "description": "Parameter values for $1, $2, etc.", "required": False},
    },
    execute_function=lambda x: db_query(x["query"], x.get("params", [])),
)

db_list_programs_tool = IkaTools(
    name="db_list_programs",
    description="List programs from the Fuzzilli database for a given fuzzer. Paginate with limit/offset.",
    parameters={
        "limit": {"type": "number", "description": "Max programs to return (default 10)", "required": False},
        "offset": {"type": "number", "description": "Skip this many programs", "required": False},
        "fuzzer_id": {"type": "number", "description": "Fuzzer instance ID to filter by", "required": False},
        "include_source": {"type": "boolean", "description": "Include base64-encoded program source", "required": False},
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
    description="Retrieve performance metrics for a fuzzer from the fuzzer_dashboard materialized view.",
    parameters={
        "fuzzer_id": {"type": "number", "description": "Fuzzer instance ID", "required": True},
    },
    execute_function=lambda x: db_get_fuzzer_performance_summary(int(x["fuzzer_id"])),
)

base64_program_to_js_tool = IkaTools(
    name="base64_program_to_js",
    description="Decode a base64 FuzzIL protobuf and lift it to JavaScript using FuzzILTool.",
    parameters={
        "base64_program": {"type": "string", "description": "Base64-encoded FuzzIL program", "required": True},
    },
    execute_function=lambda x: base64_program_to_js(x["base64_program"]),
)

db_list_fuzzers_tool = IkaTools(
    name="db_list_fuzzers",
    description="List all fuzzer instances registered in the Fuzzilli database.",
    parameters={"N/A": "N/A"},
    execute_function=lambda _: db_list_fuzzers(),
)

db_get_crash_diversity_tool = IkaTools(
    name="db_get_crash_diversity",
    description="Get crash diversity statistics for a fuzzer from the crash_analysis view.",
    parameters={
        "fuzzer_id": {"type": "number", "description": "Fuzzer instance ID", "required": True},
    },
    execute_function=lambda x: db_get_crash_diversity(int(x["fuzzer_id"])),
)

db_get_mutator_effectiveness_tool = IkaTools(
    name="db_get_mutator_effectiveness",
    description="Retrieve mutator effectiveness stats for a fuzzer from mutator_effectiveness_per_fuzzer view.",
    parameters={
        "fuzzer_id": {"type": "number", "description": "Fuzzer instance ID", "required": True},
        "time_window_hours": {"type": "number", "description": "Lookback window in hours (default 1)", "required": False},
    },
    execute_function=lambda x: db_get_mutator_effectiveness(
        int(x["fuzzer_id"]), int(x.get("time_window_hours", 1))
    ),
)

db_get_program_grouping_tool = IkaTools(
    name="db_get_program_grouping",
    description="Group programs by size buckets to analyze convergence patterns. Uses program_convergence view.",
    parameters={
        "fuzzer_id": {"type": "number", "description": "Fuzzer instance ID", "required": True},
        "time_window_hours": {"type": "number", "description": "Lookback window in hours (default 1)", "required": False},
        "size_tolerance_bytes": {"type": "number", "description": "Bucket programs within this byte range (default 50)", "required": False},
    },
    execute_function=lambda x: db_get_program_grouping(
        int(x["fuzzer_id"]),
        int(x.get("time_window_hours", 1)),
        int(x.get("size_tolerance_bytes", 50)),
    ),
)

db_get_execution_outcome_distribution_tool = IkaTools(
    name="db_get_execution_outcome_distribution",
    description="Get distribution of execution outcomes (crash/success/timeout) over time from execution_outcome_distribution view.",
    parameters={
        "fuzzer_id": {"type": "number", "description": "Fuzzer instance ID", "required": True},
        "time_window_hours": {"type": "number", "description": "Lookback window in hours (default 1)", "required": False},
        "sample_interval_minutes": {"type": "number", "description": "Aggregation interval in minutes (default 5)", "required": False},
    },
    execute_function=lambda x: db_get_execution_outcome_distribution(
        int(x["fuzzer_id"]),
        int(x.get("time_window_hours", 1)),
        int(x.get("sample_interval_minutes", 5)),
    ),
)

get_program_js_from_hash_tool = IkaTools(
    name="get_program_js_from_hash",
    description="Fetch a program by its hash from the database, decode from base64, and convert to JavaScript. Use for crash analysis.",
    parameters={
        "program_hash": {"type": "string", "description": "Program hash (SHA or DB identifier)", "required": True},
    },
    execute_function=lambda x: get_program_js_from_hash(x["program_hash"]),
)

db_store_generated_program_tool = IkaTools(
    name="db_store_generated_program",
    description="Store a generated JavaScript program in the database. Program is base64-encoded and hashed before insertion.",
    parameters={
        "js_program": {"type": "string", "description": "JavaScript source code to store", "required": True},
        "fuzzer_id": {"type": "number", "description": "Fuzzer instance ID to associate with", "required": True},
    },
    execute_function=lambda x: db_store_generated_program(x["js_program"], int(x["fuzzer_id"])),
)
