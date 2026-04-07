import os
import json
import base64
import hashlib
import re
import tempfile
import psycopg2
import psycopg2.extras
from pathlib import Path

import tools._shared as shared_tools
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

_AGENTIC_JS_MUTATOR = "AgenticJSSeed"
_AGENTIC_JS_CONTRIBUTOR = "EBGGeneratedJS"


def _ensure_generated_program_queue_table(conn) -> None:
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS generated_program_queue (
            queue_id BIGSERIAL PRIMARY KEY,
            target_fuzzer_id BIGINT NOT NULL,
            program_hash VARCHAR(64) NOT NULL,
            program_base64 TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'agentic',
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (target_fuzzer_id, program_hash)
        )
        """
    )
    conn.commit()


def _build_fuzzilli_compile_env() -> dict | None:
    env = os.environ.copy()
    candidates = []

    # Prefer an explicit FUZZILLI_PATH if provided by runtime env.
    fuzzilli_root = os.getenv("FUZZILLI_PATH", "").strip()
    if fuzzilli_root:
        candidates.append(Path(fuzzilli_root) / "Sources" / "Fuzzilli" / "Compiler" / "Parser" / "node_modules")

    # Fallback to this repo layout: <repo>/Sources/Fuzzilli/Compiler/Parser/node_modules
    repo_root = Path(__file__).resolve().parents[4]
    candidates.append(repo_root / "Sources" / "Fuzzilli" / "Compiler" / "Parser" / "node_modules")

    for node_modules_path in candidates:
        if node_modules_path.exists():
            existing = env.get("NODE_PATH", "").strip()
            if existing:
                env["NODE_PATH"] = f"{node_modules_path}:{existing}"
            else:
                env["NODE_PATH"] = str(node_modules_path)
            return env

    return None


def _looks_like_javascript_text(decoded: bytes) -> bool:
    try:
        text = decoded.decode("utf-8")
    except UnicodeDecodeError:
        return False
    js_markers = (
        "function ",
        "const ",
        "let ",
        "var ",
        "class ",
        "=>",
        "print(",
        "'use strict'",
        '"use strict"',
    )
    return any(marker in text for marker in js_markers)


def _lift_program_bytes_to_js(decoded: bytes) -> str:
    with open(TEMP_FUZZIL_PATH, "wb") as f:
        f.write(decoded)

    result = shared_tools.run_fuzzilli_tool(["--liftToJS", TEMP_FUZZIL_PATH])
    lifted = shared_tools.get_output(result)
    if result.returncode == 0:
        return lifted
    if _looks_like_javascript_text(decoded):
        return decoded.decode("utf-8")
    return lifted


def _compile_js_to_fuzzil_bytes(js_program: str) -> tuple[bytes | None, str | None]:
    with tempfile.TemporaryDirectory(prefix="ebg_js_compile_") as tmpdir:
        js_path = os.path.join(tmpdir, "generated.js")
        with open(js_path, "w", encoding="utf-8") as f:
            f.write(js_program)

        compile_env = _build_fuzzilli_compile_env()
        result = shared_tools.run_fuzzilli_tool(["--compile", js_path], timeout=120, env=compile_env)
        if result.returncode != 0:
            return None, shared_tools.get_output(result)

        fzil_path = os.path.splitext(js_path)[0] + ".fzil"
        if not os.path.exists(fzil_path):
            return None, f"Error: FuzzILTool did not produce expected output file {fzil_path}"

        with open(fzil_path, "rb") as f:
            return f.read(), None


def _normalize_target_fuzzer_id(fuzzer_id) -> tuple[int | None, str | None]:
    if isinstance(fuzzer_id, bool):
        return None, "fuzzer_id must identify a real fuzzer, not a boolean"
    if isinstance(fuzzer_id, int):
        return (fuzzer_id, None) if fuzzer_id > 0 else (None, "fuzzer_id must be a positive integer")

    raw = str(fuzzer_id or "").strip()
    if not raw:
        return None, "fuzzer_id is required"
    if raw.isdigit():
        parsed = int(raw)
        return (parsed, None) if parsed > 0 else (None, "fuzzer_id must be a positive integer")

    match = re.fullmatch(r"fuzzer-(\d+)", raw)
    if match:
        parsed = int(match.group(1))
        return (parsed, None) if parsed > 0 else (None, "fuzzer_id must be a positive integer")

    return None, "fuzzer_id must be a positive integer or a label like 'fuzzer-3'"


def _resolve_existing_fuzzer_id(fuzzer_id, conn=None) -> tuple[int | None, str | None, dict | None]:
    resolved_fuzzer_id, normalize_error = _normalize_target_fuzzer_id(fuzzer_id)
    if normalize_error:
        return None, normalize_error, None

    owns_connection = conn is None
    try:
        if conn is None:
            conn = psycopg2.connect(
                host=POSTGRES_HOST,
                port=POSTGRES_PORT,
                dbname=POSTGRES_DB,
                user=POSTGRES_USER,
                password=POSTGRES_PASSWORD,
            )

        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute(
            """
            SELECT fuzzer_id, status, created_at, last_activity, engine_arguments
            FROM main
            WHERE fuzzer_id = %s
            LIMIT 1
            """,
            (resolved_fuzzer_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None, f"fuzzer_id {resolved_fuzzer_id} was not found in the main fuzzer table", None

        return resolved_fuzzer_id, None, row
    except psycopg2.Error as e:
        return None, f"Database error: {e}", None
    except Exception as e:
        return None, f"Unexpected error: {e}", None
    finally:
        if owns_connection and conn:
            conn.close()


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


def db_resolve_fuzzer_id(fuzzer_id) -> str:
    resolved_fuzzer_id, error, row = _resolve_existing_fuzzer_id(fuzzer_id)
    if error:
        return json.dumps({"error": error}, default=json_serial, indent=2)

    return json.dumps(
        {
            "input": fuzzer_id,
            "resolved_fuzzer_id": resolved_fuzzer_id,
            "fuzzer": row,
        },
        default=json_serial,
        indent=2,
    )


def db_list_programs(limit: int = 10, offset: int = 0, fuzzer_id=None, include_source: bool = False) -> str:
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
        select_columns = "program_hash, fuzzer_id, inserted_at, program_base64" if include_source else "program_hash, fuzzer_id, inserted_at"
        if fuzzer_id is not None:
            resolved_fuzzer_id, error, _ = _resolve_existing_fuzzer_id(fuzzer_id, conn=conn)
            if error:
                return json.dumps({"error": error}, default=json_serial, indent=2)
            cursor.execute(
                f"SELECT {select_columns} FROM program WHERE fuzzer_id = %s ORDER BY inserted_at DESC LIMIT %s OFFSET %s",
                (resolved_fuzzer_id, limit, offset),
            )
        else:
            cursor.execute(
                f"SELECT {select_columns} FROM program ORDER BY inserted_at DESC LIMIT %s OFFSET %s",
                (limit, offset),
            )
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


def db_get_fuzzer_performance_summary(fuzzer_id) -> str:
    conn = None
    try:
        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            dbname=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD
        )
        resolved_fuzzer_id, error, _ = _resolve_existing_fuzzer_id(fuzzer_id, conn=conn)
        if error:
            return json.dumps({"error": error}, default=json_serial, indent=2)

        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute("SELECT * FROM fuzzer_dashboard WHERE fuzzer_id = %s", (resolved_fuzzer_id,))
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
    try:
        output = _lift_program_bytes_to_js(decoded_program)
        return json.dumps(output)
    except Exception as e:
        return json.dumps(f"Error running FuzzILTool: {e}")


def db_get_crash_program_as_js(program_hash: str) -> str:
    """Fetch a crash program from DB by hash, decode from base64, and convert
    to JavaScript in one atomic step.  This avoids passing large base64 strings
    through the LLM tool-call interface (which can truncate them and cause
    'Incorrect padding' errors)."""
    conn = None
    try:
        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            dbname=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD,
        )
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute(
            "SELECT program_base64 FROM program WHERE program_hash = %s LIMIT 1",
            (program_hash,),
        )
        row = cursor.fetchone()
        if not row:
            return json.dumps({"error": f"Program with hash {program_hash} not found in database"})

        program_b64 = row["program_base64"]
        decoded = base64.b64decode(program_b64)

        if _looks_like_javascript_text(decoded):
            js_code = decoded.decode("utf-8")
        else:
            js_code = _lift_program_bytes_to_js(decoded)

        if js_code.startswith("Error"):
            return json.dumps({"program_hash": program_hash, "error": js_code})

        return json.dumps(
            {"program_hash": program_hash, "javascript_code": js_code}, indent=2
        )

    except psycopg2.Error as e:
        return json.dumps({"error": f"Database error: {e}"})
    except Exception as e:
        return json.dumps({"error": f"Error converting crash program: {e}"})
    finally:
        if conn:
            conn.close()


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


def db_get_crash_diversity(fuzzer_id) -> str:
    conn = None
    try:
        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            dbname=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD
        )
        resolved_fuzzer_id, error, _ = _resolve_existing_fuzzer_id(fuzzer_id, conn=conn)
        if error:
            return json.dumps({"error": error}, default=json_serial, indent=2)

        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute("SELECT * FROM crash_analysis WHERE fuzzer_id = %s", (resolved_fuzzer_id,))
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


def db_get_mutator_effectiveness(fuzzer_id, time_window_hours: int = 1) -> str:
    conn = None
    try:
        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            dbname=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD
        )
        resolved_fuzzer_id, error, _ = _resolve_existing_fuzzer_id(fuzzer_id, conn=conn)
        if error:
            return json.dumps({"error": error}, default=json_serial, indent=2)

        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute("""
            SELECT * FROM mutator_effectiveness_per_fuzzer
            WHERE fuzzer_id = %s
            AND last_updated > NOW() - INTERVAL '%s hours'
        """, (resolved_fuzzer_id, time_window_hours))
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


def db_get_program_grouping(fuzzer_id, time_window_hours: int = 1, size_tolerance_bytes: int = 50) -> str:
    conn = None
    try:
        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            dbname=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD
        )
        resolved_fuzzer_id, error, _ = _resolve_existing_fuzzer_id(fuzzer_id, conn=conn)
        if error:
            return json.dumps({"error": error}, default=json_serial, indent=2)

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
        """, (size_tolerance_bytes, size_tolerance_bytes, resolved_fuzzer_id, time_window_hours))
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


def db_get_execution_outcome_distribution(fuzzer_id, time_window_hours: int = 1, sample_interval_minutes: int = 5) -> str:
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
        resolved_fuzzer_id, error, _ = _resolve_existing_fuzzer_id(fuzzer_id, conn=conn)
        if error:
            return json.dumps({"error": error}, default=json_serial, indent=2)

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
        """, (sample_interval_minutes, resolved_fuzzer_id, time_window_hours))
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
        return _lift_program_bytes_to_js(decoded)

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
        js_code = _lift_program_bytes_to_js(decoded)

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

        normalized_fuzzer_id, normalize_error = _normalize_target_fuzzer_id(fuzzer_id)
        if normalize_error:
            return json.dumps({"error": normalize_error}, indent=2)

        compiled_program_bytes, compile_error = _compile_js_to_fuzzil_bytes(js_program)
        if compile_error:
            return json.dumps({"error": compile_error}, indent=2)

        program_base64 = base64.b64encode(compiled_program_bytes).decode('utf-8')
        program_hash = hashlib.sha256(program_base64.encode('utf-8')).hexdigest()

        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            dbname=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD
        )
        _ensure_generated_program_queue_table(conn)
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        insert_query = """
            INSERT INTO generated_program_queue (target_fuzzer_id, program_hash, program_base64, source, metadata)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (target_fuzzer_id, program_hash) DO NOTHING
            RETURNING program_hash
        """
        cursor.execute(
            insert_query,
            (
                normalized_fuzzer_id,
                program_hash,
                program_base64,
                "agentic",
                psycopg2.extras.Json(
                    {
                        "source_mutators": [_AGENTIC_JS_MUTATOR],
                        "contributors": [_AGENTIC_JS_CONTRIBUTOR],
                    }
                ),
            ),
        )
        row = cursor.fetchone()
        conn.commit()
        _DB_QUERY_CACHE.clear()

        if row is None:
            result = {
                "program_id": program_hash,
                "target_fuzzer_id": normalized_fuzzer_id,
                "message": "Program already exists in generated queue",
            }
        else:
            result = {"program_id": row['program_hash'], "target_fuzzer_id": normalized_fuzzer_id}

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
    description="List programs from the Fuzzilli database. Accepts an optional numeric DB fuzzer id or label like fuzzer-3.",
    parameters={
        "limit": {"type": "number", "description": "Max programs to return (default 10)", "required": False},
        "offset": {"type": "number", "description": "Skip this many programs", "required": False},
        "fuzzer_id": {"type": "string", "description": "Optional target DB fuzzer id or label like fuzzer-3", "required": False},
        "include_source": {"type": "boolean", "description": "Include base64-encoded program source", "required": False},
    },
    execute_function=lambda x: db_list_programs(
        limit=int(x.get("limit", 10)),
        offset=int(x.get("offset", 0)),
        fuzzer_id=x.get("fuzzer_id"),
        include_source=bool(x.get("include_source", False)),
    ),
)

db_resolve_fuzzer_id_tool = IkaTools(
    name="db_resolve_fuzzer_id",
    description="Resolve a fuzzer routing label like fuzzer-3 or a numeric string to the concrete DB fuzzer id in the main table.",
    parameters={
        "fuzzer_id": {"type": "string", "description": "DB fuzzer id, numeric string, or label like fuzzer-3", "required": True},
    },
    execute_function=lambda x: db_resolve_fuzzer_id(x["fuzzer_id"]),
)

db_get_fuzzer_performance_summary_tool = IkaTools(
    name="db_get_fuzzer_performance_summary",
    description="Retrieve performance metrics for a fuzzer from the fuzzer_dashboard materialized view.",
    parameters={
        "fuzzer_id": {"type": "string", "description": "Target DB fuzzer id or label like fuzzer-3", "required": True},
    },
    execute_function=lambda x: db_get_fuzzer_performance_summary(x["fuzzer_id"]),
)

base64_program_to_js_tool = IkaTools(
    name="base64_program_to_js",
    description="Decode a base64 FuzzIL protobuf and lift it to JavaScript using FuzzILTool.",
    parameters={
        "base64_program": {"type": "string", "description": "Base64-encoded FuzzIL program", "required": True},
    },
    execute_function=lambda x: base64_program_to_js(x["base64_program"]),
)

db_get_crash_program_as_js_tool = IkaTools(
    name="db_get_crash_program_as_js",
    description=(
        "Fetch a crash program from the database by its program_hash, decode it, "
        "and return the JavaScript source code.  Use this instead of manually calling "
        "base64_program_to_js with a raw base64 string (which can be truncated by "
        "the tool-call interface).  Only requires the program_hash."
    ),
    parameters={
        "program_hash": {
            "type": "string",
            "description": "The program_hash (SHA-256 hex) from the program or crash_analysis table",
            "required": True,
        },
    },
    execute_function=lambda x: db_get_crash_program_as_js(x["program_hash"]),
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
        "fuzzer_id": {"type": "string", "description": "Target DB fuzzer id or label like fuzzer-3", "required": True},
    },
    execute_function=lambda x: db_get_crash_diversity(x["fuzzer_id"]),
)

db_get_mutator_effectiveness_tool = IkaTools(
    name="db_get_mutator_effectiveness",
    description="Retrieve mutator effectiveness stats for a fuzzer from mutator_effectiveness_per_fuzzer view.",
    parameters={
        "fuzzer_id": {"type": "string", "description": "Target DB fuzzer id or label like fuzzer-3", "required": True},
        "time_window_hours": {"type": "number", "description": "Lookback window in hours (default 1)", "required": False},
    },
    execute_function=lambda x: db_get_mutator_effectiveness(
        x["fuzzer_id"], int(x.get("time_window_hours", 1))
    ),
)

db_get_program_grouping_tool = IkaTools(
    name="db_get_program_grouping",
    description="Group programs by size buckets to analyze convergence patterns. Uses program_convergence view.",
    parameters={
        "fuzzer_id": {"type": "string", "description": "Target DB fuzzer id or label like fuzzer-3", "required": True},
        "time_window_hours": {"type": "number", "description": "Lookback window in hours (default 1)", "required": False},
        "size_tolerance_bytes": {"type": "number", "description": "Bucket programs within this byte range (default 50)", "required": False},
    },
    execute_function=lambda x: db_get_program_grouping(
        x["fuzzer_id"],
        int(x.get("time_window_hours", 1)),
        int(x.get("size_tolerance_bytes", 50)),
    ),
)

db_get_execution_outcome_distribution_tool = IkaTools(
    name="db_get_execution_outcome_distribution",
    description="Get distribution of execution outcomes (crash/success/timeout) over time from execution_outcome_distribution view.",
    parameters={
        "fuzzer_id": {"type": "string", "description": "Target DB fuzzer id or label like fuzzer-3", "required": True},
        "time_window_hours": {"type": "number", "description": "Lookback window in hours (default 1)", "required": False},
        "sample_interval_minutes": {"type": "number", "description": "Aggregation interval in minutes (default 5)", "required": False},
    },
    execute_function=lambda x: db_get_execution_outcome_distribution(
        x["fuzzer_id"],
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
    description="Compile a generated JavaScript program to FuzzIL and enqueue it into the target fuzzer's generated corpus inbox. Accepts a numeric DB fuzzer id or a label like fuzzer-3.",
    parameters={
        "js_program": {"type": "string", "description": "JavaScript source code to enqueue", "required": True},
        "fuzzer_id": {"type": "string", "description": "Target DB fuzzer id or label like fuzzer-3", "required": True},
    },
    execute_function=lambda x: db_store_generated_program(x["js_program"], x["fuzzer_id"]),
)
