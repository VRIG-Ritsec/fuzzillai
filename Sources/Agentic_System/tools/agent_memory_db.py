"""
Agent memory storage with session-scoped JSON files.
Storage: runtime_data/agent_memory/<session>/<entry_id>.json
Session: AGENT_MEMORY_SESSION env or auto-generated timestamp (YYYYMMDD_HHMMSS).
"""

import json
import os
from datetime import datetime
from pathlib import Path

_AGENT_MEMORY_DIR: Path | None = None
_SESSION_ID: str | None = None


def _base_dir() -> Path:
    global _AGENT_MEMORY_DIR
    if _AGENT_MEMORY_DIR is None:
        default = Path(__file__).resolve().parent.parent / "runtime_data" / "agent_memory"
        env_path = os.environ.get("AGENT_MEMORY_DIR")
        _AGENT_MEMORY_DIR = Path(env_path).resolve() if env_path else default.resolve()
    return _AGENT_MEMORY_DIR


def _get_session_id() -> str:
    global _SESSION_ID
    if _SESSION_ID is None:
        # Prefer the FoG session ID so agent memory is co-located with the run
        _SESSION_ID = (
            os.environ.get("FOG_SESSION_ID")
            or os.environ.get("AGENT_MEMORY_SESSION")
            or datetime.now().strftime("%Y%m%d_%H%M%S")
        )
    return _SESSION_ID


def _session_dir() -> Path:
    return _base_dir() / _get_session_id()


def _entry_path(entry_id: str) -> Path:
    return _session_dir() / f"{entry_id}.json"


def entry_path(entry_id: str) -> Path:
    return _entry_path(entry_id)


def load(entry_id: str) -> dict:
    path = _entry_path(entry_id)
    if not path.exists():
        return {}
    try:
        with open(path, "r") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save(entry_id: str, data: dict) -> None:
    session = _session_dir()
    session.mkdir(parents=True, exist_ok=True)
    path = _entry_path(entry_id)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def list_ids() -> list[str]:
    session = _session_dir()
    if not session.exists():
        return []
    ids = []
    for p in session.iterdir():
        if p.suffix == ".json" and p.is_file():
            ids.append(p.stem)
    return sorted(ids)


def get_session_id() -> str:
    return _get_session_id()
