#!/bin/bash

# stop-distributed.sh - Stop distributed fuzzing containers

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
MASTER_COMPOSE="${PROJECT_ROOT}/docker-compose.master.yml"
WORKER_COMPOSE="${PROJECT_ROOT}/docker-compose.workers.yml"

# Kill FuzzilliCli processes on the host
sudo pkill -f FuzzilliCli -QUIT

# Stop docker containers
docker compose -f "${MASTER_COMPOSE}" -f "${WORKER_COMPOSE}" down
