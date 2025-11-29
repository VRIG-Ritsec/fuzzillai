#!/bin/bash

# Fuzzilli Crash Extraction Script
# Extracts crash information from PostgreSQL database
# Usage: ./Scripts/extract-crashes.sh [options]
#
# Environment variables (optional, for remote PostgreSQL):
#   - POSTGRES_HOST: Remote PostgreSQL host/IP (if set, connects to remote instead of local container)
#   - POSTGRES_PORT: PostgreSQL port (default: 5432)
#   - POSTGRES_DB: Database name (default: fuzzilli_master)
#   - POSTGRES_USER: Database user (default: fuzzilli)
#   - POSTGRES_PASSWORD: PostgreSQL password (default: fuzzilli123)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Load environment variables if available
if [ -f "${PROJECT_ROOT}/.env" ]; then
    source "${PROJECT_ROOT}/.env"
elif [ -f "${PROJECT_ROOT}/env.distributed" ]; then
    source "${PROJECT_ROOT}/env.distributed"
fi

# Database configuration
DB_CONTAINER=${DB_CONTAINER:-"fuzzilli-postgres-master"}
DB_NAME=${POSTGRES_DB:-"fuzzilli_master"}
DB_USER=${POSTGRES_USER:-"fuzzilli"}
DB_PASSWORD=${POSTGRES_PASSWORD:-"fuzzilli123"}

POSTGRES_HOST=${POSTGRES_HOST:-}
POSTGRES_PORT=${POSTGRES_PORT:-5432}

# Output configuration
OUTPUT_DIR="${PROJECT_ROOT}/crashes"
OUTPUT_FORMAT="json"  # json, csv, or text
LIMIT=100
FUZZER_ID=""
INCLUDE_PROGRAM=false
UNIQUE_ONLY=true

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# Determine if using remote database
USE_REMOTE_DB=false
if [ -n "$POSTGRES_HOST" ]; then
    USE_REMOTE_DB=true
fi

# Check if Docker is available (only for local DB)
check_docker() {
    if [ "$USE_REMOTE_DB" = false ]; then
        if ! command -v docker &> /dev/null; then
            echo -e "${RED}Error: Docker command not found. Please install Docker.${NC}"
            exit 1
        fi
    fi
}

# Check if psql is available (only for remote DB)
check_psql() {
    if [ "$USE_REMOTE_DB" = true ]; then
        if ! command -v psql &> /dev/null; then
            echo -e "${RED}Error: psql command not found. Please install PostgreSQL client.${NC}"
            echo "  On Ubuntu/Debian: sudo apt-get install postgresql-client"
            echo "  On RHEL/CentOS: sudo yum install postgresql"
            exit 1
        fi
    fi
}

# Check database connection
check_database() {
    if [ "$USE_REMOTE_DB" = true ]; then
        export PGPASSWORD="${DB_PASSWORD}"
        if ! psql -h "${POSTGRES_HOST}" -p "${POSTGRES_PORT}" -U "${DB_USER}" -d "${DB_NAME}" -c "SELECT 1" > /dev/null 2>&1; then
            unset PGPASSWORD
            echo -e "${RED}Error: Cannot connect to remote PostgreSQL at ${POSTGRES_HOST}:${POSTGRES_PORT}${NC}"
            exit 1
        fi
        unset PGPASSWORD
    else
        if ! docker ps --format "table {{.Names}}" | grep -q "$DB_CONTAINER"; then
            echo -e "${RED}Error: PostgreSQL container '$DB_CONTAINER' is not running${NC}"
            echo "Available containers:"
            docker ps --format "table {{.Names}}\t{{.Status}}"
            exit 1
        fi
    fi
}

# Run a query
run_query() {
    local query="$1"
    local format="${2:-tuples}"  # tuples, csv, json
    local result
    local exit_code
    
    if [ "$USE_REMOTE_DB" = true ]; then
        export PGPASSWORD="${DB_PASSWORD}"
        case "$format" in
            "csv")
                result=$(psql -h "${POSTGRES_HOST}" -p "${POSTGRES_PORT}" -U "${DB_USER}" -d "${DB_NAME}" -c "$query" --csv 2>&1)
                ;;
            "json")
                result=$(psql -h "${POSTGRES_HOST}" -p "${POSTGRES_PORT}" -U "${DB_USER}" -d "${DB_NAME}" -t -A -c "SELECT json_agg(row_to_json(t)) FROM ($query) t;" 2>&1)
                ;;
            *)
                result=$(psql -h "${POSTGRES_HOST}" -p "${POSTGRES_PORT}" -U "${DB_USER}" -d "${DB_NAME}" -c "$query" 2>&1)
                ;;
        esac
        exit_code=$?
        unset PGPASSWORD
    else
        case "$format" in
            "csv")
                result=$(docker exec -i "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -c "$query" --csv 2>&1)
                ;;
            "json")
                result=$(docker exec -i "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -t -A -c "SELECT json_agg(row_to_json(t)) FROM ($query) t;" 2>&1)
                ;;
            *)
                result=$(docker exec -i "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -c "$query" 2>&1)
                ;;
        esac
        exit_code=$?
    fi
    
    if [ $exit_code -ne 0 ]; then
        echo -e "${RED}Query error: $result${NC}" >&2
        return 1
    fi
    
    echo "$result"
    return 0
}

# Extract crashes with all details
extract_crashes() {
    echo -e "${CYAN}=== Extracting Crashes from Database ===${NC}"
    echo ""
    
    # Build WHERE clause
    local where_clause="WHERE eo.outcome = 'Crashed'"
    if [ -n "$FUZZER_ID" ]; then
        where_clause="$where_clause AND p.fuzzer_id = $FUZZER_ID"
    fi
    
    # Build SELECT clause
    local program_field=""
    if [ "$INCLUDE_PROGRAM" = true ]; then
        program_field=", f.program_base64"
    fi
    
    # Build the query
    local query="
        SELECT 
            e.execution_id,
            e.program_hash,
            p.fuzzer_id,
            p.source_mutator,
            p.created_at as program_created_at,
            e.created_at as crash_time,
            e.coverage_total,
            e.edges_found,
            e.total_edges,
            e.is_new_edge,
            e.turbofan_optimization_bits,
            e.feedback_nexus_count,
            e.stdout,
            e.stderr,
            e.fuzzout,
            mt.name as mutator_name,
            mt.category as mutator_category
            $program_field
        FROM execution e
        JOIN program p ON e.program_hash = p.program_hash
        JOIN execution_outcome eo ON e.execution_outcome_id = eo.id
        LEFT JOIN mutator_type mt ON e.mutator_type_id = mt.id
        LEFT JOIN fuzzer f ON e.program_hash = f.program_hash
        $where_clause
        ORDER BY e.created_at DESC
        LIMIT $LIMIT
    "
    
    # Create output directory
    mkdir -p "$OUTPUT_DIR"
    
    # Get timestamp for filename
    local timestamp=$(date +"%Y%m%d_%H%M%S")
    
    case "$OUTPUT_FORMAT" in
        "json")
            local output_file="${OUTPUT_DIR}/crashes_${timestamp}.json"
            echo -e "${GREEN}Extracting crashes to JSON format...${NC}"
            run_query "$query" "json" > "$output_file"
            echo -e "${GREEN}✓ Crashes saved to: ${output_file}${NC}"
            ;;
        "csv")
            local output_file="${OUTPUT_DIR}/crashes_${timestamp}.csv"
            echo -e "${GREEN}Extracting crashes to CSV format...${NC}"
            run_query "$query" "csv" > "$output_file"
            echo -e "${GREEN}✓ Crashes saved to: ${output_file}${NC}"
            ;;
        "text")
            local output_file="${OUTPUT_DIR}/crashes_${timestamp}.txt"
            echo -e "${GREEN}Extracting crashes to text format...${NC}"
            run_query "$query" "tuples" > "$output_file"
            echo -e "${GREEN}✓ Crashes saved to: ${output_file}${NC}"
            ;;
    esac
    
    # Print summary
    local total_crashes=$(run_query "SELECT COUNT(*) FROM execution e JOIN execution_outcome eo ON e.execution_outcome_id = eo.id WHERE eo.outcome = 'Crashed'" "json" | grep -oP '\d+' | head -1)
    echo -e "${CYAN}Total crashes in database: ${total_crashes:-0}${NC}"
    echo -e "${CYAN}Crashes extracted: ${LIMIT}${NC}"
}

# Extract unique crashes (deduplicated by program hash)
extract_unique_crashes() {
    echo -e "${CYAN}=== Extracting Unique Crashes ===${NC}"
    echo ""
    
    # Build WHERE clause
    local where_clause=""
    if [ -n "$FUZZER_ID" ]; then
        where_clause="WHERE ca.fuzzer_id = $FUZZER_ID"
    fi
    
    # Use the crash_analysis materialized view for unique crashes
    local query="
        SELECT 
            ca.program_hash,
            ca.fuzzer_id,
            ca.crash_count,
            ca.first_crash,
            ca.last_crash,
            ca.mutators_involved,
            ca.max_coverage_before_crash,
            ca.found_new_edges,
            f.program_base64
        FROM crash_analysis ca
        LEFT JOIN fuzzer f ON ca.program_hash = f.program_hash
        $where_clause
        ORDER BY ca.crash_count DESC, ca.first_crash DESC
        LIMIT $LIMIT
    "
    
    mkdir -p "$OUTPUT_DIR"
    local timestamp=$(date +"%Y%m%d_%H%M%S")
    
    case "$OUTPUT_FORMAT" in
        "json")
            local output_file="${OUTPUT_DIR}/unique_crashes_${timestamp}.json"
            echo -e "${GREEN}Extracting unique crashes to JSON format...${NC}"
            run_query "$query" "json" > "$output_file"
            echo -e "${GREEN}✓ Unique crashes saved to: ${output_file}${NC}"
            ;;
        "csv")
            local output_file="${OUTPUT_DIR}/unique_crashes_${timestamp}.csv"
            echo -e "${GREEN}Extracting unique crashes to CSV format...${NC}"
            run_query "$query" "csv" > "$output_file"
            echo -e "${GREEN}✓ Unique crashes saved to: ${output_file}${NC}"
            ;;
        "text")
            local output_file="${OUTPUT_DIR}/unique_crashes_${timestamp}.txt"
            echo -e "${GREEN}Extracting unique crashes to text format...${NC}"
            run_query "$query" "tuples" > "$output_file"
            echo -e "${GREEN}✓ Unique crashes saved to: ${output_file}${NC}"
            ;;
    esac
}

# Show crash statistics
show_crash_stats() {
    echo -e "${CYAN}=== Crash Statistics ===${NC}"
    echo ""
    
    local stats_query="
        SELECT 
            COUNT(DISTINCT e.program_hash) as unique_crashes,
            COUNT(*) as total_crash_executions,
            COUNT(DISTINCT p.fuzzer_id) as fuzzers_with_crashes,
            MIN(e.created_at) as first_crash,
            MAX(e.created_at) as latest_crash,
            AVG(e.coverage_total) as avg_coverage_at_crash,
            COUNT(*) FILTER (WHERE e.is_new_edge = true) as crashes_with_new_edges
        FROM execution e
        JOIN program p ON e.program_hash = p.program_hash
        JOIN execution_outcome eo ON e.execution_outcome_id = eo.id
        WHERE eo.outcome = 'Crashed'
    "
    
    run_query "$stats_query" "tuples"
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -f|--format)
            OUTPUT_FORMAT="$2"
            shift 2
            ;;
        -l|--limit)
            LIMIT="$2"
            shift 2
            ;;
        -o|--output)
            OUTPUT_DIR="$2"
            shift 2
            ;;
        --fuzzer-id)
            FUZZER_ID="$2"
            shift 2
            ;;
        --include-program)
            INCLUDE_PROGRAM=true
            shift
            ;;
        --unique)
            UNIQUE_ONLY=true
            shift
            ;;
        --all)
            UNIQUE_ONLY=false
            shift
            ;;
        --stats)
            check_docker
            check_psql
            check_database
            show_crash_stats
            exit 0
            ;;
        -h|--help)
            echo "Usage: $0 [options]"
            echo ""
            echo "Options:"
            echo "  -f, --format FORMAT       Output format: json, csv, or text (default: json)"
            echo "  -l, --limit LIMIT         Maximum number of crashes to extract (default: 100)"
            echo "  -o, --output DIR          Output directory (default: ./crashes)"
            echo "  --fuzzer-id ID            Filter by fuzzer ID"
            echo "  --include-program         Include base64 encoded program in output"
            echo "  --unique                  Extract only unique crashes (default)"
            echo "  --all                     Extract all crash executions"
            echo "  --stats                   Show crash statistics only"
            echo "  -h, --help                Show this help message"
            echo ""
            echo "Database connection:"
            echo "  By default, connects to local Docker container 'fuzzilli-postgres-master'"
            echo ""
            echo "  For remote PostgreSQL, set environment variables:"
            echo "    POSTGRES_HOST - Remote PostgreSQL host/IP"
            echo "    POSTGRES_PORT - PostgreSQL port (default: 5432)"
            echo "    POSTGRES_DB - Database name (default: fuzzilli_master)"
            echo "    POSTGRES_USER - Database user (default: fuzzilli)"
            echo "    POSTGRES_PASSWORD - PostgreSQL password"
            echo ""
            echo "Examples:"
            echo "  $0 --format json --limit 50"
            echo "  $0 --unique --include-program -o /tmp/crashes"
            echo "  $0 --fuzzer-id 1 --all --format csv"
            echo "  POSTGRES_HOST=192.168.1.100 $0 --stats"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use '$0 --help' for usage information"
            exit 1
            ;;
    esac
done

# Main execution
main() {
    check_docker
    check_psql
    check_database
    
    echo -e "${GREEN}Fuzzilli Crash Extraction Tool${NC}"
    echo "================================="
    echo ""
    
    if [ "$UNIQUE_ONLY" = true ]; then
        extract_unique_crashes
    else
        extract_crashes
    fi
    
    echo ""
    echo -e "${GREEN}Extraction complete!${NC}"
}

main
