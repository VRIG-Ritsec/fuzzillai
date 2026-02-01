#!/bin/bash

# performance-analysis.sh - Quick performance analysis tool for Fuzzilli
# Usage: ./Scripts/performance-analysis.sh [command]
#
# Commands:
#   monitor    - Real-time system and fuzzing metrics
#   stats      - Database statistics dashboard
#   profile    - CPU profiling with flamegraph (requires running fuzzer)
#   database   - Database performance queries
#   containers - Container resource usage
#   all        - Run all analysis tools

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

show_usage() {
    echo -e "${CYAN}Performance Analysis Tool for Fuzzilli${NC}"
    echo ""
    echo "Usage: $0 [command]"
    echo ""
    echo "Commands:"
    echo "  monitor    - Real-time system and fuzzing metrics (interactive)"
    echo "  stats      - Database statistics dashboard (interactive)"
    echo "  profile    - CPU profiling with flamegraph (requires running fuzzer)"
    echo "  database   - Database performance queries"
    echo "  containers - Container resource usage"
    echo "  quick      - Quick performance snapshot"
    echo "  all        - Run all non-interactive analysis"
    echo ""
    echo "Examples:"
    echo "  $0 monitor      # Start real-time monitoring"
    echo "  $0 stats        # View statistics dashboard"
    echo "  $0 profile      # Profile CPU and generate flamegraph"
    echo "  $0 quick        # Quick snapshot of current performance"
}

run_monitor() {
    echo -e "${GREEN}Starting real-time performance monitor...${NC}"
    "${SCRIPT_DIR}/monitor-performance.sh"
}

run_stats() {
    echo -e "${GREEN}Starting statistics dashboard...${NC}"
    "${SCRIPT_DIR}/fuzzer-stats.sh"
}

run_profile() {
    echo -e "${GREEN}Starting CPU profiling...${NC}"
    
    # Check if perf is available
    if ! command -v perf &> /dev/null; then
        echo -e "${RED}Error: perf tool not found. Install with: sudo apt-get install linux-perf${NC}"
        exit 1
    fi
    
    # Check if perf can run (perf_event_paranoid check)
    if ! perf record --help &> /dev/null; then
        echo -e "${YELLOW}Warning: perf may require sudo privileges${NC}"
        echo -e "${CYAN}If you see 'perf_event_paranoid' errors, run with:${NC}"
        echo "  sudo $0 profile"
        echo ""
        echo -e "${CYAN}Or adjust perf_event_paranoid (temporary):${NC}"
        echo "  sudo sysctl -w kernel.perf_event_paranoid=-1"
        echo ""
    fi
    
    # Check if perf_fuzzilli.sh exists
    if [ -f "/home/aleksi/perf_fuzzilli.sh" ]; then
        # Make sure it's executable, or run with bash
        if [ -x "/home/aleksi/perf_fuzzilli.sh" ]; then
            /home/aleksi/perf_fuzzilli.sh
        else
            echo -e "${YELLOW}perf_fuzzilli.sh not executable, running with bash...${NC}"
            bash /home/aleksi/perf_fuzzilli.sh
        fi
    else
        echo -e "${YELLOW}perf_fuzzilli.sh not found. Creating profile...${NC}"
        
        # Find FuzzilliCli process
        FUZZILLI_PID=$(docker exec fuzzer-worker-1 pgrep -f FuzzilliCli 2>/dev/null | head -n 1)
        
        if [ -z "$FUZZILLI_PID" ]; then
            echo -e "${RED}Error: FuzzilliCli not running. Start fuzzers first.${NC}"
            exit 1
        fi
        
        echo "Profiling PID $FUZZILLI_PID for 8 minutes..."
        DURATION=1200
        
        # Note: perf needs to run on host, not in container
        echo -e "${YELLOW}Note: perf must run on host system. Use:${NC}"
        echo "  sudo perf record -F 99 --call-graph dwarf -p <PID> -o perf.data -- sleep $DURATION"
        echo "  perf script -i perf.data > out.perf"
        echo "  /home/aleksi/FlameGraph/stackcollapse-perf.pl out.perf | swift demangle > out.folded"
        echo "  /home/aleksi/FlameGraph/flamegraph.pl --title 'Fuzzilli Performance' out.folded > fuzzilli_flamegraph.svg"
    fi
}

run_database() {
    echo -e "${GREEN}Database Performance Analysis${NC}"
    echo ""
    
    DB_CONTAINER="fuzzilli-postgres-master"
    DB_NAME="fuzzilli_master"
    DB_USER="fuzzilli"
    
    if ! docker ps --format "{{.Names}}" | grep -q "^${DB_CONTAINER}$"; then
        echo -e "${RED}Error: Database container not running${NC}"
        exit 1
    fi
    
    echo -e "${CYAN}=== Database Size ===${NC}"
    docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -c "
        SELECT pg_size_pretty(pg_database_size('$DB_NAME')) as database_size;
    "
    
    echo ""
    echo -e "${CYAN}=== Active Connections ===${NC}"
    docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -c "
        SELECT count(*) as active_connections 
        FROM pg_stat_activity 
        WHERE datname = '$DB_NAME';
    "
    
    echo ""
    echo -e "${CYAN}=== Table Sizes ===${NC}"
    docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -c "
        SELECT 
            tablename,
            pg_size_pretty(pg_total_relation_size('public.'||tablename)) AS size
        FROM pg_tables
        WHERE schemaname = 'public'
        ORDER BY pg_total_relation_size('public.'||tablename) DESC
        LIMIT 10;
    "
    
    echo ""
    echo -e "${CYAN}=== Slow Queries (if enabled) ===${NC}"
    docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -c "
        SELECT 
            query,
            mean_exec_time,
            calls
        FROM pg_stat_statements
        ORDER BY mean_exec_time DESC
        LIMIT 5;
    " 2>/dev/null || echo "pg_stat_statements not enabled"
    
    echo ""
    echo -e "${CYAN}=== Index Usage ===${NC}"
    docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -c "
        SELECT 
            tablename,
            indexname,
            idx_scan
        FROM pg_stat_user_indexes
        WHERE idx_scan = 0
        ORDER BY pg_relation_size(indexrelid) DESC
        LIMIT 10;
    "
}

run_containers() {
    echo -e "${GREEN}Container Resource Usage${NC}"
    echo ""
    
    echo -e "${CYAN}=== All Containers ===${NC}"
    docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.BlockIO}}"
    
    echo ""
    echo -e "${CYAN}=== Fuzzer Workers Only ===${NC}"
    docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}" $(docker ps --format "{{.Names}}" | grep fuzzer-worker)
}

run_quick() {
    echo -e "${GREEN}Quick Performance Snapshot${NC}"
    echo ""
    
    # System resources
    echo -e "${CYAN}=== System Resources ===${NC}"
    CPU=$(top -bn1 | grep "Cpu(s)" | sed "s/.*, *\([0-9.]*\)%* id.*/\1/" | awk '{print 100 - $1}')
    MEM=$(free | grep Mem | awk '{printf "%.1f", ($3/$2) * 100.0}')
    echo "CPU Usage: ${CPU}%"
    echo "Memory Usage: ${MEM}%"
    echo ""
    
    # Container count
    echo -e "${CYAN}=== Containers ===${NC}"
    WORKER_COUNT=$(docker ps --format "{{.Names}}" | grep -c fuzzer-worker || echo "0")
    echo "Active Workers: $WORKER_COUNT"
    echo ""
    
    # Database stats
    DB_CONTAINER="fuzzilli-postgres-master"
    DB_NAME="fuzzilli_master"
    DB_USER="fuzzilli"
    
    if docker ps --format "{{.Names}}" | grep -q "^${DB_CONTAINER}$"; then
        echo -e "${CYAN}=== Database ===${NC}"
        EXECS_LAST_HOUR=$(docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -t -A -c "
            SELECT COUNT(*) FROM execution WHERE created_at > NOW() - INTERVAL '1 hour';
        " 2>/dev/null || echo "0")
        EXECS_PER_SEC=$(echo "scale=2; $EXECS_LAST_HOUR / 3600" | bc 2>/dev/null || echo "0")
        echo "Executions (last hour): $EXECS_LAST_HOUR"
        echo "Executions/sec: $EXECS_PER_SEC"
    fi
}

run_all() {
    echo -e "${GREEN}Running all performance analysis...${NC}"
    echo ""
    
    run_quick
    echo ""
    run_database
    echo ""
    run_containers
}

# Main
if [ $# -eq 0 ]; then
    show_usage
    exit 0
fi

COMMAND=$1
shift

case "$COMMAND" in
    monitor)
        run_monitor
        ;;
    stats)
        run_stats
        ;;
    profile)
        run_profile
        ;;
    database)
        run_database
        ;;
    containers)
        run_containers
        ;;
    quick)
        run_quick
        ;;
    all)
        run_all
        ;;
    *)
        echo -e "${RED}Unknown command: $COMMAND${NC}"
        echo ""
        show_usage
        exit 1
        ;;
esac
