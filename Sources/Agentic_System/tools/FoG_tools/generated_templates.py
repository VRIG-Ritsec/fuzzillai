"""
FoG generated-template bookkeeping tools.

These helpers store proposed ProgramTemplate changes as session-scoped JSON
artifacts before applying them to Swift source. Compiled JavaScript still lives
in the existing generated_templates directory; template diffs live beside it in
template_diffs so they can be inspected or replayed.
"""

import difflib
import json
import re
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


def _params(params: dict | None = None, **kwargs) -> dict:
    merged = dict(params or {})
    merged.update({key: value for key, value in kwargs.items() if value is not None})
    return merged


def _diff_dir() -> Path:
    _shared._init_session()
    path = _shared.SESSION_DIR / "template_diffs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", name.strip())
    return cleaned or "template"


def _template_name(template_source: str) -> str | None:
    match = re.search(r'ProgramTemplate\(\s*"([^"]+)"', template_source)
    return match.group(1) if match else None


def _extract_named_block(text: str, name: str) -> str:
    marker = f'ProgramTemplate("{name}"'
    start = text.find(marker)
    if start == -1:
        return ""

    line_start = text.rfind("\n", 0, start) + 1
    depth = 0
    seen_brace = False
    end = None
    for index in range(start, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
            seen_brace = True
        elif char == "}":
            depth -= 1
            if seen_brace and depth == 0:
                end = index + 1
                if end < len(text) and text[end] == ",":
                    end += 1
                break

    if end is None:
        return ""
    return text[line_start:end].rstrip()


def extract_template_block(params) -> str:
    if isinstance(params, dict):
        text = params.get("text") or params.get("template_source") or ""
        name = params.get("name") or _template_name(text)
    else:
        text = str(params or "")
        name = _template_name(text)

    if name and _shared.PROGRAM_TEMPLATES_FILE.exists() and text == name:
        source = _shared.PROGRAM_TEMPLATES_FILE.read_text(encoding="utf-8", errors="ignore")
        return _extract_named_block(source, name)
    if name:
        return _extract_named_block(text, name) or text.strip()
    return text.strip()


def _current_block(name: str) -> str:
    if not _shared.PROGRAM_TEMPLATES_FILE.exists():
        return ""
    source = _shared.PROGRAM_TEMPLATES_FILE.read_text(encoding="utf-8", errors="ignore")
    return _extract_named_block(source, name)


def save_template_as_diff(params: dict | None = None, **kwargs) -> dict:
    params = _params(params, **kwargs)
    template_source = (params.get("template_source") or params.get("source") or "").strip()
    name = (params.get("name") or _template_name(template_source) or "").strip()
    if not name or not template_source:
        return {"status": "ERROR", "error": "name and template_source are required"}

    existing = _current_block(name)
    diff = "\n".join(
        difflib.unified_diff(
            existing.splitlines(),
            template_source.splitlines(),
            fromfile=f"current/{name}",
            tofile=f"proposed/{name}",
            lineterm="",
        )
    )

    artifact = {
        "created_at": _now(),
        "diff": diff,
        "name": name,
        "session_id": _shared.FOG_SESSION_ID,
        "status": "OK",
        "target_description": params.get("target_description", params.get("target", "")),
        "template_source": template_source,
        "weight": int(params.get("weight", 1)),
    }
    path = _diff_dir() / f"{_safe_name(name)}_{_timestamp()}.json"
    path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    artifact["path"] = str(path)
    return artifact


def _find_diff(name: str = "", diff_path: str = "") -> Path | None:
    if diff_path:
        path = Path(diff_path).expanduser().resolve()
        return path if path.exists() else None

    candidates = sorted(_diff_dir().glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if name:
        safe = _safe_name(name)
        candidates = [path for path in candidates if path.name.startswith(f"{safe}_")]
    return candidates[0] if candidates else None


def apply_template_diff(params: dict | None = None, **kwargs) -> dict:
    params = _params(params, **kwargs)
    path = _find_diff(params.get("name", ""), params.get("diff_path", ""))
    if path is None:
        return {"status": "ERROR", "error": "template diff not found"}

    data = json.loads(path.read_text(encoding="utf-8"))
    name = params.get("name") or data.get("name", "")
    template_source = params.get("template_source") or data.get("template_source", "")
    weight = int(params.get("weight", data.get("weight", 1)))
    if not name or not template_source:
        return {"status": "ERROR", "error": "diff is missing name or template_source", "path": str(path)}

    from .file_patch import upsert_program_template

    result = json.loads(upsert_program_template({
        "name": name,
        "template_source": template_source,
        "weight": weight,
    }))
    return {"status": result.get("status", "ERROR"), "path": str(path), "apply_result": result}


def list_template_diffs(params: dict | None = None, **kwargs) -> dict:
    params = _params(params, **kwargs)
    name = params.get("name", "")
    artifacts = []
    for path in sorted(_diff_dir().glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if name and data.get("name") != name:
            continue
        artifacts.append({
            "created_at": data.get("created_at"),
            "name": data.get("name"),
            "path": str(path),
            "target_description": data.get("target_description", ""),
            "weight": data.get("weight"),
        })
    return {"status": "OK", "diffs": artifacts}


def evaluate_target_quality(params: dict | None = None, **kwargs) -> dict:
    params = _params(params, **kwargs)
    target = (params.get("target_description") or params.get("target") or "").strip()
    template_source = (params.get("template_source") or "").strip()
    issues = []
    if not target:
        issues.append("missing target_description")
    if template_source and "ProgramTemplate(" not in template_source:
        issues.append("template_source does not declare a ProgramTemplate")
    if template_source and " b." not in template_source and "\tb." not in template_source:
        issues.append("template_source does not appear to emit builder operations")

    score = max(0, 100 - 20 * len(issues))
    return {
        "status": "OK",
        "score": score,
        "issues": issues,
        "target_description": target,
    }


def check_template_novelty(params: dict | None = None, **kwargs) -> dict:
    params = _params(params, **kwargs)
    template_source = (params.get("template_source") or "").strip()
    name = (params.get("name") or _template_name(template_source) or "").strip()

    existing_source = ""
    if _shared.PROGRAM_TEMPLATES_FILE.exists():
        existing_source = _shared.PROGRAM_TEMPLATES_FILE.read_text(encoding="utf-8", errors="ignore")

    existing_name = bool(name and f'ProgramTemplate("{name}"' in existing_source)
    exact_source = bool(template_source and template_source in existing_source)

    saved_exact = False
    for path in _diff_dir().glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if template_source and data.get("template_source", "").strip() == template_source:
            saved_exact = True
            break

    novelty_score = 0.0 if exact_source or saved_exact else (0.4 if existing_name else 1.0)
    return {
        "status": "OK",
        "name": name,
        "existing_name": existing_name,
        "exact_source_match": exact_source,
        "saved_diff_match": saved_exact,
        "novel": novelty_score >= 0.5,
        "novelty_score": novelty_score,
    }


def _save_executor(params: dict) -> str:
    return _json(save_template_as_diff(params))


def _apply_executor(params: dict) -> str:
    return _json(apply_template_diff(params))


def _list_executor(params: dict) -> str:
    return _json(list_template_diffs(params))


def _evaluate_executor(params: dict) -> str:
    return _json(evaluate_target_quality(params))


def _novelty_executor(params: dict) -> str:
    return _json(check_template_novelty(params))


save_template_diff_tool = IkaTools(
    name="save_template_diff",
    description="Save a proposed Fuzzilli ProgramTemplate as a session-scoped JSON diff artifact.",
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "template_source": {"type": "string"},
            "target_description": {"type": "string"},
            "weight": {"type": "integer"},
        },
        "required": ["name", "template_source"],
    },
    execute_function=_save_executor,
)

apply_template_diff_tool = IkaTools(
    name="apply_template_diff",
    description="Apply a saved template diff to ProgramTemplates.swift and ProgramTemplateWeights.swift.",
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "diff_path": {"type": "string"},
            "weight": {"type": "integer"},
        },
        "required": [],
    },
    execute_function=_apply_executor,
)

list_template_diffs_tool = IkaTools(
    name="list_template_diffs",
    description="List saved FoG template diff artifacts for the current session.",
    parameters={"type": "object", "properties": {"name": {"type": "string"}}, "required": []},
    execute_function=_list_executor,
)

evaluate_target_tool = IkaTools(
    name="evaluate_target_quality",
    description="Score whether a target description and optional template source are specific enough for FoG work.",
    parameters={
        "type": "object",
        "properties": {
            "target_description": {"type": "string"},
            "template_source": {"type": "string"},
        },
        "required": [],
    },
    execute_function=_evaluate_executor,
)

check_template_novelty_tool = IkaTools(
    name="check_template_novelty",
    description="Check whether a proposed ProgramTemplate duplicates current source or saved session diffs.",
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "template_source": {"type": "string"},
        },
        "required": [],
    },
    execute_function=_novelty_executor,
)
