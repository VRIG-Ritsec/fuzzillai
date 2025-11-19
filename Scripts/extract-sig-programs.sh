#!/bin/bash

# Script to extract all programs that caused signals (crashes or sigchecks)
# Extracts programs to Scripts/sig_programs/ with naming: program-{execution_id}-signal-{signal_code}
#
# Usage:
#   Local: FUZZILTOOL=/path/to/FuzzILTool ./Scripts/extract-sig-programs.sh
#   Remote: FUZZILTOOL=/path/to/FuzzILTool POSTGRES_HOST=your-prod-server.com ./Scripts/extract-sig-programs.sh
#
# Required environment variables:
#   FUZZILTOOL - Path to FuzzILTool binary (required)
#
# Optional environment variables (same as start-distributed.sh):
#   POSTGRES_HOST - Remote PostgreSQL host/IP (required for remote)
#   POSTGRES_PORT - PostgreSQL port (default: 5432)
#   POSTGRES_DB - Database name (default: fuzzilli_master)
#   POSTGRES_USER - Database user (default: fuzzilli)
#   POSTGRES_PASSWORD - PostgreSQL password (default: fuzzilli123)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
OUTPUT_DIR="${SCRIPT_DIR}/sig_programs"

# Load environment variables
if [ -f "${PROJECT_ROOT}/.env" ]; then
    source "${PROJECT_ROOT}/.env"
elif [ -f "${PROJECT_ROOT}/env.distributed" ]; then
    source "${PROJECT_ROOT}/env.distributed"
fi

# Database connection parameters
DB_CONTAINER=${DB_CONTAINER:-"fuzzilli-postgres-master"}
DB_NAME=${POSTGRES_DB:-"fuzzilli_master"}
DB_USER=${POSTGRES_USER:-"fuzzilli"}
DB_PASSWORD=${POSTGRES_PASSWORD:-"fuzzilli123"}
POSTGRES_HOST=${POSTGRES_HOST:-}
POSTGRES_PORT=${POSTGRES_PORT:-5432}

# Determine if we're using remote or local postgres
USE_REMOTE_DB=false
if [ -n "$POSTGRES_HOST" ]; then
    USE_REMOTE_DB=true
fi

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# Check if FUZZILTOOL is set and valid
if [ -z "${FUZZILTOOL:-}" ]; then
    echo -e "${RED}Error: FUZZILTOOL environment variable is required${NC}"
    echo ""
    echo "Please set FUZZILTOOL to the path of the FuzzILTool binary:"
    echo "  export FUZZILTOOL=/path/to/FuzzILTool"
    echo "  ./Scripts/extract-sig-programs.sh"
    echo ""
    exit 1
fi

# Verify FUZZILTOOL exists and is executable
if [ ! -f "$FUZZILTOOL" ] && ! command -v "$FUZZILTOOL" &> /dev/null; then
    echo -e "${RED}Error: FUZZILTOOL not found at '$FUZZILTOOL'${NC}"
    echo ""
    echo "Please verify the path is correct:"
    echo "  export FUZZILTOOL=/path/to/FuzzILTool"
    echo ""
    exit 1
fi

# Function to convert .fzil to .js
convert_to_js() {
    local fzil_file="$1"
    local js_file="${fzil_file%.fzil}.js"
    
    # Run FuzzILTool to convert
    "$FUZZILTOOL" --liftToJS "$fzil_file" > "$js_file" 2>/dev/null
    
    if [ $? -eq 0 ] && [ -f "$js_file" ] && [ -s "$js_file" ]; then
        return 0
    else
        return 1
    fi
}

# Function to run a query
run_query() {
    local query="$1"
    if [ "$USE_REMOTE_DB" = true ]; then
        export PGPASSWORD="${DB_PASSWORD}"
        psql -h "${POSTGRES_HOST}" -p "${POSTGRES_PORT}" -U "${DB_USER}" -d "${DB_NAME}" -t -A -F'|' -c "$query" 2>&1
        local exit_code=$?
        unset PGPASSWORD
        return $exit_code
    else
        docker exec -i "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -t -A -F'|' -c "$query" 2>&1
        return $?
    fi
}

# Function to check database connection
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
            exit 1
        fi
    fi
}

echo -e "${CYAN}========================================${NC}"
echo -e "${CYAN}  Extract Signal Programs${NC}"
echo -e "${CYAN}========================================${NC}"
echo ""

check_database

# Create output directory
mkdir -p "${OUTPUT_DIR}"
echo -e "${YELLOW}Output directory: ${OUTPUT_DIR}${NC}"
echo ""

# Query to get all executions with signals (both Crashed and SigCheck outcomes)
echo -e "${BLUE}Querying database for programs with signals...${NC}"

QUERY="
SELECT 
    e.execution_id,
    e.signal_code,
    p.program_base64,
    eo.outcome,
    m.fuzzer_name
FROM execution e
JOIN program p ON e.program_hash = p.program_hash
JOIN main m ON p.fuzzer_id = m.fuzzer_id
JOIN execution_outcome eo ON e.execution_outcome_id = eo.id
WHERE eo.outcome IN ('Crashed', 'SigCheck') 
  AND e.signal_code IS NOT NULL
ORDER BY e.execution_id;
"

results=$(run_query "$QUERY")
if [ $? -ne 0 ]; then
    echo -e "${RED}Error: Failed to query database${NC}"
    echo "$results"
    exit 1
fi

if [ -z "$results" ] || [ "$results" = "" ]; then
    echo -e "${YELLOW}No programs with signals found${NC}"
    exit 0
fi

# Count total programs
total_count=$(echo "$results" | wc -l)
echo -e "${GREEN}Found ${total_count} programs with signals${NC}"
echo ""

# Process each result
count=0
while IFS='|' read -r execution_id signal_code program_base64 outcome fuzzer_name; do
    # Skip empty lines
    [ -z "$execution_id" ] && continue
    
    count=$((count + 1))
    
    # Create filename: program-{execution_id}-signal-{signal_code}
    filename="program-${execution_id}-signal-${signal_code}"
    fzil_filepath="${OUTPUT_DIR}/${filename}.fzil"
    js_filepath="${OUTPUT_DIR}/${filename}.js"
    
    # Decode base64 program and write to .fzil file
    if [ -n "$program_base64" ]; then
        echo "$program_base64" | base64 -d > "$fzil_filepath" 2>/dev/null
        if [ $? -eq 0 ]; then
            # Convert to JavaScript
            if convert_to_js "$fzil_filepath"; then
                # Remove .fzil file after successful conversion (keep only .js)
                rm -f "$fzil_filepath"
                echo -e "${GREEN}[${count}/${total_count}]${NC} Extracted: ${CYAN}${filename}.js${NC} (${outcome}, signal ${signal_code}, fuzzer: ${fuzzer_name})"
            else
                # Keep .fzil if conversion failed
                echo -e "${YELLOW}[${count}/${total_count}]${NC} Extracted (FuzzIL only): ${CYAN}${filename}.fzil${NC} (${outcome}, signal ${signal_code}, fuzzer: ${fuzzer_name})"
                echo -e "${YELLOW}  JavaScript conversion failed - FuzzILTool may not be available${NC}"
            fi
        else
            echo -e "${RED}[${count}/${total_count}]${NC} Failed to decode: ${filename}"
        fi
    else
        echo -e "${YELLOW}[${count}/${total_count}]${NC} Skipping: ${filename} (no program data)"
    fi
done <<< "$results"

echo ""
echo -e "${CYAN}========================================${NC}"
echo -e "${GREEN}Extraction complete!${NC}"
echo -e "${CYAN}========================================${NC}"
echo -e "Extracted ${count} programs to: ${OUTPUT_DIR}"
echo -e "Programs converted to JavaScript (.js files)"
echo ""

