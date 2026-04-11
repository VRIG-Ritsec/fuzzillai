"""
Put the repo .venv site-packages on sys.path before other imports.

Must stay stdlib-only so it can run before third-party deps load.
"""

from __future__ import annotations

import os
import site
import sys
from pathlib import Path


def fuzzillai_repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def preferred_python_executable() -> str:
    bindir = fuzzillai_repo_root() / ".venv" / "bin"
    if not bindir.is_dir():
        return sys.executable
    for exe_name in (
        f"python{sys.version_info.major}.{sys.version_info.minor}",
        "python3",
        "python",
    ):
        candidate = bindir / exe_name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return sys.executable


def add_fuzzillai_repo_venv_site_packages() -> None:
    try:
        root = fuzzillai_repo_root()
        lib = root / ".venv" / "lib"
        if not lib.is_dir():
            return
        suffix = f"{sys.version_info.major}.{sys.version_info.minor}"
        preferred = lib / f"python{suffix}" / "site-packages"
        if preferred.is_dir():
            site.addsitedir(str(preferred))
            return
        candidates = sorted(
            (p for p in lib.glob("python*/site-packages") if p.is_dir()),
            key=lambda p: p.name,
            reverse=True,
        )
        if candidates:
            site.addsitedir(str(candidates[0]))
    except Exception:
        pass
