#!/usr/bin/env bash
# Extract lifted JavaScript for a program (crash) hash from the Fuzzilli Postgres DB.
#
# Usage:
#   ./Scripts/extract-crash-js.sh <program_hash> [output.js]
#
# If output.js is omitted, writes to crashes/<program_hash>.js under the repo root.
#
# Environment (optional):
#   POSTGRES_URL   e.g. postgresql://user:pass@host:5432/dbname
#   DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD  (defaults match fuzzer-stats.sh)
#   FUZZILTOOL       path to FuzzILTool (default: <repo>/.build/debug/FuzzILTool)
#   VERIFY_CRASH=1   require at least one crashed execution for this hash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [ -f "${PROJECT_ROOT}/.env" ]; then
  # shellcheck source=/dev/null
  source "${PROJECT_ROOT}/.env"
elif [ -f "${PROJECT_ROOT}/env.distributed" ]; then
  # shellcheck source=/dev/null
  source "${PROJECT_ROOT}/env.distributed"
fi

DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
DB_NAME="${DB_NAME:-fuzzilli_master}"
DB_USER="${DB_USER:-fuzzilli}"
DB_PASSWORD="${DB_PASSWORD:-fuzzilli123}"

FUZZILTOOL="${FUZZILTOOL:-${PROJECT_ROOT}/.build/debug/FuzzILTool}"

usage() {
  echo "Usage: $0 <program_hash> [output.js]" >&2
  echo "  program_hash: 64-char hex sha256 from program / crash records" >&2
  echo "  output.js:    optional path (default: crashes/<program_hash>.js)" >&2
  exit 1
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  usage
fi

HASH="${1:-}"
OUTFILE="${2:-}"

if [ -z "$HASH" ]; then
  usage
fi

if ! [[ "$HASH" =~ ^[0-9a-fA-F]{64}$ ]]; then
  echo "Error: program_hash must be 64 hex characters, got: $HASH" >&2
  exit 1
fi

HASH_LC="$(echo "$HASH" | tr '[:upper:]' '[:lower:]')"

if [ ! -x "$FUZZILTOOL" ]; then
  echo "Error: FuzzILTool not found or not executable: $FUZZILTOOL" >&2
  echo "Set FUZZILTOOL or build: swift build" >&2
  exit 1
fi

run_psql() {
  local sql="$1"
  if [ -n "${POSTGRES_URL:-}" ]; then
    psql "$POSTGRES_URL" -t -A -c "$sql"
  else
    PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -t -A -c "$sql"
  fi
}

if [ "${VERIFY_CRASH:-0}" = "1" ]; then
  crash_count="$(run_psql "SELECT COUNT(*)::text FROM execution WHERE program_hash = '$HASH_LC' AND execution_outcome_id = 1;" | tr -d '[:space:]')"
  if [ "${crash_count:-0}" = "0" ]; then
    echo "Error: no crashed execution (outcome 1) for hash $HASH_LC (set VERIFY_CRASH=0 to skip)" >&2
    exit 1
  fi
fi

b64="$(run_psql "SELECT program_base64 FROM program WHERE program_hash = '$HASH_LC' LIMIT 1;" | tr -d '[:space:]')"

if [ -z "$b64" ]; then
  echo "Error: no program row for hash $HASH_LC" >&2
  exit 1
fi

WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/extract-crash-js.XXXXXX")"
cleanup() { rm -rf "$WORKDIR"; }
trap cleanup EXIT

echo "$b64" | tr -d '[:space:]' | base64 --decode > "${WORKDIR}/program.fzil"

if [ -z "$OUTFILE" ]; then
  mkdir -p "${PROJECT_ROOT}/crashes"
  OUTFILE="${PROJECT_ROOT}/crashes/${HASH_LC}.js"
fi

"$FUZZILTOOL" --liftToJS "${WORKDIR}/program.fzil" > "$OUTFILE"

echo "Wrote $OUTFILE"
