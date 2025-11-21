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


# provide the agents calling this tool with the associated database schema in DatabaseSchema.swift
@tool
def db_query(query: str, params: list = []) -> str:
    '''
    Perform and arbitrary query on the PostgresSQL database.

    Args:
        query (str): The SQL query to perform.
        params (list): The parameters to pass to the query.

    Returns:
        str: A JSON string containing the query results.
    '''
    conn = None
    try:
        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            dbname=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD
        )

        query = query.strip()
        if not query:
            return "Error: Empty query provided"
        if "%s" in query and not params:
            return "Error: Query contains %s placeholder but no parameters provided"

        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute(query, params)
        rows = cursor.fetchall()
        result_json = json.dumps(rows, default=json_serial, indent=2)
        return result_json  

    except psycopg2.Error as e:
        return f"Database error: {e}... maybe try again?"
    except Exception as e:
        return f"Unexpected error: {e}"
    finally:
        if conn:
            conn.close()

@tool
def db_list_programs(limit: int = 10, offset: int = 0, fuzzer_id: int = None, include_source: bool = False) -> str:
    """
    Lists executed programs from the Fuzzilli database with pagination and filtering.

    Args:
        limit (int): The number of programs to retrieve (default: 10).
        offset (int): The number of programs to skip (default: 0).
        fuzzer_id (int, optional): If provided, only list programs from this specific fuzzer instance.
        include_source (bool): If True, includes the full 'program_base64' field. 
                               Defaults to False to save token usage/bandwidth.

    Returns:
        str: A JSON string containing a list of program metadata (hash, size, creation time, mutator).
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

        fields = "program_hash, fuzzer_id, created_at, program_size, source_mutator, parent_program_hash"
        if include_source:
            fields += ", program_base64"

        query = f"SELECT {fields} FROM program"
        params = []

        if fuzzer_id is not None:
            query += " WHERE fuzzer_id = %s"
            params.append(fuzzer_id)

        query += " LIMIT %s"
        params.extend([limit])

        cursor.execute(query, tuple(params))
        rows = cursor.fetchall()
        result_json = json.dumps(rows, default=json_serial, indent=2)
        return result_json

    except psycopg2.Error as e:
        return f"Database error: {e}... maybe try again?"
    except Exception as e:
        return f"Unexpected error: {e}"
    finally:
        if conn:
            conn.close()
@tool
def db_get_fuzzer_performance_summary(fuzzer_id: int) -> str:
    '''
    Gets performance summary information about a specific fuzzer instance from the database.
    Uses optimized query with pre-computed aggregations to avoid correlated subqueries.
    
    Args:
        fuzzer_id (int): The ID of the fuzzer instance to get performance summary information about.
    Returns:
        str: A JSON string containing the fuzzer performance summary information.
    '''
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
        
        # Optimized query using window functions and aggregations
        # instead of correlated subqueries
        query = """
        WITH fuzzer_data AS (
            SELECT 
                m.fuzzer_id,
                m.fuzzer_name,
                m.status,
                m.created_at,
                COUNT(DISTINCT p.program_hash) as programs_count,
                COUNT(DISTINCT e.execution_id) as executions_count,
                SUM(CASE WHEN eo.outcome = 'Crashed' THEN 1 ELSE 0 END) as crash_count,
                MAX(e.coverage_total) as highest_coverage_pct,
                COUNT(CASE WHEN e.created_at > NOW() - INTERVAL '1 hour' THEN 1 END)::NUMERIC / 3600.0 as execs_per_second
            FROM main m
            LEFT JOIN program p ON m.fuzzer_id = p.fuzzer_id
            LEFT JOIN execution e ON p.program_hash = e.program_hash
            LEFT JOIN execution_outcome eo ON e.execution_outcome_id = eo.id
            WHERE m.fuzzer_id = %s
            GROUP BY m.fuzzer_id, m.fuzzer_name, m.status, m.created_at
        )
        SELECT 
            fuzzer_id,
            fuzzer_name,
            status,
            COALESCE(programs_count, 0) as programs_count,
            COALESCE(executions_count, 0) as executions_count,
            COALESCE(crash_count, 0) as crash_count,
            COALESCE(highest_coverage_pct, 0) as highest_coverage_pct,
            COALESCE(execs_per_second, 0) as execs_per_second
        FROM fuzzer_data
        """
        
        params = [fuzzer_id]
        cursor.execute(query, params)
        rows = cursor.fetchall()
        result_json = json.dumps(rows, default=json_serial, indent=2)
        return result_json
        
    except psycopg2.Error as e:
        return f"Database error: {e}... maybe try again?"
    except Exception as e:
        return f"Unexpected error: {e}"


@tool
def base64_program_to_js(base64_program: str) -> str:
    '''
    Decode a base64 program into JavaScript

    Args:
        base64_program (str): The fuzzil as base64 to be decoded into JavaScript

    Returns:
        str: The JavaScript program decoded from the base64 program
    '''
    try:
        decoded_program = base64.b64decode(base64_program)
    except base64.binascii.Error as e:
        return f"Error decoding base64 program: {e}"
    except UnicodeDecodeError as e:
        return f"Error decoding unicode (might not be utf-8): {e}"

    with open(TEMP_FUZZIL_PATH, "wb") as f:
        f.write(decoded_program)

    return lift_fuzzil_to_js(TEMP_FUZZIL_PATH)


@tool
def db_list_fuzzers() -> str:
    """
    Lists all registered fuzzer instances from the database.

    Returns:
        str: A JSON string containing a list of fuzzers (id, name, engine, status).
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

        query = "SELECT fuzzer_id, fuzzer_name, engine_type, status, created_at FROM main ORDER BY fuzzer_id ASC"
        
        cursor.execute(query)
        rows = cursor.fetchall()
        result_json = json.dumps(rows, default=json_serial, indent=2)

        return result_json

    except psycopg2.Error as e:
        return f"Database error: {e}... maybe try again?"
    except Exception as e:
        return f"Unexpected error: {e}"
    finally:
        if conn:
            conn.close()


@tool
def db_get_crash_diversity(fuzzer_id: int) -> str:
    """
    Analyze diversity of crashes found (unique signals, locations, reproducibility).
    
    Args:
        fuzzer_id (int): The ID of the fuzzer instance to analyze.
    
    Returns:
        str: A JSON string containing crash diversity metrics.
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
        
        query = """
        WITH crash_stats AS (
            SELECT 
                COUNT(*) as total_crashes,
                COUNT(DISTINCT e.signal_code) as unique_signals,
                COUNT(DISTINCT ca.crash_type) as unique_crash_types,
                COUNT(DISTINCT ca.crash_location) as unique_crash_locations,
                COUNT(CASE WHEN ca.is_reproducible = TRUE THEN 1 END) as reproducible_count,
                COUNT(CASE WHEN ca.is_reproducible = FALSE THEN 1 END) as non_reproducible_count
            FROM execution e
            JOIN program p ON e.program_hash = p.program_hash
            JOIN execution_outcome eo ON e.execution_outcome_id = eo.id
            LEFT JOIN crash_analysis ca ON e.execution_id = ca.execution_id
            WHERE p.fuzzer_id = %s AND eo.outcome = 'Crashed'
        ),
        signal_breakdown AS (
            SELECT 
                CASE 
                    WHEN e.signal_code = 11 THEN 'SIGSEGV'
                    WHEN e.signal_code = 6 THEN 'SIGABRT'
                    WHEN e.signal_code = 4 THEN 'SIGILL'
                    WHEN e.signal_code = 8 THEN 'SIGFPE'
                    WHEN e.signal_code = 3 THEN 'SIGQUIT'
                    WHEN e.signal_code IS NULL THEN 'NO_SIGNAL'
                    ELSE 'SIG' || e.signal_code::TEXT
                END as signal_name,
                e.signal_code,
                COUNT(*) as count
            FROM execution e
            JOIN program p ON e.program_hash = p.program_hash
            JOIN execution_outcome eo ON e.execution_outcome_id = eo.id
            WHERE p.fuzzer_id = %s AND eo.outcome = 'Crashed'
            GROUP BY e.signal_code
        )
        SELECT 
            cs.total_crashes,
            cs.unique_signals,
            cs.unique_crash_types,
            cs.unique_crash_locations,
            cs.reproducible_count,
            cs.non_reproducible_count,
            CASE 
                WHEN cs.total_crashes = 0 THEN 0
                ELSE ROUND((cs.reproducible_count::NUMERIC / cs.total_crashes::NUMERIC) * 100, 2)
            END as reproducible_percentage,
            ROUND((cs.unique_signals::NUMERIC + cs.unique_crash_types::NUMERIC + cs.unique_crash_locations::NUMERIC) / 3.0, 2) as crash_diversity_score,
            json_agg(
                json_build_object('signal_name', sb.signal_name, 'signal_code', sb.signal_code, 'count', sb.count)
                ORDER BY sb.count DESC
            ) as signal_breakdown
        FROM crash_stats cs, signal_breakdown sb
        GROUP BY cs.total_crashes, cs.unique_signals, cs.unique_crash_types, cs.unique_crash_locations, 
                 cs.reproducible_count, cs.non_reproducible_count
        """
        
        params = [fuzzer_id, fuzzer_id]
        cursor.execute(query, params)
        rows = cursor.fetchall()
        result_json = json.dumps(rows, default=json_serial, indent=2)
        return result_json
        
    except psycopg2.Error as e:
        return f"Database error: {e}... maybe try again?"
    except Exception as e:
        return f"Unexpected error: {e}"
    finally:
        if conn:
            conn.close()


#@tool
#def db_get_mutator_effectiveness(fuzzer_id: int, time_window_hours: int = 24) -> str:
#    """
#    Rank mutators by effectiveness: how often their mutations led to new coverage or crashes.
#    Uses execution.mutator_type_id since program.source_mutator is typically NULL.
#    
#    Args:
#        fuzzer_id (int): The ID of the fuzzer instance to analyze.
#        time_window_hours (int): Time window for analysis in hours (default: 24).
#    
#    Returns:
#        str: A JSON string containing per-mutator effectiveness metrics.
#    """
#    conn = None
#    try:
#        conn = psycopg2.connect(
#            host=POSTGRES_HOST,
#            port=POSTGRES_PORT,
#            dbname=POSTGRES_DB,
#            user=POSTGRES_USER,
#            password=POSTGRES_PASSWORD
#        )
#        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
#        
#        query = """
#        WITH mutator_execution_stats AS (
#            SELECT 
#                COALESCE(e.mutator_type_id, 'Unknown') as mutator_name,
#                COUNT(DISTINCT e.execution_id) as total_executions,
#                SUM(CASE WHEN eo.outcome = 'Crashed' THEN 1 ELSE 0 END) as crash_discoveries,
#                SUM(CASE WHEN eo.outcome = 'Succeeded' THEN 1 ELSE 0 END) as successful_executions,
#                MAX(e.coverage_total) as max_coverage,
#                AVG(e.coverage_total) as avg_coverage
#            FROM execution e
#            JOIN program p ON e.program_hash = p.program_hash
#            JOIN execution_outcome eo ON e.execution_outcome_id = eo.id
#            WHERE p.fuzzer_id = %s 
#                AND e.created_at > NOW() - INTERVAL '%s hours'
#            GROUP BY e.mutator_type_id
#        ),
#        new_coverage_stats AS (
#            SELECT 
#                COALESCE(e.mutator_type_id, 'Unknown') as mutator_name,
#                COUNT(DISTINCT e.execution_id) as new_coverage_executions
#            FROM execution e
#            JOIN program p ON e.program_hash = p.program_hash
#            JOIN coverage_detail cd ON e.execution_id = cd.execution_id
#            WHERE p.fuzzer_id = %s 
#                AND e.created_at > NOW() - INTERVAL '%s hours'
#                AND cd.is_new_edge = TRUE
#            GROUP BY e.mutator_type_id
#        )
#        SELECT 
#            mes.mutator_name,
#            mes.total_executions,
#            COALESCE(ncs.new_coverage_executions, 0) as new_coverage_discoveries,
#            mes.crash_discoveries,
#            mes.successful_executions,
#            ROUND(COALESCE(ncs.new_coverage_executions, 0)::NUMERIC / NULLIF(mes.total_executions, 0) * 100, 2) as new_coverage_discovery_rate,
#            ROUND(
#                (COALESCE(ncs.new_coverage_executions, 0)::NUMERIC + mes.crash_discoveries::NUMERIC) / NULLIF(mes.total_executions, 0) * 100,
#                2
#            ) as effectiveness_score,
#            ROUND(mes.avg_coverage::NUMERIC, 2) as avg_coverage,
#            ROUND(mes.max_coverage::NUMERIC, 2) as max_coverage
#        FROM mutator_execution_stats mes
#        LEFT JOIN new_coverage_stats ncs ON mes.mutator_name = ncs.mutator_name
#        ORDER BY effectiveness_score DESC NULLS LAST
#        """
#        
#        params = [fuzzer_id, time_window_hours, fuzzer_id, time_window_hours]
#        cursor.execute(query, params)
#        rows = cursor.fetchall()
#        result_json = json.dumps(rows, default=json_serial, indent=2)
#        return result_json
#        
#    except psycopg2.Error as e:
#        return f"Database error: {e}... maybe try again?"
#    except Exception as e:
#        return f"Unexpected error: {e}"
#    finally:
#        if conn:
#            conn.close()

# TODO: fix tracking for mutator types in corpus. the above 2 db_get_mutator_effectiveness functions are more so debugging "fixes" that don't address the underlying problem
@tool
def db_get_mutator_effectiveness(fuzzer_id: int, time_window_hours: int = 24) -> str:
    """
    Rank mutators by effectiveness: how often their mutations led to new coverage or crashes.
    
    Args:
        fuzzer_id (int): The ID of the fuzzer instance to analyze.
        time_window_hours (int): Time window for analysis in hours (default: 24).
    
    Returns:
        str: A JSON string containing per-mutator effectiveness metrics.
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
        
        query = """
        SELECT 
            p.source_mutator as mutator_name,
            COUNT(DISTINCT p.program_hash) as total_mutations,
            COUNT(DISTINCT e.execution_id) as total_executions,
            SUM(CASE WHEN cd.is_new_edge = TRUE THEN 1 ELSE 0 END) as new_coverage_discoveries,
            SUM(CASE WHEN eo.outcome = 'Crashed' THEN 1 ELSE 0 END) as crash_discoveries,
            COUNT(CASE WHEN eo.outcome = 'Succeeded' THEN 1 END) as successful_executions,
            ROUND(AVG(CASE WHEN cd.is_new_edge = TRUE THEN 1 ELSE 0 END)::NUMERIC * 100, 2) as new_coverage_discovery_rate,
            ROUND(
                (
                    SUM(CASE WHEN cd.is_new_edge = TRUE THEN 1 ELSE 0 END)::NUMERIC + 
                    SUM(CASE WHEN eo.outcome = 'Crashed' THEN 1 ELSE 0 END)::NUMERIC
                ) / NULLIF(COUNT(DISTINCT e.execution_id), 0) * 100,
                2
            ) as effectiveness_score
        FROM program p
        JOIN execution e ON p.program_hash = e.program_hash
        LEFT JOIN coverage_detail cd ON e.execution_id = cd.execution_id
        JOIN execution_outcome eo ON e.execution_outcome_id = eo.id
        WHERE p.fuzzer_id = %s 
            AND e.created_at > NOW() - INTERVAL '%s hours'
            AND p.source_mutator IS NOT NULL
        GROUP BY p.source_mutator
        ORDER BY effectiveness_score DESC NULLS LAST
        """
        
        params = [fuzzer_id, time_window_hours]
        cursor.execute(query, params)
        rows = cursor.fetchall()
        result_json = json.dumps(rows, default=json_serial, indent=2)
        return result_json
        
    except psycopg2.Error as e:
        return f"Database error: {e}... maybe try again?"
    except Exception as e:
        return f"Unexpected error: {e}"
    finally:
        if conn:
            conn.close()


@tool
def db_get_program_convergence(fuzzer_id: int, time_window_hours: int = 24, size_tolerance_bytes: int = 50) -> str:
    """
    Detect if corpus is converging to similar programs (genetic drift).
    Analyzes program size clustering and mutator diversity.
    
    Args:
        fuzzer_id (int): The ID of the fuzzer instance to analyze.
        time_window_hours (int): Time window for analysis in hours (default: 24).
        size_tolerance_bytes (int): Byte range for size clustering (default: 50).
    
    Returns:
        str: A JSON string containing convergence metrics and clustering analysis.
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
        
        query = """
        WITH program_stats AS (
            SELECT 
                COUNT(DISTINCT program_hash) as total_programs,
                COUNT(DISTINCT program_size) as unique_sizes,
                MIN(program_size) as min_size,
                MAX(program_size) as max_size,
                ROUND(AVG(program_size)::NUMERIC, 2) as avg_size,
                ROUND(STDDEV(program_size)::NUMERIC, 2) as size_stddev,
                COUNT(DISTINCT source_mutator) as unique_mutators
            FROM program
            WHERE fuzzer_id = %s AND created_at > NOW() - INTERVAL '%s hours'
        ),
        size_clustering AS (
            SELECT 
                ROUND((program_size / %s)::NUMERIC) * %s as size_bucket,
                COUNT(*) as programs_in_bucket,
                COUNT(DISTINCT source_mutator) as mutators_in_bucket
            FROM program
            WHERE fuzzer_id = %s AND created_at > NOW() - INTERVAL '%s hours'
            GROUP BY size_bucket
        ),
        mutator_distribution AS (
            SELECT 
                source_mutator,
                COUNT(*) as programs_created,
                ROUND((COUNT(*)::NUMERIC / SUM(COUNT(*)) OVER ())::NUMERIC * 100, 2) as percentage
            FROM program
            WHERE fuzzer_id = %s AND created_at > NOW() - INTERVAL '%s hours'
            GROUP BY source_mutator
            ORDER BY programs_created DESC
        )
        SELECT 
            ps.total_programs,
            ps.unique_sizes,
            ps.min_size,
            ps.max_size,
            ps.avg_size,
            ps.size_stddev,
            ps.unique_mutators,
            ROUND(
                (1.0 - (ps.unique_sizes::NUMERIC / NULLIF(ps.total_programs, 0)))::NUMERIC,
                3
            ) as size_convergence_score,
            ROUND(
                (1.0 - (ps.unique_mutators::NUMERIC / 10.0))::NUMERIC,
                3
            ) as mutator_diversity_score,
            json_agg(
                json_build_object(
                    'size_bucket', sc.size_bucket,
                    'programs_in_bucket', sc.programs_in_bucket,
                    'mutators_in_bucket', sc.mutators_in_bucket
                )
                ORDER BY sc.programs_in_bucket DESC
            ) FILTER (WHERE sc.programs_in_bucket > 0) as size_distribution,
            json_agg(
                json_build_object(
                    'mutator', md.source_mutator,
                    'programs_created', md.programs_created,
                    'percentage', md.percentage
                )
            ) FILTER (WHERE md.source_mutator IS NOT NULL) as mutator_distribution
        FROM program_stats ps, size_clustering sc, mutator_distribution md
        GROUP BY ps.total_programs, ps.unique_sizes, ps.min_size, ps.max_size, ps.avg_size, 
                 ps.size_stddev, ps.unique_mutators
        """
        
        params = [fuzzer_id, time_window_hours, size_tolerance_bytes, size_tolerance_bytes, 
                  fuzzer_id, time_window_hours, fuzzer_id, time_window_hours]
        cursor.execute(query, params)
        rows = cursor.fetchall()
        result_json = json.dumps(rows, default=json_serial, indent=2)
        return result_json
        
    except psycopg2.Error as e:
        return f"Database error: {e}... maybe try again?"
    except Exception as e:
        return f"Unexpected error: {e}"
    finally:
        if conn:
            conn.close()


@tool
def db_get_execution_outcome_distribution(fuzzer_id: int, time_window_hours: int = 24, sample_interval_minutes: int = 5) -> str:
    """
    Track ratio of Succeeded vs TimedOut vs Crashed vs Failed executions over time.
    
    Args:
        fuzzer_id (int): The ID of the fuzzer instance to analyze.
        time_window_hours (int): Time window for analysis in hours (default: 24).
        sample_interval_minutes (int): Interval for time-series aggregation in minutes (default: 5).
    
    Returns:
        str: A JSON string containing time-series outcome distribution.
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
        
        query = """
        WITH time_buckets AS (
            SELECT 
                DATE_TRUNC('minutes', e.created_at - (EXTRACT(MINUTE FROM e.created_at)::INT %% %s) * INTERVAL '1 minute') as time_bucket,
                eo.outcome,
                COUNT(*) as outcome_count
            FROM execution e
            JOIN program p ON e.program_hash = p.program_hash
            JOIN execution_outcome eo ON e.execution_outcome_id = eo.id
            WHERE p.fuzzer_id = %s AND e.created_at > NOW() - INTERVAL '%s hours'
            GROUP BY time_bucket, eo.outcome
        ),
        bucket_totals AS (
            SELECT 
                time_bucket,
                SUM(outcome_count) as total_executions
            FROM time_buckets
            GROUP BY time_bucket
        ),
        outcomes_pivot AS (
            SELECT 
                tb.time_bucket,
                tb.total_executions,
                SUM(CASE WHEN t.outcome = 'Succeeded' THEN t.outcome_count ELSE 0 END) as succeeded_count,
                SUM(CASE WHEN t.outcome = 'TimedOut' THEN t.outcome_count ELSE 0 END) as timed_out_count,
                SUM(CASE WHEN t.outcome = 'Crashed' THEN t.outcome_count ELSE 0 END) as crashed_count,
                SUM(CASE WHEN t.outcome = 'Failed' THEN t.outcome_count ELSE 0 END) as failed_count
            FROM bucket_totals tb
            LEFT JOIN time_buckets t ON tb.time_bucket = t.time_bucket
            GROUP BY tb.time_bucket, tb.total_executions
        )
        SELECT 
            time_bucket,
            total_executions,
            succeeded_count,
            timed_out_count,
            crashed_count,
            failed_count,
            ROUND((succeeded_count::NUMERIC / NULLIF(total_executions, 0))::NUMERIC * 100, 2) as succeeded_percentage,
            ROUND((timed_out_count::NUMERIC / NULLIF(total_executions, 0))::NUMERIC * 100, 2) as timed_out_percentage,
            ROUND((crashed_count::NUMERIC / NULLIF(total_executions, 0))::NUMERIC * 100, 2) as crashed_percentage,
            ROUND((failed_count::NUMERIC / NULLIF(total_executions, 0))::NUMERIC * 100, 2) as failed_percentage
        FROM outcomes_pivot
        ORDER BY time_bucket ASC
        """
        
        params = [sample_interval_minutes, fuzzer_id, time_window_hours]
        cursor.execute(query, params)
        rows = cursor.fetchall()
        result_json = json.dumps(rows, default=json_serial, indent=2)
        return result_json
        
    except psycopg2.Error as e:
        return f"Database error: {e}... maybe try again?"
    except Exception as e:
        return f"Unexpected error: {e}"
    finally:
        if conn:
            conn.close()

@tool
def db_get_program_coverage_mapping(fuzzer_id: int, limit: int = 50, min_coverage: float = None, sort_by: str = "coverage_total") -> str:
    """
    Maps programs to their coverage metrics and coverage increases.
    Shows the relationship between generated programs and their code coverage impact.
    
    Args:
        fuzzer_id (int): The ID of the fuzzer instance to analyze.
        limit (int): Maximum number of programs to return (default: 50).
        min_coverage (float): Optional filter for minimum coverage percentage (0-100).
        sort_by (str): Sort order - "coverage_total", "coverage_increase", or "execution_count" (default: "coverage_total").
    
    Returns:
        str: A JSON string containing program-to-coverage mappings with coverage increase tracking.
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
        
        valid_sort_fields = ["coverage_total", "coverage_increase", "execution_count"]
        if sort_by not in valid_sort_fields:
            return f"Invalid sort_by parameter. Must be one of: {', '.join(valid_sort_fields)}"
        
        query = """
        WITH program_coverage_stats AS (
            SELECT 
                p.program_hash,
                p.fuzzer_id,
                p.created_at as program_created_at,
                p.program_size,
                p.source_mutator,
                p.parent_program_hash,
                COUNT(DISTINCT e.execution_id) as execution_count,
                MAX(e.coverage_total) as coverage_total,
                AVG(e.coverage_total) as avg_coverage,
                SUM(CASE WHEN cd.is_new_edge = TRUE THEN 1 ELSE 0 END) as new_edges_discovered,
                COUNT(DISTINCT cd.edge_index) as unique_edges_covered
            FROM program p
            LEFT JOIN execution e ON p.program_hash = e.program_hash
            LEFT JOIN coverage_detail cd ON e.execution_id = cd.execution_id
            WHERE p.fuzzer_id = %s
            GROUP BY p.program_hash, p.fuzzer_id, p.created_at, p.program_size, p.source_mutator, p.parent_program_hash
        ),
        parent_coverage AS (
            SELECT 
                pcs.program_hash,
                COALESCE(parent_pcs.coverage_total, 0) as parent_coverage,
                COALESCE(pcs.coverage_total - parent_pcs.coverage_total, pcs.coverage_total) as coverage_increase
            FROM program_coverage_stats pcs
            LEFT JOIN program_coverage_stats parent_pcs ON pcs.parent_program_hash = parent_pcs.program_hash
        )
        SELECT 
            pcs.program_hash,
            pcs.program_created_at,
            pcs.program_size,
            pcs.source_mutator,
            pcs.parent_program_hash,
            pcs.execution_count,
            ROUND(pcs.coverage_total::NUMERIC, 2) as coverage_total,
            ROUND(pcs.avg_coverage::NUMERIC, 2) as avg_coverage,
            ROUND(pc.coverage_increase::NUMERIC, 2) as coverage_increase,
            ROUND(pc.parent_coverage::NUMERIC, 2) as parent_coverage,
            pcs.new_edges_discovered,
            pcs.unique_edges_covered
        FROM program_coverage_stats pcs
        LEFT JOIN parent_coverage pc ON pcs.program_hash = pc.program_hash
        WHERE pcs.fuzzer_id = %s
        """ 
        
        params = [fuzzer_id, fuzzer_id]
        
        if min_coverage is not None:
            query += " AND pcs.coverage_total >= %s"
            params.append(min_coverage)
        
        # Determine order based on sort_by
        if sort_by == "coverage_increase":
            query += " ORDER BY coverage_increase DESC NULLS LAST"
        elif sort_by == "execution_count":
            query += " ORDER BY execution_count DESC"
        else:  # coverage_total (default)
            query += " ORDER BY coverage_total DESC NULLS LAST"
        
        query += " LIMIT %s"
        params.append(limit)
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        result_json = json.dumps(rows, default=json_serial, indent=2)
        return result_json
        
    except psycopg2.Error as e:
        return f"Database error: {e}... maybe try again?"
    except Exception as e:
        return f"Unexpected error: {e}"
    finally:
        if conn:
            conn.close()
