#!/bin/bash
# Binary-search / delta-debug minimize d8 flags needed to reproduce a crash.
# Usage: ./Scripts/minimize-flags.sh [crash.js] [d8_path]
#   crash.js: path to crash reproducer (default: ./Scripts/crash_repro.js)
#   d8_path: path to d8 binary (default: V8 fuzzbuild or D8_PATH env)

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CRASH_JS="${1:-$SCRIPT_DIR/crash_repro.js}"
D8="${2:-${D8_PATH:-/mnt/vdc/v8_vrig/v8/out/fuzzbuild/d8}}"

FLAGS=(
  --expose-gc
  --expose-externalize-string
  --omit-quit
  --allow-natives-syntax
  --fuzzing
  --jit-fuzzing
  --future
  --harmony
  --experimental-fuzzing
  --js-staging
  --wasm-staging
  --wasm-fast-api
  --expose-fast-api
  --experimental-wasm-rab-integration
  --wasm-test-streaming
)

if [ ! -f "$CRASH_JS" ]; then
  echo "Creating crash reproducer at $CRASH_JS"
  mkdir -p "$(dirname "$CRASH_JS")"
  cat > "$CRASH_JS" << 'CRASH_EOF'
async function* f0(a1, a2, a3) {
    function F4(a6, a7) {
        if (!new.target) { throw 'must be called with new'; }
    }
    function F8(a10, a11, a12) {
        if (!new.target) { throw 'must be called with new'; }
        try { this(F4); } catch (e) {}
    }
    return a3;
}
CRASH_EOF
fi

if [ ! -x "$D8" ]; then
  echo "Error: d8 not found or not executable: $D8"
  exit 1
fi

crashes() {
  local out
  out=$("$D8" "$@" "$CRASH_JS" 2>&1)
  local code=$?
  if echo "$out" | grep -q "Bytecode mismatch"; then
    return 0
  fi
  [ $code -eq 134 ] || [ $code -eq 6 ] || [ $code -eq 139 ]
}

echo "D8: $D8"
echo "Crash script: $CRASH_JS"
echo "Full flags (${#FLAGS[@]}): ${FLAGS[*]}"
echo ""

if ! crashes "${FLAGS[@]}"; then
  echo "Error: crash does not reproduce with full flags. Cannot minimize."
  exit 1
fi
echo "Crash reproduces with full flags."
echo ""

# Delta debugging: try removing each flag; if still crashes, drop it
MINIMAL=("${FLAGS[@]}")
changed=1
while [ $changed -eq 1 ]; do
  changed=0
  for i in "${!MINIMAL[@]}"; do
    trial=()
    for j in "${!MINIMAL[@]}"; do
      [ $j -ne $i ] && trial+=("${MINIMAL[$j]}")
    done
    if crashes "${trial[@]}"; then
      echo "Dropped: ${MINIMAL[$i]}"
      unset 'MINIMAL[i]'
      MINIMAL=("${MINIMAL[@]}")
      changed=1
      break
    fi
  done
done

echo ""
echo "Minimal flags (${#MINIMAL[@]}):"
echo "  ${MINIMAL[*]}"
echo ""
echo "Run: $D8 ${MINIMAL[*]} $CRASH_JS"
