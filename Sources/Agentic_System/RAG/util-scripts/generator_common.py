from __future__ import annotations

import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1].parent
EXTERNAL_ROOT = REPO_ROOT.parents[1]
REGRESSIONS_DIR = REPO_ROOT / "regressions"
REGRESSIONS_JSON = REPO_ROOT / "regressions.json"
TEMPLATES_JSON = REPO_ROOT / "templates" / "templates.json"
LIFTED_TEMPLATES_DIR = EXTERNAL_ROOT / "Corpus" / "lifted_templates"
SWIFT_TEMPLATE_SOURCES = [
    EXTERNAL_ROOT / "Sources" / "Fuzzilli" / "CodeGen" / "ProgramTemplates.swift",
    EXTERNAL_ROOT / "Sources" / "FuzzilliCli" / "Profiles" / "V8CommonProfile.swift",
]

D8_TRACE_FLAGS = [
    "--allow-natives-syntax",
    "--print-bytecode",
    "--print-maglev-code",
    "--print-maglev-graphs",
    "--maglev-print-feedback",
    "--print-flag-values",
    "--print-scopes",
    "--print-opt-source",
    "--turboshaft-trace-reduction",
    "--turboshaft-trace-typing",
    "--trace-deopt",
    "--trace-opt-verbose",
    "--trace-opt-status",
    "--trace-opt",
    "--trace-gc-verbose",
    "--trace-wasm",
    "--trace-turbolev-graph-building",
    "--trace-store-elimination",
    "--trace-turbo-load-elimination",
    "--trace-turbo-escape",
    "--trace-osr",
    "--trace-maglev-object-tracking",
    "--trace-generalization",
    "--trace-migration",
    "--trace-protector-invalidation",
    "--trace-pretenuring",
]


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is not set")
    return value


def run_command(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)


def run_d8(js_path: Path) -> str:
    d8_path = require_env("D8_PATH")
    result = run_command([d8_path, *D8_TRACE_FLAGS, str(js_path)])
    return (result.stdout or "") + (result.stderr or "")


def parse_fuzzil_from_output(text: str) -> str:
    start_seen = False
    out_lines: list[str] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not start_seen:
            if stripped.startswith(("v", "const", "function")):
                start_seen = True
                out_lines.append(line)
            continue
        if line.startswith("FuzzIL program written to"):
            break
        out_lines.append(line)
    return "\n".join(out_lines).strip()

