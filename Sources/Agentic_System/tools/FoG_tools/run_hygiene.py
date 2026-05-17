"""
FoG run hygiene helpers.

The runtime data produced by the agent branch keeps three pieces of state around
template mutation runs:

* a global baseline of ProgramTemplates.swift and ProgramTemplateWeights.swift
* per-session preflight/restore/postrun JSON reports
* snapshots of files immediately before baseline restore
"""

import hashlib
import json
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from IkaCore.tools import IkaTools

from . import _shared


def _json(data: dict) -> str:
    return json.dumps(data, indent=2, sort_keys=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_info(path: Path, source_path: bool = False) -> dict:
    key = "source_path" if source_path else "path"
    if not path.exists():
        return {"exists": False, key: str(path), "bytes": 0, "sha256": None}
    return {
        key: str(path),
        "exists": True,
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _runtime_data_dir() -> Path:
    return _shared.SESSIONS_DIR.parent


def _current_files() -> dict:
    return {
        "ProgramTemplateWeights.swift": _file_info(_shared.PROGRAM_WEIGHTS_FILE),
        "ProgramTemplates.swift": _file_info(_shared.PROGRAM_TEMPLATES_FILE),
    }


def _template_names() -> list[str]:
    if not _shared.PROGRAM_TEMPLATES_FILE.exists():
        return []
    text = _shared.PROGRAM_TEMPLATES_FILE.read_text(encoding="utf-8", errors="ignore")
    return __import__("re").findall(r'ProgramTemplate\(\s*"([^"]+)"', text)


def _weight_names() -> list[str]:
    if not _shared.PROGRAM_WEIGHTS_FILE.exists():
        return []
    text = _shared.PROGRAM_WEIGHTS_FILE.read_text(encoding="utf-8", errors="ignore")
    return __import__("re").findall(r'"([^"]+)"\s*:', text)


def _duplicates(values: list[str]) -> list[str]:
    counts = Counter(values)
    return sorted([name for name, count in counts.items() if count > 1])


def _template_checks() -> dict:
    templates = _template_names()
    weights = _weight_names()
    template_set = set(templates)
    weight_set = set(weights)
    return {
        "duplicate_templates": _duplicates(templates),
        "duplicate_weights": _duplicates(weights),
        "missing_weights": sorted(template_set - weight_set),
        "orphan_weights": sorted(weight_set - template_set),
        "template_count": len(templates),
        "weight_count": len(weights),
    }


def _write_report(prefix: str, report: dict) -> dict:
    _shared.FOG_RUN_HYGIENE_DIR.mkdir(parents=True, exist_ok=True)
    path = _shared.FOG_RUN_HYGIENE_DIR / f"{prefix}_{_timestamp()}.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    returned = dict(report)
    returned["report_path"] = str(path)
    return returned


def _manifest_file_info(path: Path) -> dict:
    info = _file_info(path, source_path=True)
    info.pop("exists", None)
    return info


def _write_baseline_manifest(source: str) -> None:
    manifest = {
        "baseline_dir": str(_shared.FOG_TEMPLATE_BASELINE_DIR),
        "created_at": _now(),
        "files": {
            "ProgramTemplateWeights.swift": _manifest_file_info(_shared.PROGRAM_WEIGHTS_FILE),
            "ProgramTemplates.swift": _manifest_file_info(_shared.PROGRAM_TEMPLATES_FILE),
        },
        "session_id": _shared.FOG_SESSION_ID,
        "source": source,
    }
    (_shared.FOG_TEMPLATE_BASELINE_DIR / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _ensure_baseline() -> dict:
    _shared.FOG_TEMPLATE_BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    baseline_templates = _shared.FOG_TEMPLATE_BASELINE_DIR / "ProgramTemplates.swift"
    baseline_weights = _shared.FOG_TEMPLATE_BASELINE_DIR / "ProgramTemplateWeights.swift"
    manifest_path = _shared.FOG_TEMPLATE_BASELINE_DIR / "manifest.json"

    if baseline_templates.exists() and baseline_weights.exists() and manifest_path.exists():
        return {
            "baseline_dir": str(_shared.FOG_TEMPLATE_BASELINE_DIR),
            "changed": False,
            "manifest_path": str(manifest_path),
            "source": "existing",
            "status": "OK",
        }

    missing = [
        str(path)
        for path in [_shared.PROGRAM_TEMPLATES_FILE, _shared.PROGRAM_WEIGHTS_FILE]
        if not path.exists()
    ]
    if missing:
        return {
            "baseline_dir": str(_shared.FOG_TEMPLATE_BASELINE_DIR),
            "changed": False,
            "manifest_path": str(manifest_path),
            "source": "missing_current_files",
            "status": "ERROR",
            "error": f"cannot create baseline; missing files: {missing}",
        }

    shutil.copy2(str(_shared.PROGRAM_TEMPLATES_FILE), str(baseline_templates))
    shutil.copy2(str(_shared.PROGRAM_WEIGHTS_FILE), str(baseline_weights))
    _write_baseline_manifest("git_head")
    return {
        "baseline_dir": str(_shared.FOG_TEMPLATE_BASELINE_DIR),
        "changed": True,
        "manifest_path": str(manifest_path),
        "source": "git_head",
        "status": "OK",
    }


def _clear_spm_locks() -> list[str]:
    root = str(_shared.FUZZILLI_PATH).strip("/")
    if not root:
        return []
    safe_root = "_" + root.replace("/", "_") + "_"
    cleared = []
    for path in Path("/tmp").glob(f"{safe_root}.build*.lock"):
        try:
            path.unlink()
            cleared.append(str(path))
        except OSError:
            pass
    return cleared


def _base_report(label: str) -> dict:
    return {
        "checks": _template_checks(),
        "created_at": _now(),
        "files": _current_files(),
        "label": label,
        "runtime_data_dir": str(_runtime_data_dir()),
        "session_dir": str(_shared.SESSION_DIR),
        "session_id": _shared.FOG_SESSION_ID,
        "status": "OK",
    }


def prepare_clean_fog_run(params: dict | None = None) -> dict:
    params = params or {}
    _shared._init_session()

    restore_first = bool(params.get("restore_baseline", False))
    restore_report = restore_template_baseline({}) if restore_first else None
    baseline = _ensure_baseline()

    report = _base_report("preflight")
    report["baseline"] = baseline
    report["cleared_spm_locks"] = _clear_spm_locks()
    report["preflight_action"] = "restored_baseline" if restore_first else "analyzed_current"
    if restore_report:
        report["preflight_restore"] = restore_report
    if baseline.get("status") != "OK":
        report["status"] = "ERROR"
    return _write_report("preflight", report)


def restore_template_baseline(params: dict | None = None) -> dict:
    _shared._init_session()
    baseline = _ensure_baseline()

    snapshot_dir = _shared.FOG_RUN_HYGIENE_DIR / f"pre_restore_{_timestamp()}"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshots = []
    for source in [_shared.PROGRAM_TEMPLATES_FILE, _shared.PROGRAM_WEIGHTS_FILE]:
        if source.exists():
            dest = snapshot_dir / source.name
            shutil.copy2(str(source), str(dest))
            snapshots.append(str(dest))

    status = "OK"
    error = None
    if baseline.get("status") == "OK":
        for filename, target in [
            ("ProgramTemplates.swift", _shared.PROGRAM_TEMPLATES_FILE),
            ("ProgramTemplateWeights.swift", _shared.PROGRAM_WEIGHTS_FILE),
        ]:
            source = _shared.FOG_TEMPLATE_BASELINE_DIR / filename
            if source.exists():
                shutil.copy2(str(source), str(target))
            else:
                status = "ERROR"
                error = f"missing baseline file: {source}"
    else:
        status = "ERROR"
        error = baseline.get("error")

    report = _base_report("after_restore")
    report["restore"] = {
        "baseline_dir": str(_shared.FOG_TEMPLATE_BASELINE_DIR),
        "snapshots": snapshots,
        "status": status,
    }
    if error:
        report["restore"]["error"] = error
        report["status"] = "ERROR"
    return _write_report("restore", report)


def write_postrun_hygiene_report(params: dict | None = None) -> dict:
    params = params or {}
    _shared._init_session()

    report = _base_report("postrun")
    report["run_result"] = {
        "completed": bool(params.get("completed", params.get("run_completed", True))),
        "error": params.get("error", params.get("run_error")),
    }

    restore_after = bool(params.get("restore_after_report", True))
    if restore_after:
        report["postrun_restore"] = restore_template_baseline({})
    return _write_report("postrun", report)


def _preflight_executor(params: dict) -> str:
    return _json(prepare_clean_fog_run(params))


def _restore_executor(params: dict) -> str:
    return _json(restore_template_baseline(params))


def _postrun_executor(params: dict) -> str:
    return _json(write_postrun_hygiene_report(params))


fog_template_preflight_tool = IkaTools(
    name="fog_template_preflight",
    description="Create a FoG session preflight report, ensure template baseline, and clear stale SwiftPM lock files.",
    parameters={
        "type": "object",
        "properties": {"restore_baseline": {"type": "boolean"}},
        "required": [],
    },
    execute_function=_preflight_executor,
)

fog_template_restore_baseline_tool = IkaTools(
    name="fog_template_restore_baseline",
    description="Restore ProgramTemplates.swift and ProgramTemplateWeights.swift from the FoG baseline.",
    parameters={"type": "object", "properties": {}, "required": []},
    execute_function=_restore_executor,
)

fog_template_postrun_report_tool = IkaTools(
    name="fog_template_postrun_report",
    description="Write a FoG postrun hygiene report and restore template baseline by default.",
    parameters={
        "type": "object",
        "properties": {
            "completed": {"type": "boolean"},
            "error": {"type": "string"},
            "restore_after_report": {"type": "boolean"},
        },
        "required": [],
    },
    execute_function=_postrun_executor,
)
