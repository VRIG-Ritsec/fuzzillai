"""
Startup-time runtime checks for Agentic_System entrypoints.
"""

import importlib.util
import os
import shutil
from pathlib import Path

from config_loader import apply_runtime_paths, get_runtime_paths

from venv_site_packages import add_fuzzillai_repo_venv_site_packages


def _check_path_value(var_name: str, raw: str, expected: str) -> str | None:
    if not raw:
        return f"{var_name} is not set"

    path = Path(raw).expanduser()
    if expected == "file" and not path.is_file():
        return f"{var_name} does not point to an existing file: {path}"
    if expected == "dir" and not path.is_dir():
        return f"{var_name} does not point to an existing directory: {path}"
    return None


def collect_runtime_preflight(
    check_debugger: bool = False,
    warn_only_vars: tuple[str, ...] = (),
) -> tuple[list[str], list[str]]:
    add_fuzzillai_repo_venv_site_packages()
    errors = []
    warnings = []
    resolved_paths = get_runtime_paths()
    apply_runtime_paths()
    warn_only = set(warn_only_vars)

    for var_name, expected in (
        ("V8_PATH", "dir"),
        ("D8_PATH", "file"),
        ("FUZZILLI_PATH", "dir"),
        ("FUZZILLI_TOOL_BIN", "file"),
    ):
        raw = resolved_paths.get(var_name, os.getenv(var_name, "").strip())
        issue = _check_path_value(var_name, raw, expected)
        if issue:
            if var_name in warn_only:
                warnings.append(issue)
            else:
                errors.append(issue)

    if check_debugger:
        if importlib.util.find_spec("pygdbmi") is None:
            warnings.append("pygdbmi is not installed; MI debugger validation will be unavailable")
        if shutil.which("gdb") is None:
            warnings.append("gdb is not available on PATH; breakpoint-driven validation will be unavailable")

    return errors, warnings


def format_preflight_report(errors: list[str], warnings: list[str]) -> str:
    lines = []
    if errors:
        lines.append("Errors:")
        lines.extend(f"- {error}" for error in errors)
    if warnings:
        lines.append("Warnings:")
        lines.extend(f"- {warning}" for warning in warnings)
    return "\n".join(lines)
