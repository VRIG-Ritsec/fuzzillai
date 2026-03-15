"""
FoG tools shared helpers and constants.
"""

import os
import re
import shlex
import subprocess
import json
import random
from pathlib import Path

_tools_dir = Path(__file__).resolve().parent.parent
_agentic_dir = _tools_dir.parent
_runtime_data_dir = _agentic_dir / "runtime_data"

V8_PATH = os.getenv("V8_PATH", "")
D8_PATH = os.getenv("D8_PATH", "")
FUZZILLI_PATH = os.getenv("FUZZILLI_PATH", "")
FUZZILLI_TOOL_BIN = os.getenv("FUZZILLI_TOOL_BIN", "")
SWIFT_PATH = os.path.join(FUZZILLI_PATH, "Sources", "Fuzzilli") if FUZZILLI_PATH else ""
OUTPUT_DIRECTORY = str(_runtime_data_dir / "fog-d8-records")
GENERATED_TEMPLATE_DIR = str(_runtime_data_dir / "generated_templates") + os.sep

_REGRESSIONS_PATH = (_agentic_dir / "regressions" / "regressions.json").resolve()
_TEMPLATES_PATH = (_agentic_dir / "templates" / "templates.json").resolve()
_REGRESSIONS_CACHE = None
_TEMPLATES_CACHE = None

os.makedirs(OUTPUT_DIRECTORY, exist_ok=True)
os.makedirs(GENERATED_TEMPLATE_DIR, exist_ok=True)

try:
    from fuzzywuzzy import fuzz
    FUZZYWUZZY_AVAILABLE = True
except ImportError:
    FUZZYWUZZY_AVAILABLE = False

    class fuzz:
        @staticmethod
        def ratio(a, b):
            if a == b:
                return 100
            shorter = min(len(a), len(b))
            longer = max(len(a), len(b))
            return int((shorter / longer) * 100) if longer else 100


def run_command(command: str, timeout: int = 90):
    try:
        return subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        class TimeoutResult:
            def __init__(self, timeout_sec, cmd):
                self.stdout = ""
                self.stderr = f"Command timed out after {timeout_sec} seconds: {cmd}"
                self.returncode = -1
                self.args = cmd
        return TimeoutResult(timeout, command)


def get_output(completed_process) -> str:
    if not completed_process:
        return ""
    out = completed_process.stdout if completed_process.stdout else None
    err = completed_process.stderr if completed_process.stderr else None
    return out if out else (err if err else "")


def is_valid_regex(pattern: str) -> tuple:
    try:
        re.compile(pattern)
        return True, None
    except re.error as e:
        return False, e


def _load_regressions_once():
    global _REGRESSIONS_CACHE
    if _REGRESSIONS_CACHE is not None:
        return _REGRESSIONS_CACHE
    try:
        with open(_REGRESSIONS_PATH, "r") as f:
            _REGRESSIONS_CACHE = json.load(f)
    except Exception:
        _REGRESSIONS_CACHE = {}
    return _REGRESSIONS_CACHE


def _load_templates_once():
    global _TEMPLATES_CACHE
    if _TEMPLATES_CACHE is not None:
        return _TEMPLATES_CACHE
    try:
        with open(_TEMPLATES_PATH, "r") as f:
            _TEMPLATES_CACHE = json.load(f)
    except Exception:
        _TEMPLATES_CACHE = {}
    return _TEMPLATES_CACHE
