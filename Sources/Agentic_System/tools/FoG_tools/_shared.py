"""
FoG tools shared helpers and constants.
"""

import json
import os
import re
import shlex
import shutil
import subprocess
import random
import uuid
from datetime import datetime, timezone
from pathlib import Path

_tools_dir = Path(__file__).resolve().parent.parent
_agentic_dir = _tools_dir.parent
_runtime_data_dir = _agentic_dir / "runtime_data"

from config_loader import (
    apply_runtime_paths,
    get_d8_path,
    get_fuzzilli_path,
    get_fuzzilli_tool_bin,
    get_v8_path,
)


def _default_fuzzilli_root() -> Path:
    try:
        return _agentic_dir.parents[1]
    except IndexError:
        return _agentic_dir.parent


def _normalize_fuzzilli_root(raw_path: str | None) -> str:
    if not raw_path:
        return str(_default_fuzzilli_root())
    path = Path(raw_path).expanduser().resolve()
    if path.name == "Agentic_System" and path.parent.name == "Sources":
        return str(path.parents[1])
    if path.name == "Sources" and (path / "Agentic_System").exists():
        return str(path.parent)
    return str(path)


def _existing_regressions_json_candidates() -> list[Path]:
    candidates: list[Path] = []
    raw = os.getenv("REGRESSIONS_JSON")
    if raw:
        candidates.append(Path(raw).expanduser())
    candidates.extend(
        [
            _agentic_dir / "regressions" / "regressions.json",
            _agentic_dir / "regressions.json",
        ]
    )
    existing = [path.resolve() for path in candidates if path.exists()]
    existing.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return existing


def _existing_regressions_zst_candidates() -> list[Path]:
    candidates: list[Path] = []
    raw = os.getenv("REGRESSIONS_JSON_ZST")
    if raw:
        candidates.append(Path(raw).expanduser())
    candidates.extend(
        [
            _agentic_dir / "regressions" / "regressions.json.zst",
            _agentic_dir / "regressions.json.zst",
        ]
    )
    existing = [path.resolve() for path in candidates if path.exists()]
    existing.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return existing


def _load_regressions_from_zst(path: Path):
    try:
        import zstandard as zstd
    except ImportError:
        zstd = None

    if zstd is not None:
        with open(path, "rb") as f:
            reader = zstd.ZstdDecompressor().stream_reader(f)
            return json.loads(reader.read().decode("utf-8"))

    for cmd in (["zstd", "-d", "-c", str(path)], ["unzstd", "-c", str(path)]):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
        return json.loads(result.stdout)

    raise FileNotFoundError(
        "Could not load compressed regressions index: install python zstandard or ensure zstd/unzstd is available"
    )


def _build_regressions_from_filesystem(root: Path) -> dict:
    if not root.exists():
        return {}
    data = {}
    for js_path in sorted(root.rglob("*")):
        if not js_path.is_file() or js_path.suffix.lower() not in {".js", ".mjs"}:
            continue
        rel_path = js_path.relative_to(root)
        key = str(rel_path.with_suffix("")).replace(os.sep, "/")
        data[key] = {
            "js": js_path.read_text(encoding="utf-8", errors="ignore"),
            "path": str(rel_path).replace(os.sep, "/"),
            "source": "filesystem",
        }
    return data

apply_runtime_paths()
V8_PATH = get_v8_path()
D8_PATH = get_d8_path()
FUZZILLI_PATH = _normalize_fuzzilli_root(get_fuzzilli_path())
FUZZILLI_TOOL_BIN = get_fuzzilli_tool_bin()
SWIFT_PATH = os.path.join(FUZZILLI_PATH, "Sources", "Fuzzilli") if FUZZILLI_PATH else ""
OUTPUT_DIRECTORY = str(_runtime_data_dir / "fog-d8-records")

PROGRAM_TEMPLATES_FILE = Path(SWIFT_PATH) / "CodeGen" / "ProgramTemplates.swift"
PROGRAM_WEIGHTS_FILE = Path(SWIFT_PATH) / "CodeGen" / "ProgramTemplateWeights.swift"

# ── Default templates (single source of truth) ────────────────────────────────

DEFAULT_TEMPLATES: frozenset = frozenset([
    "Codegen100", "Codegen50", "WasmCodegen50", "WasmCodegen100",
    "MixedJsAndWasm1", "MixedJsAndWasm2", "JSPI",
    "ThrowInWasmCatchInJS", "WasmReturnCalls",
    "JIT1Function", "JIT2Functions", "JITTrickyFunction", "JSONFuzzer",
])

# ── Session identity ───────────────────────────────────────────────────────────

def _generate_session_id() -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"fog_{ts}_{uuid.uuid4().hex[:8]}"


FOG_SESSION_ID: str = os.environ.setdefault("FOG_SESSION_ID", _generate_session_id())

SESSIONS_DIR = _runtime_data_dir / "fog_sessions"
SESSION_DIR = SESSIONS_DIR / FOG_SESSION_ID
GENERATED_TEMPLATE_DIR = str(SESSION_DIR / "generated_templates") + os.sep
TEMPLATE_BACKUP_DIR = SESSION_DIR / "template_backups"
SESSION_METADATA_FILE = SESSION_DIR / "metadata.json"

_TEMPLATES_PATH = (_agentic_dir / "templates" / "templates.json").resolve()
_REGRESSIONS_CACHE = None
_TEMPLATES_CACHE = None

os.makedirs(OUTPUT_DIRECTORY, exist_ok=True)
os.makedirs(GENERATED_TEMPLATE_DIR, exist_ok=True)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _runtime_artifact_dir(js_path: str | None = None) -> str:
    runtime_root = _runtime_data_dir.resolve()
    default_dir = Path(OUTPUT_DIRECTORY).expanduser().resolve()

    if js_path:
        try:
            candidate = Path(js_path).expanduser().resolve().parent
            if _is_relative_to(candidate, runtime_root):
                candidate.mkdir(parents=True, exist_ok=True)
                return str(candidate)
        except Exception:
            pass

    default_dir.mkdir(parents=True, exist_ok=True)
    return str(default_dir)

# ── Session initialisation ─────────────────────────────────────────────────────

def _init_session() -> str:
    """
    Create session directories, snapshot original Swift files, write metadata.json.
    Safe to call multiple times (idempotent). Returns the session ID.
    """
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    Path(GENERATED_TEMPLATE_DIR).mkdir(parents=True, exist_ok=True)
    TEMPLATE_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    Path(OUTPUT_DIRECTORY).mkdir(parents=True, exist_ok=True)

    orig_templates = TEMPLATE_BACKUP_DIR / "original.swift"
    if not orig_templates.exists() and PROGRAM_TEMPLATES_FILE.exists():
        shutil.copy2(str(PROGRAM_TEMPLATES_FILE), str(orig_templates))

    orig_weights = TEMPLATE_BACKUP_DIR / "original_weights.swift"
    if not orig_weights.exists() and PROGRAM_WEIGHTS_FILE.exists():
        shutil.copy2(str(PROGRAM_WEIGHTS_FILE), str(orig_weights))

    if not SESSION_METADATA_FILE.exists():
        meta = {
            "session_id": FOG_SESSION_ID,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "worker_model": os.environ.get("FOG_WORKER_MODEL", ""),
            "manager_model": os.environ.get("FOG_MANAGER_MODEL", ""),
            "template_attempts": {},
        }
        with open(SESSION_METADATA_FILE, "w") as f:
            json.dump(meta, f, indent=2)

    return FOG_SESSION_ID


def _next_attempt(template_name: str) -> int:
    """Increment and return the per-template compile attempt counter for this session."""
    if not SESSION_METADATA_FILE.exists():
        _init_session()
    try:
        with open(SESSION_METADATA_FILE) as f:
            meta = json.load(f)
    except Exception:
        meta = {}
    attempts = meta.setdefault("template_attempts", {})
    count = attempts.get(template_name, 0) + 1
    attempts[template_name] = count
    try:
        with open(SESSION_METADATA_FILE, "w") as f:
            json.dump(meta, f, indent=2)
    except Exception:
        pass
    return count

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


def _error_process(args, message: str, returncode: int = 127):
    return subprocess.CompletedProcess(args=args, returncode=returncode, stdout="", stderr=message)


def run_process(args: list[str], timeout: int = 90, cwd: str | None = None):
    if not args:
        return _error_process(args, "Error: no command provided")
    try:
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
    except subprocess.TimeoutExpired:
        return _error_process(args, f"Command timed out after {timeout} seconds: {' '.join(args)}", returncode=-1)


def get_output(completed_process) -> str:
    if not completed_process:
        return ""
    out = completed_process.stdout if completed_process.stdout else None
    err = completed_process.stderr if completed_process.stderr else None
    return out if out else (err if err else "")


def _check_v8_binary() -> str | None:
    if not D8_PATH:
        return "Error: D8_PATH is not set"
    if not os.path.exists(D8_PATH):
        return f"Error: D8 not found at '{D8_PATH}'"
    return None


def _check_fuzzilli_tool_bin() -> str | None:
    if not FUZZILLI_TOOL_BIN:
        return "Error: FUZZILLI_TOOL_BIN is not set"
    if not os.path.exists(FUZZILLI_TOOL_BIN):
        return f"Error: FuzzILTool not found at '{FUZZILLI_TOOL_BIN}'"
    return None


def run_d8_command(extra_args: list[str], timeout: int = 90, cwd: str | None = None):
    err = _check_v8_binary()
    if err:
        return _error_process([D8_PATH, *extra_args], err)
    return run_process([D8_PATH, *extra_args], timeout=timeout, cwd=cwd or _runtime_artifact_dir())


def run_fuzzilli_tool(extra_args: list[str], timeout: int = 90, cwd: str | None = None):
    err = _check_fuzzilli_tool_bin()
    if err:
        return _error_process([FUZZILLI_TOOL_BIN, *extra_args], err)
    return run_process([FUZZILLI_TOOL_BIN, *extra_args], timeout=timeout, cwd=cwd)


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

    json_candidates = _existing_regressions_json_candidates()
    if json_candidates:
        try:
            with open(json_candidates[0], "r", encoding="utf-8") as f:
                _REGRESSIONS_CACHE = json.load(f)
            return _REGRESSIONS_CACHE
        except Exception:
            pass

    zst_candidates = _existing_regressions_zst_candidates()
    if zst_candidates:
        try:
            _REGRESSIONS_CACHE = _load_regressions_from_zst(zst_candidates[0])
            return _REGRESSIONS_CACHE
        except Exception:
            pass

    _REGRESSIONS_CACHE = _build_regressions_from_filesystem(_agentic_dir / "regressions")
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
