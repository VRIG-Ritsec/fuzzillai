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
    """Get performance from fuzzer_dashboard materialized view using index idx_fuzzer_dashboard_id"""
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
def db_get_mutator_effectiveness(fuzzer_id: int, time_window_hours: int = 24) -> str:
    """
    Use database materialized view for mutator effectiveness, mutator_effectiveness_per_fuzzer limited to time_window_hours
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
def db_get_program_convergence(fuzzer_id: int, time_window_hours: int = 24, size_tolerance_bytes: int = 50) -> str:
    return ""
    
@tool
def db_get_execution_outcome_distribution(fuzzer_id: int, time_window_hours: int = 24, sample_interval_minutes: int = 5) -> str:
    """

    """  
    return ""    


@tool
def db_get_program_coverage_mapping(fuzzer_id: int, limit: int = 50, min_coverage: float = None, sort_by: str = "coverage_total") -> str:
    return ""
    
    