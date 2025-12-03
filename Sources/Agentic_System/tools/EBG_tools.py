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
            cursor.execute("SELECT program_hash, fuzzer_id, inserted_at, program_source FROM fuzzer WHERE fuzzer_id = %s LIMIT %s OFFSET %s", (fuzzer_id, limit, offset))
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
    
    