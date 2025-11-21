#!/bin/bash

# Fuzzilli Fuzzer Statistics Script
# Shows comprehensive statistics including highest coverage and per-fuzzer information
# Usage: ./Scripts/fuzzer-stats.sh
#
# Environment variables (optional, for remote PostgreSQL):
#   - POSTGRES_HOST: Remote PostgreSQL host/IP (if set, connects to remote instead of local container)
#   - POSTGRES_PORT: PostgreSQL port (default: 5432)
#   - POSTGRES_DB: Database name (default: fuzzilli_master)
#   - POSTGRES_USER: Database user (default: fuzzilli)
#   - POSTGRES_PASSWORD: PostgreSQL password (default: fuzzilli123)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [ -f "${PROJECT_ROOT}/.env" ]; then
    source "${PROJECT_ROOT}/.env"
elif [ -f "${PROJECT_ROOT}/env.distributed" ]; then
    source "${PROJECT_ROOT}/env.distributed"
fi

DB_CONTAINER=${DB_CONTAINER:-"fuzzilli-postgres-master"}
DB_NAME=${POSTGRES_DB:-"fuzzilli_master"}
DB_USER=${POSTGRES_USER:-"fuzzilli"}
DB_PASSWORD=${POSTGRES_PASSWORD:-"fuzzilli123"}

POSTGRES_HOST=${POSTGRES_HOST:-}
POSTGRES_PORT=${POSTGRES_PORT:-5432}

USE_REMOTE_DB=false
if [ -n "$POSTGRES_HOST" ]; then
    USE_REMOTE_DB=true
fi

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

check_docker() {
    if [ "$USE_REMOTE_DB" = false ]; then
        if ! command -v docker &> /dev/null; then
            echo -e "${RED}Error: Docker command not found. Please install Docker.${NC}"
            exit 1
        fi
    fi
}

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

check_database() {
    if [ "$USE_REMOTE_DB" = true ]; then
        export PGPASSWORD="${DB_PASSWORD}"
        if ! psql -h "${POSTGRES_HOST}" -p "${POSTGRES_PORT}" -U "${DB_USER}" -d "${DB_NAME}" -c "SELECT 1" > /dev/null 2>&1; then
            unset PGPASSWORD
            echo -e "${RED}Error: Cannot connect to remote PostgreSQL at ${POSTGRES_HOST}:${POSTGRES_PORT}${NC}"
            echo "  Please verify connection settings and network access"
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

run_query() {
    local query="$1"
    local result
    local exit_code
    
    if [ "$USE_REMOTE_DB" = true ]; then
        export PGPASSWORD="${DB_PASSWORD}"
        result=$(psql -h "${POSTGRES_HOST}" -p "${POSTGRES_PORT}" -U "${DB_USER}" -d "${DB_NAME}" -t -A -F'|' -c "$query" 2>&1)
        exit_code=$?
        unset PGPASSWORD
    else
        result=$(docker exec -i "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -t -A -F'|' -c "$query" 2>&1)
        exit_code=$?
    fi
    
    if [ $exit_code -ne 0 ]; then
        if echo "$result" | grep -qiE "(error|does not exist|relation|syntax|permission denied|connection|authentication|could not connect)"; then
            if ! echo "$result" | grep -qiE "(warning|notice|no rows)"; then
                echo "Query error: $result" >&2
            fi
        fi
        echo ""
        return 1
    fi
    
    echo "$result"
    return 0
}

format_number() {
    printf "%'d" "$1" 2>/dev/null || echo "$1"
}

format_decimal() {
    printf "%.2f" "$1" 2>/dev/null || echo "$1"
}

main() {
    echo -e "${CYAN}========================================${NC}"
    echo -e "${CYAN}  Fuzzilli Fuzzer Statistics${NC}"
    echo -e "${CYAN}========================================${NC}"
    echo ""
    
    check_docker
    check_psql
    check_database
    
    all_data=$(run_query "
        WITH global AS (
            SELECT 
                COALESCE(MAX(highest_coverage_pct), 0) as highest_coverage,
                COALESCE(SUM(total_programs), 0) as total_programs,
                COALESCE(SUM(total_executions), 0) as total_executions,
                COALESCE(SUM(total_crashes), 0) as total_crashes,
                COALESCE(MAX(active_fuzzers), 0) as active_fuzzers
            FROM global_statistics
        ),
        fuzzers AS (
            SELECT 
                '##FUZZER##' as marker,
                fuzzer_id::text as col1,
                fuzzer_name as col2,
                status as col3,
                COALESCE(execs_per_second, 0)::text as col4,
                COALESCE(programs_count, 0)::text as col5,
                COALESCE(executions_count, 0)::text as col6,
                COALESCE(crash_count, 0)::text as col7,
                COALESCE(highest_coverage_pct, 0)::text as col8,
                NULL as col9,
                NULL as col10,
                NULL as col11
            FROM fuzzer_performance_summary
            ORDER BY fuzzer_id
        ),
        crashes AS (
            SELECT 
                '##CRASH##' as marker,
                fuzzer_id::text as col1,
                fuzzer_name as col2,
                NULL as col3,
                NULL as col4,
                NULL as col5,
                NULL as col6,
                NULL as col7,
                NULL as col8,
                signal_code as col9,
                signal_name as col10,
                crash_count::text as col11
            FROM crash_by_signal
            ORDER BY fuzzer_id, crash_count DESC
        )
        SELECT '##GLOBAL##' as marker, 
               highest_coverage::text, total_programs::text, total_executions::text, 
               total_crashes::text, active_fuzzers::text,
               NULL, NULL, NULL, NULL, NULL, NULL, NULL
        FROM global
        UNION ALL
        SELECT marker, NULL, NULL, NULL, NULL, NULL, col1, col2, col3, col4, col5, col6, col7
        FROM (SELECT marker, col1, col2, col3, col4, col5, col6, col7, col8 FROM fuzzers) f
        UNION ALL
        SELECT marker, NULL, NULL, NULL, NULL, NULL, col1, col2, col9, col10, col11, NULL, NULL
        FROM crashes;
    ")
    
    if [ -z "$all_data" ]; then
        echo -e "${RED}Error: Failed to retrieve data from database${NC}"
        exit 1
    fi
    
    echo -e "${GREEN}=== Highest Coverage (Overall) ===${NC}"
    global_section=$(echo "$all_data" | grep "^##GLOBAL##" | head -1)
    
    if [ -n "$global_section" ]; then
        IFS='|' read -r marker highest_coverage total_programs total_executions total_crashes active_fuzzers rest <<< "$global_section"
        
        if [ -n "$highest_coverage" ] && [ "$highest_coverage" != "0" ]; then
            echo -e "  ${YELLOW}Highest Coverage:${NC} ${GREEN}$(format_decimal "$highest_coverage")%${NC}"
        else
            echo -e "  ${YELLOW}Highest Coverage:${NC} ${RED}No coverage data available${NC}"
        fi
        echo ""
        
        echo -e "${GREEN}=== Global Statistics ===${NC}"
        echo -e "  ${YELLOW}Total Programs:${NC} $(format_number "$total_programs")"
        echo -e "  ${YELLOW}Total Executions:${NC} $(format_number "$total_executions")"
        echo -e "  ${YELLOW}Total Crashes:${NC} ${RED}$(format_number "$total_crashes")${NC}"
        echo -e "  ${YELLOW}Active Fuzzers:${NC} $(format_number "$active_fuzzers")"
    else
        echo -e "  ${YELLOW}No global statistics available${NC}"
    fi
    echo ""
    
    echo -e "${GREEN}=== Per-Fuzzer Performance Summary ===${NC}"
    echo ""
    printf "%-6s %-20s %-10s %-12s %-12s %-12s %-10s %-15s\n" \
        "ID" "Name" "Status" "Execs/s" "Programs" "Executions" "Crashes" "Coverage %"
    echo "------------------------------------------------------------------------------------------------------"
    
    fuzzer_data=$(echo "$all_data" | grep "^##FUZZER##")
    
    if [ -z "$fuzzer_data" ]; then
        echo -e "${YELLOW}No fuzzer data available${NC}"
    else
        while IFS='|' read -r marker _ _ _ _ _ fuzzer_id fuzzer_name status execs_per_sec programs executions crashes; do
            if [ -z "$fuzzer_id" ]; then
                continue
            fi
            
            execs_formatted=$(printf "%.2f" "$execs_per_sec" 2>/dev/null || echo "0.00")
            
            if [ "$status" = "active" ]; then
                status_color="${GREEN}"
            else
                status_color="${RED}"
            fi
            
            printf "%-6s %-20s ${status_color}%-10s${NC} %-12s %-12s %-12s ${RED}%-10s${NC}\n" \
                "$fuzzer_id" "$fuzzer_name" "$status" "$execs_formatted" \
                "$(format_number "$programs")" "$(format_number "$executions")" "$(format_number "$crashes")"
        done <<< "$fuzzer_data"
    fi
    echo ""
    
    echo -e "${GREEN}=== Crash Breakdown by Signal (Per Fuzzer) ===${NC}"
    echo ""
    
    crash_data=$(echo "$all_data" | grep "^##CRASH##")
    
    if [ -z "$crash_data" ]; then
        echo -e "${YELLOW}No crash data available${NC}"
    else
        current_fuzzer=""
        while IFS='|' read -r marker _ _ _ _ _ fuzzer_id fuzzer_name signal_code signal_name crash_count rest; do
            if [ -z "$fuzzer_id" ]; then
                continue
            fi
            
            if [ "$current_fuzzer" != "$fuzzer_id" ]; then
                if [ -n "$current_fuzzer" ]; then
                    echo ""
                fi
                echo -e "${CYAN}Fuzzer ${fuzzer_id} (${fuzzer_name}):${NC}"
                current_fuzzer="$fuzzer_id"
            fi
            
            printf "  ${YELLOW}%-15s${NC} (Signal %-3s): ${RED}%s${NC} crashes\n" \
                "$signal_name" "${signal_code:-N/A}" "$(format_number "$crash_count")"
        done <<< "$crash_data"
    fi
    echo ""
    
    echo -e "${CYAN}========================================${NC}"
    echo -e "${CYAN}  Statistics Complete${NC}"
    echo -e "${CYAN}========================================${NC}"
}

case "${1:-}" in
    "help"|"-h"|"--help")
        echo "Usage: $0"
        echo ""
        echo "Shows comprehensive fuzzer statistics including:"
        echo "  - Highest coverage (overall)"
        echo "  - Global statistics"
        echo "  - Per-fuzzer information"
        echo "  - Crash breakdown by signal per fuzzer"
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
        echo "  Example: POSTGRES_HOST=192.168.1.100 $0"
        ;;
    "")
        main
        ;;
    *)
        echo "Unknown option: $1"
        echo "Use '$0 help' for usage information"
        exit 1
        ;;
esac