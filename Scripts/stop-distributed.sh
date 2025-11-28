#!/bin/bash

# stop-distributed.sh - Stop distributed fuzzing containers
# Usage: ./Scripts/stop-distributed.sh [-v|--volumes]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
MASTER_COMPOSE="${PROJECT_ROOT}/docker-compose.master.yml"
WORKER_COMPOSE="${PROJECT_ROOT}/docker-compose.workers.yml"

REMOVE_VOLUMES=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -v|--volumes)
            REMOVE_VOLUMES=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [-v|--volumes]"
            exit 1
            ;;
    esac
done

if [ ! -f "${WORKER_COMPOSE}" ]; then
    echo "Error: ${WORKER_COMPOSE} not found."
    echo "Have you run start-distributed.sh?"
    exit 1
fi

# Check if we are using local postgres (indicated by dependency in workers compose)
USE_LOCAL_DB=false
if grep -q "postgres-master" "${WORKER_COMPOSE}"; then
    USE_LOCAL_DB=true
fi

echo "=========================================="
echo "Stopping Distributed Fuzzilli"
echo "=========================================="

COMPOSE_CMD="docker compose"
DOWN_ARGS=""

if [ "$REMOVE_VOLUMES" = true ]; then
    DOWN_ARGS="-v"
    echo "Note: Volumes will be removed."
fi

if [ "$USE_LOCAL_DB" = true ]; then
    echo "Detected local database configuration."
    echo "Stopping workers and master database..."
    $COMPOSE_CMD -f "${MASTER_COMPOSE}" -f "${WORKER_COMPOSE}" down $DOWN_ARGS
else
    echo "Detected remote database configuration."
    echo "Stopping workers..."
    $COMPOSE_CMD -f "${WORKER_COMPOSE}" down $DOWN_ARGS
fi

echo ""
echo "=========================================="
echo "Shutdown complete!"
echo "=========================================="
