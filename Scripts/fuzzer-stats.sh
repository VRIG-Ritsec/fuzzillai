#!/bin/bash

# Distributed Fuzzer Statistics Monitor
# Displays real-time statistics from PostgreSQL database every 60 seconds

# Configuration
DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
DB_NAME="${DB_NAME:-fuzzilli_master}"
DB_USER="${DB_USER:-fuzzilli}"
DB_PASSWORD="${DB_PASSWORD:-fuzzilli123}"
REFRESH_INTERVAL=60

# Color codes for terminal output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# Function to execute SQL query
query_db() {
    PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -t -A -c "$1"
}

# Function to format duration from seconds
format_duration() {
    local total_seconds=$1
    local days=$((total_seconds / 86400))
    local hours=$(((total_seconds % 86400) / 3600))
    local minutes=$(((total_seconds % 3600) / 60))
    local seconds=$((total_seconds % 60))
    printf "%dd %dh %dm %ds" $days $hours $minutes $seconds
}

# Function to format large numbers with commas
format_number() {
    printf "%'d" "$1" 2>/dev/null || echo "$1"
}

# Function to display statistics
display_stats() {
    clear
    
    echo -e "${BOLD}${CYAN}================================================================================"
    echo -e "         Distributed Fuzzer Campaign Statistics"
    echo -e "================================================================================${NC}"
    echo ""
    
    # Refresh materialized views (do this in background to not block)
    query_db "REFRESH MATERIALIZED VIEW CONCURRENTLY fuzzer_dashboard;" &>/dev/null &
    query_db "REFRESH MATERIALIZED VIEW CONCURRENTLY mutator_effectiveness_aggregate;" &>/dev/null &
    
    # Campaign Overview
    echo -e "${BOLD}${GREEN}Campaign Overview:${NC}"
    
    local active_fuzzers=$(query_db "SELECT COUNT(*) FROM main WHERE status = 'active';")
    local oldest_fuzzer=$(query_db "SELECT EXTRACT(EPOCH FROM (NOW() - MIN(created_at)))::INT FROM main WHERE status = 'active';")
    local campaign_started=$(query_db "SELECT TO_CHAR(MIN(created_at), 'YYYY-MM-DD HH24:MI:SS UTC') FROM main WHERE status = 'active';")
    local last_activity=$(query_db "SELECT TO_CHAR(MAX(last_activity), 'YYYY-MM-DD HH24:MI:SS UTC') FROM main WHERE status = 'active';")
    
    echo -e "  Active Fuzzers:              ${BOLD}${GREEN}$active_fuzzers${NC}"
    echo -e "  Total Uptime:                ${BOLD}$(format_duration $oldest_fuzzer)${NC}"
    echo -e "  Campaign Started:            $campaign_started"
    echo -e "  Last Activity:               $last_activity"
    echo ""
    
    # Aggregate Corpus & Execution
    echo -e "${BOLD}${YELLOW}Aggregate Corpus & Execution:${NC}"
    
    local total_programs=$(query_db "SELECT COUNT(*) FROM program;")
    local corpus_size=$(query_db "SELECT COUNT(*) FROM fuzzer;")
    local total_executions=$(query_db "SELECT COUNT(*) FROM execution;")
    local valid_samples=$(query_db "SELECT COUNT(DISTINCT e.program_hash) FROM execution e WHERE e.execution_outcome_id = 3;")
    local interesting_samples=$(query_db "SELECT COUNT(DISTINCT e.program_hash) FROM execution e WHERE e.is_new_edge = TRUE;")
    
    if [ "$total_executions" -eq 0 ]; then
        echo -e "  ${YELLOW}⚠ Status: Building initial corpus (no executions yet)${NC}"
        echo -e "  Programs Generated:          $(format_number $total_programs)"
        echo -e "  Corpus Size:                 $(format_number $corpus_size)"
        echo -e "  ${CYAN}ℹ Fuzzing will start once corpus generation completes${NC}"
    else
        echo -e "  Total Samples:               $(format_number $total_executions)"
        echo -e "  Total Executions:            $(format_number $total_executions)"
        echo -e "  Unique Programs:             $(format_number $total_programs)"
        echo -e "  Corpus Size (all fuzzers):   $(format_number $corpus_size)"
        echo -e "  Interesting Samples Found:   $(format_number $interesting_samples)"
        echo -e "  Valid Samples Found:         $(format_number $valid_samples)"
    fi
    echo ""
    
    # Performance Metrics (only show if we have executions)
    if [ "$total_executions" -gt 0 ]; then
        echo -e "${BOLD}${BLUE}Performance Metrics:${NC}"
        
        local correctness_rate=$(query_db "SELECT ROUND((COUNT(*) FILTER (WHERE execution_outcome_id = 3)::NUMERIC / NULLIF(COUNT(*), 0) * 100), 2) FROM execution;")
        local timeout_rate=$(query_db "SELECT ROUND((COUNT(*) FILTER (WHERE execution_outcome_id = 4)::NUMERIC / NULLIF(COUNT(*), 0) * 100), 2) FROM execution;")
        local execs_last_hour=$(query_db "SELECT COUNT(*) FROM execution WHERE created_at > NOW() - INTERVAL '1 hour';")
        local avg_execs_per_sec=$(query_db "SELECT ROUND(COUNT(*)::NUMERIC / 3600.0, 2) FROM execution WHERE created_at > NOW() - INTERVAL '1 hour';")
        local peak_execs=$(query_db "SELECT COALESCE(ROUND(MAX(exec_rate), 2), 0) FROM (SELECT COUNT(*)::NUMERIC / 60.0 as exec_rate FROM execution WHERE created_at > NOW() - INTERVAL '1 hour' GROUP BY DATE_TRUNC('minute', created_at)) sub;")
        
        echo -e "  Overall Correctness Rate:    ${correctness_rate}%"
        echo -e "  Overall Timeout Rate:        ${timeout_rate}%"
        echo -e "  Avg. Execs/Second (all):     $avg_execs_per_sec"
        echo -e "  Peak Execs/Second:           $peak_execs"
        echo -e "  Execs Last Hour:             $(format_number $execs_last_hour)"
        echo ""
    fi
    
    # Coverage & Discovery (only show if we have executions)
    if [ "$total_executions" -gt 0 ]; then
        echo -e "${BOLD}${MAGENTA}Coverage & Discovery:${NC}"
        
        local max_coverage=$(query_db "SELECT COALESCE(ROUND(MAX(coverage_total), 2), 0) FROM execution;")
        local avg_coverage=$(query_db "SELECT COALESCE(ROUND(AVG(coverage_total), 2), 0) FROM execution WHERE coverage_total IS NOT NULL;")
        local total_edges=$(query_db "SELECT COALESCE(MAX(edges_found), 0) FROM execution;")
        local new_edges_hour=$(query_db "SELECT COUNT(*) FROM execution WHERE is_new_edge = TRUE AND created_at > NOW() - INTERVAL '1 hour';")
        
        echo -e "  Max Coverage Achieved:       ${BOLD}${max_coverage}%${NC}"
        echo -e "  Avg. Coverage (all fuzzers): ${avg_coverage}%"
        echo -e "  Total Unique Edges:          $(format_number $total_edges)"
        echo -e "  New Edges (last hour):       ${BOLD}${GREEN}$(format_number $new_edges_hour)${NC}"
        echo ""
    fi
    
    # Crashes & Issues (only show if we have executions)
    if [ "$total_executions" -gt 0 ]; then
        echo -e "${BOLD}${RED}Crashes & Issues:${NC}"
        
        local total_crashes=$(query_db "SELECT COUNT(*) FROM execution WHERE execution_outcome_id = 1;")
        local unique_crashes=$(query_db "SELECT COUNT(DISTINCT program_hash) FROM execution WHERE execution_outcome_id = 1;")
        local crashes_hour=$(query_db "SELECT COUNT(*) FROM execution WHERE execution_outcome_id = 1 AND created_at > NOW() - INTERVAL '1 hour';")
        local total_timeouts=$(query_db "SELECT COUNT(*) FROM execution WHERE execution_outcome_id = 4;")
        
        echo -e "  Total Crashes Found:         ${BOLD}${RED}$(format_number $total_crashes)${NC}"
        echo -e "  Unique Crash Hashes:         $(format_number $unique_crashes)"
        echo -e "  Crashes (last hour):         $(format_number $crashes_hour)"
        echo -e "  Total Timeouts:              $(format_number $total_timeouts)"
        echo ""
    fi
    
    # Program Statistics (only show if we have executions)
    if [ "$total_executions" -gt 0 ]; then
        echo -e "${BOLD}${CYAN}Program Statistics:${NC}"
        
        local avg_exec_time=$(query_db "SELECT COALESCE(ROUND(AVG(time_diff) * 1000), 0) FROM (SELECT EXTRACT(EPOCH FROM (created_at - LAG(created_at) OVER (ORDER BY created_at))) as time_diff FROM execution ORDER BY created_at DESC LIMIT 1000) sub WHERE time_diff IS NOT NULL;" 2>/dev/null || echo "0")
        local max_generation=$(query_db "SELECT COALESCE(MAX(generation), 0) FROM program_lineage;" 2>/dev/null || echo "0")
        
        echo -e "  Avg. Execution Time:         ${avg_exec_time}ms"
        echo -e "  Max Generation Depth:        $max_generation"
        echo ""
    fi
    
    # Top Performing Mutators (only show if we have data)
    local mutator_count=$(query_db "SELECT COUNT(*) FROM mutator_effectiveness_aggregate WHERE total_interesting_samples > 0;" 2>/dev/null || echo "0")
    
    if [ "$mutator_count" -gt 0 ]; then
        echo -e "${BOLD}${YELLOW}Top Performing Mutators (by interesting samples):${NC}"
        
        query_db "
        SELECT 
            ROW_NUMBER() OVER (ORDER BY total_interesting_samples DESC) as rank,
            mutator_name,
            total_interesting_samples,
            ROUND((total_interesting_samples::NUMERIC / NULLIF(SUM(total_interesting_samples) OVER (), 0) * 100), 1) as percentage,
            ROUND(avg_correctness_rate, 1) as correctness
        FROM mutator_effectiveness_aggregate
        WHERE total_interesting_samples > 0
        ORDER BY total_interesting_samples DESC
        LIMIT 5;
        " | while IFS='|' read -r rank name samples pct correct; do
            if [ -n "$rank" ]; then
                printf "  %s. %-25s %6s samples (%5s%%)  correctness: %5s%%\n" "$rank" "$name" "$(format_number $samples)" "$pct" "$correct"
            fi
        done
        echo ""
    fi
    
    # Per-Fuzzer Breakdown
    echo -e "${BOLD}${GREEN}Per-Fuzzer Breakdown:${NC}"
    echo -e "  ┌──────────────────┬──────────┬──────────┬──────────┬──────────┬─────────┐"
    echo -e "  │ Fuzzer ID        │ Status   │ Execs    │ Crashes  │ Coverage │ Uptime  │"
    echo -e "  ├──────────────────┼──────────┼──────────┼──────────┼──────────┼─────────┤"
    
    query_db "
    SELECT 
        'fuzzer-' || m.fuzzer_id,
        m.status,
        COALESCE(COUNT(e.execution_id), 0) as execs,
        COALESCE(COUNT(e.execution_id) FILTER (WHERE e.execution_outcome_id = 1), 0) as crashes,
        COALESCE(ROUND(MAX(e.coverage_total), 2), 0) as max_cov,
        EXTRACT(EPOCH FROM (NOW() - m.created_at))::INT as uptime
    FROM main m
    LEFT JOIN program p ON m.fuzzer_id = p.fuzzer_id
    LEFT JOIN execution e ON p.program_hash = e.program_hash
    WHERE m.status = 'active'
    GROUP BY m.fuzzer_id, m.status, m.created_at
    ORDER BY max_cov DESC;
    " | while IFS='|' read -r id status execs crashes cov uptime; do
        if [ -n "$id" ]; then
            local uptime_fmt=$(format_duration $uptime | cut -d' ' -f1-2)
            printf "  │ %-16s │ %-8s │ %8s │ %8s │ %7s%% │ %-7s │\n" \
                "$id" "$status" "$(format_number $execs)" "$crashes" "$cov" "$uptime_fmt"
        fi
    done
    
    echo -e "  └──────────────────┴──────────┴──────────┴──────────┴──────────┴─────────┘"
    echo ""
    
    # Recent Activity (only show if we have executions)
    if [ "$total_executions" -gt 0 ]; then
        echo -e "${BOLD}${BLUE}Recent Activity (last 10 minutes):${NC}"
        
        local new_edges_10m=$(query_db "SELECT COUNT(*) FROM execution WHERE is_new_edge = TRUE AND created_at > NOW() - INTERVAL '10 minutes';")
        local interesting_10m=$(query_db "SELECT COUNT(DISTINCT program_hash) FROM execution WHERE is_new_edge = TRUE AND created_at > NOW() - INTERVAL '10 minutes';")
        local execs_10m=$(query_db "SELECT COUNT(*) FROM execution WHERE created_at > NOW() - INTERVAL '10 minutes';")
        local active_mutators=$(query_db "SELECT COUNT(DISTINCT mutator_type_id) FROM mutator_stats WHERE last_updated > NOW() - INTERVAL '10 minutes';" 2>/dev/null || echo "0")
        
        echo -e "  New edges discovered:        ${BOLD}${GREEN}$new_edges_10m${NC}"
        echo -e "  Interesting samples:         $(format_number $interesting_10m)"
        echo -e "  Executions:                  $(format_number $execs_10m)"
        echo -e "  Active mutators:             $active_mutators/10"
        echo ""
    fi
    
    # Database Status
    echo -e "${BOLD}${CYAN}Database Status:${NC}"
    
    local total_records=$(query_db "SELECT SUM(n_live_tup) FROM pg_stat_user_tables WHERE schemaname = 'public';")
    local db_size=$(query_db "SELECT pg_size_pretty(pg_database_size('$DB_NAME'));")
    local last_refresh=$(query_db "SELECT COALESCE(EXTRACT(EPOCH FROM (NOW() - MAX(refreshed_at)))::INT, 0) FROM fuzzer_dashboard;" 2>/dev/null || echo "N/A")
    
    echo -e "  Total Records:               $(format_number $total_records)"
    echo -e "  DB Size:                     $db_size"
    echo -e "  Last Stats Refresh:          ${last_refresh}s ago"
    echo -e "${BOLD}${CYAN}================================================================================${NC}"
    echo ""
    echo -e "${BOLD}Refreshing in $REFRESH_INTERVAL seconds... (Press Ctrl+C to exit)${NC}"
}

# Main loop
echo "Starting Distributed Fuzzer Statistics Monitor..."
echo "Connecting to PostgreSQL at $DB_HOST:$DB_PORT..."

# Test database connection
if ! PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -c "SELECT 1;" &>/dev/null; then
    echo -e "${RED}Error: Could not connect to database!${NC}"
    echo "Please check your database connection settings."
    exit 1
fi

echo "Database connection successful!"
sleep 2

# Main monitoring loop
while true; do
    display_stats
    sleep $REFRESH_INTERVAL
done