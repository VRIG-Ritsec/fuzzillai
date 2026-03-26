#!/bin/bash
# Validate that extracted crashes are real V8 bugs (not fake/test crashes).
# Usage: ./Scripts/validate-crashes.sh [crash_dir]
#   crash_dir: directory with crashes_unique_*.json (default: ./crashes)

set -e
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CRASH_DIR="${1:-$PROJECT_ROOT/crashes}"
FUZZILTOOL="${FUZZILTOOL:-$PROJECT_ROOT/.build/debug/FuzzILTool}"
D8_PATH="${D8_PATH:-}"
POSTGRES_URL="${POSTGRES_URL:-postgresql://fuzzilli:fuzzilli123@localhost:5432/fuzzilli_master}"

echo "=== Crash Validation ==="
echo "Crash dir: $CRASH_DIR"
echo ""

if [ ! -d "$CRASH_DIR" ]; then
    echo "Error: Crash directory not found: $CRASH_DIR"
    exit 1
fi

JSON_FILE=$(ls "$CRASH_DIR"/crashes_unique_*.json 2>/dev/null | head -1)
if [ -z "$JSON_FILE" ]; then
    echo "Error: No crashes_unique_*.json found in $CRASH_DIR"
    echo "Run: ./Scripts/extract-crashes.py --unique --save-programs -o ./crashes"
    exit 1
fi

echo "Checking execution records for fake crashes..."
for hash in $(jq -r '.[].program_hash' "$JSON_FILE" 2>/dev/null); do
    echo ""
    echo "--- $hash ---"
    stdout=$(psql "$POSTGRES_URL" -t -A -c "SELECT stdout FROM execution e WHERE e.program_hash = '$hash' AND e.execution_outcome_id = 1 LIMIT 1;" 2>/dev/null || echo "")
    if echo "$stdout" | grep -q "Fake crash inserted"; then
        echo "FAKE: stdout contains 'Fake crash inserted' (from insert_fake_crash.py)"
    else
        echo "Real: No fake marker in execution record"
    fi
done

echo ""
echo "=== Reproduction steps ==="
echo "1. Decode and lift to JS:"
echo "   cd $CRASH_DIR/programs_*"
echo "   for f in *.b64; do base64 -d \"\$f\" > \"\${f%.b64}.fzil\"; done"
echo "   for f in *.fzil; do $FUZZILTOOL --liftToJS \"\$f\" > \"\${f%.fzil}.js\"; done"
echo "   (Run from project root or use: FUZZILTOOL=$PROJECT_ROOT/.build/debug/FuzzILTool)"
echo ""
echo "2. Run with d8 (use same flags as fuzzer):"
echo "   d8 --expose-gc --expose-externalize-string --omit-quit --allow-natives-syntax \\"
echo "      --fuzzing --jit-fuzzing --future --harmony --experimental-fuzzing \\"
echo "      --js-staging --wasm-staging --wasm-fast-api --expose-fast-api \\"
echo "      --experimental-wasm-rab-integration --wasm-test-streaming your_crash.js"
echo ""
echo "3. Real crash: non-zero exit (e.g. 134 for SIGABRT). Fake: may not reproduce."
