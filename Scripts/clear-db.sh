#!/bin/bash

# Clear PostgreSQL Database Script
# Clears Fuzzilli campaign data while preserving schema and lookup seeds.
# Materialized views are refreshed after truncate (they cannot be TRUNCATEd).

set +e

DB_CONTAINER="fuzzilli-postgres-master"
DB_NAME="fuzzilli_master"
DB_USER="fuzzilli"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

check_docker() {
    if ! command -v docker &> /dev/null; then
        echo -e "${RED}Error: Docker command not found. Please install Docker.${NC}"
        exit 1
    fi
}

check_container() {
    if ! docker ps --format "table {{.Names}}" | grep -q "$DB_CONTAINER"; then
        echo -e "${RED}Error: PostgreSQL container '$DB_CONTAINER' is not running${NC}"
        echo "Available containers:"
        docker ps --format "table {{.Names}}\t{{.Status}}"
        exit 1
    fi
}

run_query() {
    local query="$1"
    docker exec -i "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -c "$query" 2>&1
}

run_query_silent() {
    local query="$1"
    docker exec -i "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -c "$query" 2>/dev/null || true
}

psql_scalar() {
    docker exec -i "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -t -A -c "$1" 2>/dev/null | tr -d ' \r'
}

show_counts() {
    echo -e "${CYAN}Current database statistics:${NC}"
    local t
    for t in main program execution mutator_stats generated_program_queue; do
        local n
        n=$(psql_scalar "SELECT COUNT(*) FROM ${t};")
        if [ -n "$n" ]; then
            echo "  ${t}: ${n}"
        fi
    done
    local mvs
    mvs=$(docker exec -i "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -t -A -c \
        "SELECT matviewname FROM pg_matviews WHERE schemaname = 'public' ORDER BY 1;" 2>/dev/null)
    while IFS= read -r mv; do
        [ -z "$mv" ] && continue
        n=$(psql_scalar "SELECT COUNT(*) FROM \"${mv}\";")
        [ -n "$n" ] && echo "  ${mv} (matview): ${n}"
    done <<< "$mvs"
}

truncate_optional_legacy_tables() {
    echo -e "${CYAN}Truncating optional legacy tables (if present)...${NC}"
    run_query_silent "TRUNCATE TABLE coverage_detail CASCADE;"
    run_query_silent "TRUNCATE TABLE feedback_vector_detail CASCADE;"
    run_query_silent "TRUNCATE TABLE fuzzer_statistics CASCADE;"
}

truncate_core_tables() {
    echo -e "${CYAN}Clearing execution...${NC}"
    run_query_silent "TRUNCATE TABLE execution CASCADE;"

    echo -e "${CYAN}Clearing program...${NC}"
    run_query_silent "TRUNCATE TABLE program CASCADE;"

    echo -e "${CYAN}Clearing mutator_stats...${NC}"
    run_query_silent "TRUNCATE TABLE mutator_stats CASCADE;"

    echo -e "${CYAN}Clearing generated_program_queue...${NC}"
    run_query_silent "TRUNCATE TABLE generated_program_queue CASCADE;"

    echo -e "${CYAN}Clearing main (fuzzer instances)...${NC}"
    run_query_silent "TRUNCATE TABLE main CASCADE;"
}

refresh_all_materialized_views() {
    echo -e "${CYAN}Refreshing materialized views...${NC}"
    local mvs
    mvs=$(docker exec -i "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -t -A -c \
        "SELECT matviewname FROM pg_matviews WHERE schemaname = 'public' ORDER BY 1;" 2>/dev/null)
    local failed=0
    while IFS= read -r mv; do
        [ -z "$mv" ] && continue
        echo "  REFRESH MATERIALIZED VIEW ${mv}"
        if ! docker exec -i "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -c \
            "REFRESH MATERIALIZED VIEW \"${mv}\";" 2>&1; then
            echo -e "${RED}  Failed to refresh: ${mv}${NC}"
            failed=1
        fi
    done <<< "$mvs"
    if [ "$failed" -ne 0 ]; then
        echo -e "${YELLOW}One or more materialized view refreshes failed.${NC}"
    fi
}

reset_sequences() {
    echo -e "${CYAN}Resetting sequences...${NC}"
    run_query_silent "ALTER SEQUENCE IF EXISTS main_fuzzer_id_seq RESTART WITH 1;"
    run_query_silent "ALTER SEQUENCE IF EXISTS execution_execution_id_seq RESTART WITH 1;"
    run_query_silent "ALTER SEQUENCE IF EXISTS generated_program_queue_queue_id_seq RESTART WITH 1;"
    run_query_silent "ALTER SEQUENCE IF EXISTS feedback_vector_detail_id_seq RESTART WITH 1;"
    run_query_silent "ALTER SEQUENCE IF EXISTS coverage_detail_id_seq RESTART WITH 1;"
}

main() {
    echo -e "${CYAN}========================================${NC}"
    echo -e "${CYAN}  Fuzzilli Database Cleanup${NC}"
    echo -e "${CYAN}========================================${NC}"
    echo ""

    check_docker
    check_container

    show_counts
    echo ""

    fuzzer_tables=$(run_query "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename LIKE 'fuzzer-%' ORDER BY tablename;" | grep -v "tablename" | grep -v "^-" | grep -v "^$" | grep -v "^(" | awk 'NF>0 {print $1}' | tr '\n' ' ')

    if [ "${1:-}" != "--yes" ] && [ "${1:-}" != "-y" ]; then
        echo -e "${YELLOW}WARNING: This will delete ALL campaign data from the database!${NC}"
        echo -e "${YELLOW}The following will be cleared:${NC}"
        echo "  - Fuzzer instances (main), programs, executions"
        echo "  - Mutator stats and generated program queue"
        echo "  - Optional legacy tables if present (coverage_detail, feedback_vector_detail, fuzzer_statistics)"
        echo "  - Materialized views will be refreshed to match empty base tables"
        if [ -n "$fuzzer_tables" ]; then
            echo -e "${YELLOW}  - Fuzzer-X tables (will be DROPPED):${NC}"
            for table in $fuzzer_tables; do
                echo "    - $table"
            done
        fi
        echo ""
        echo -e "${YELLOW}Preserved:${NC}"
        echo "  - Schema, indexes, materialized view definitions"
        echo "  - Lookup tables (mutator_type, execution_outcome)"
        echo ""
        read -p "Are you sure you want to continue? (yes/no): " confirm
        if [ "$confirm" != "yes" ]; then
            echo -e "${GREEN}Operation cancelled.${NC}"
            exit 0
        fi
    fi

    echo -e "${YELLOW}Clearing database...${NC}"
    echo ""

    truncate_optional_legacy_tables
    truncate_core_tables

    if [ -n "$fuzzer_tables" ]; then
        echo -e "${CYAN}Dropping fuzzer-X tables...${NC}"
        for table in $fuzzer_tables; do
            if [ -n "$table" ]; then
                echo "  Dropping table: $table"
                run_query_silent "DROP TABLE IF EXISTS \"$table\" CASCADE;"
            fi
        done
    else
        echo -e "${CYAN}No fuzzer-X tables found to drop.${NC}"
    fi

    reset_sequences
    refresh_all_materialized_views

    echo ""
    echo -e "${GREEN}Database cleared successfully!${NC}"
    echo ""

    show_counts
    echo ""

    echo -e "${CYAN}========================================${NC}"
    echo -e "${CYAN}  Cleanup Complete${NC}"
    echo -e "${CYAN}========================================${NC}"
}

case "${1:-}" in
    "help"|"-h"|"--help")
        echo "Usage: $0 [--yes|-y]"
        echo ""
        echo "Clears campaign data from the Fuzzilli PostgreSQL database."
        echo ""
        echo "Options:"
        echo "  --yes, -y    Skip confirmation prompt"
        echo "  help, -h     Show this help message"
        echo ""
        echo "This script will:"
        echo "  - Truncate main, program, execution, mutator_stats, generated_program_queue"
        echo "  - Optionally truncate legacy tables if they exist"
        echo "  - Drop fuzzer-* tables if present"
        echo "  - Reset serial sequences and refresh all materialized views"
        echo "  - Preserve mutator_type, execution_outcome, and schema"
        echo ""
        echo "Set DB_CONTAINER if your Postgres container name differs."
        ;;
    "")
        main
        ;;
    "--yes"|"-y")
        main "$1"
        ;;
    *)
        echo "Unknown option: $1"
        echo "Use '$0 help' for usage information"
        exit 1
        ;;
esac
