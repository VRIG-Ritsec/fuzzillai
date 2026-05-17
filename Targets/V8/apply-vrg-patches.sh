#!/bin/bash
# Re-apply VRIG Fuzzilli instrumentation patches to a V8 checkout.
#
# Upstream copies live under: .../v8_vrig/v8_patch/*.diff
# Some diffs do not apply cleanly to a moving V8 main (line drift, corrupt hunk
# in pipeline-cc.diff). This script applies the ones that are still clean, then
# prints what must be merged by hand if anything fails.
#
# Usage:
#   VRIG_PATCH_DIR=/path/to/v8_patch V8_SRC=/path/to/v8 ./apply-vrg-patches.sh
#
# After this script, ensure src/fuzzilli/cov.cc, src/compiler/pipeline.cc, and
# src/compiler/js-heap-broker.cc match the fuzzillai-maintained tree (see git
# diff in a known-good checkout).

set -euo pipefail

V8_SRC="${V8_SRC:-/mnt/vdc/v8_vrig/v8}"
PATCH_DIR="${VRIG_PATCH_DIR:-/mnt/vdc/v8_vrig/v8_patch}"

cd "$V8_SRC"

apply() {
  local f="$1"
  echo "Applying $(basename "$f")..."
  git apply "$f"
}

apply "$PATCH_DIR/opt-comp-info-h.diff"
apply "$PATCH_DIR/cov-h.diff"
apply "$PATCH_DIR/feedback-vector-cc.diff"
apply "$PATCH_DIR/maglev-graph-builder-h.diff"

if git apply --check "$PATCH_DIR/cov-cc.diff" 2>/dev/null; then
  apply "$PATCH_DIR/cov-cc.diff"
else
  echo "NOTE: cov-cc.diff did not apply (expected on newer V8). Merge cov.cc manually from a patched tree."
fi

if git apply --check "$PATCH_DIR/pipeline-cc.diff" 2>/dev/null; then
  apply "$PATCH_DIR/pipeline-cc.diff"
else
  echo "NOTE: pipeline-cc.diff often fails (last hunk line counts or upstream drift)."
  echo "      Instrument pipeline.cc from a known-good diff or fuzzillai history."
fi

if git apply --check "$PATCH_DIR/js-heap-broker-cc.diff" 2>/dev/null; then
  apply "$PATCH_DIR/js-heap-broker-cc.diff"
else
  echo "NOTE: js-heap-broker-cc.diff may need manual merge when signatures drift."
fi

echo "Done. Verify with: cd \"$V8_SRC\" && git diff --stat"
