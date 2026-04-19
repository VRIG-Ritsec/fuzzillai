#!/usr/bin/env bash
# Wrapper: lifts every row in program (and optionally generated_program_queue) to .js via FuzzILTool.
# See Scripts/dump-all-programs-to-js.py --help

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${SCRIPT_DIR}/dump-all-programs-to-js.py" "$@"
