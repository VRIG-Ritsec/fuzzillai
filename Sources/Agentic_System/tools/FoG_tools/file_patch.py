"""
FoG file patch tools.

These tools intentionally use exact string replacement instead of fuzzy patching:
agents must read the target file, copy the old text exactly, and then submit a
replacement. That keeps compiler edits deterministic and easy to audit.
"""

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from IkaCore.tools import IkaTools

from . import _shared


def _json(data: dict) -> str:
    return json.dumps(data, indent=2, sort_keys=True)


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _resolve_under(base: str | Path, file_path: str) -> Path:
    if not file_path:
        raise ValueError("file_path is required")

    base_path = Path(base).expanduser().resolve()
    raw_path = Path(file_path).expanduser()
    candidate = raw_path.resolve() if raw_path.is_absolute() else (base_path / raw_path).resolve()

    if not _is_relative_to(candidate, base_path):
        raise ValueError(f"path escapes allowed root: {file_path}")
    return candidate


def _resolve_swift_path(file_path: str) -> Path:
    if file_path.startswith("Sources/") or file_path.startswith("Tests/") or file_path == "Package.swift":
        return _resolve_under(_shared.FUZZILLI_PATH, file_path)
    return _resolve_under(_shared.SWIFT_PATH, file_path)


def _record_read(kind: str, file_path: str) -> None:
    try:
        _shared.SESSION_DIR.mkdir(parents=True, exist_ok=True)
        log_path = _shared.SESSION_DIR / "file_reads.jsonl"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"kind": kind, "file_path": file_path}, sort_keys=True) + "\n")
    except Exception:
        pass


def record_v8_file_read(file_path: str) -> None:
    _record_read("v8", file_path)


def record_swift_file_read(file_path: str) -> None:
    _record_read("swift", file_path)


def _backup_before_write(target: Path, action: str = "patch") -> str | None:
    if target.name not in {"ProgramTemplates.swift", "ProgramTemplateWeights.swift"}:
        return None
    if not target.exists():
        return None

    _shared._init_session()
    _shared.TEMPLATE_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup = _shared.TEMPLATE_BACKUP_DIR / f"pre_{action}_{target.stem}_{_timestamp()}.swift"
    shutil.copy2(str(target), str(backup))
    return str(backup)


def _apply_exact_replacements(target: Path, replacements: list[dict]) -> dict:
    text = target.read_text(encoding="utf-8")
    updated = text
    applied = 0

    for replacement in replacements:
        old = replacement.get("old_string")
        new = replacement.get("new_string")
        if old is None or new is None:
            return {"status": "ERROR", "error": "each patch requires old_string and new_string"}
        if old not in updated:
            return {
                "status": "ERROR",
                "error": "old_string not found",
                "file_path": str(target),
                "applied": applied,
            }
        updated = updated.replace(old, new, 1)
        applied += 1

    if updated != text:
        backup = _backup_before_write(target, "patch")
        target.write_text(updated, encoding="utf-8")
    else:
        backup = None

    result = {"status": "OK", "file_path": str(target), "applied": applied}
    if backup:
        result["backup"] = backup
    return result


def _patch_file(params: dict, resolver) -> str:
    try:
        target = resolver(params.get("file_path", ""))
        if not target.exists():
            return _json({"status": "ERROR", "error": "file does not exist", "file_path": str(target)})

        if "patches" in params:
            patches = params["patches"]
        else:
            patches = [{"old_string": params.get("old_string"), "new_string": params.get("new_string")}]

        if not isinstance(patches, list):
            return _json({"status": "ERROR", "error": "patches must be a list"})
        return _json(_apply_exact_replacements(target, patches))
    except Exception as exc:
        return _json({"status": "ERROR", "error": str(exc)})


def _multi_patch(params: dict, resolver) -> str:
    items = params.get("patches", [])
    if not isinstance(items, list):
        return _json({"status": "ERROR", "error": "patches must be a list"})

    results = []
    for item in items:
        if not isinstance(item, dict):
            results.append({"status": "ERROR", "error": "patch item must be an object"})
            continue
        results.append(json.loads(_patch_file(item, resolver)))
    overall = "OK" if all(result.get("status") == "OK" for result in results) else "ERROR"
    return _json({"status": overall, "results": results})


def _v8_patch_executor(params: dict) -> str:
    return _patch_file(params, lambda path: _resolve_under(_shared.V8_PATH, path))


def _v8_multi_patch_executor(params: dict) -> str:
    return _multi_patch(params, lambda path: _resolve_under(_shared.V8_PATH, path))


def _swift_patch_executor(params: dict) -> str:
    return _patch_file(params, _resolve_swift_path)


def _swift_multi_patch_executor(params: dict) -> str:
    return _multi_patch(params, _resolve_swift_path)


def _replace_template_block(text: str, name: str, template_source: str) -> tuple[str, bool]:
    marker = f'ProgramTemplate("{name}"'
    start = text.find(marker)
    if start == -1:
        return text, False

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
                while end < len(text) and text[end] in " \t\r\n":
                    end += 1
                break

    if end is None:
        raise ValueError(f"could not locate end of template {name}")
    replacement = template_source.strip()
    if replacement:
        return text[:line_start] + replacement.rstrip() + "\n\n" + text[end:], True
    return text[:line_start] + text[end:], True


def _upsert_weight(weights_text: str, name: str, weight: int) -> str:
    line = f'\t"{name}": {int(weight)},'
    marker = f'"{name}"'
    lines = weights_text.splitlines()
    for idx, existing in enumerate(lines):
        if marker in existing:
            lines[idx] = line
            return "\n".join(lines) + "\n"

    insert_at = len(lines)
    for idx in range(len(lines) - 1, -1, -1):
        if lines[idx].strip() == "]":
            insert_at = idx
            break
    lines.insert(insert_at, line)
    return "\n".join(lines) + "\n"


def upsert_program_template(params: dict) -> str:
    try:
        name = params.get("name", "").strip()
        template_source = params.get("template_source", "").strip()
        weight = int(params.get("weight", 1))
        if not name or not template_source:
            return _json({"status": "ERROR", "error": "name and template_source are required"})

        templates_file = _shared.PROGRAM_TEMPLATES_FILE
        weights_file = _shared.PROGRAM_WEIGHTS_FILE
        templates_text = templates_file.read_text(encoding="utf-8")

        if f'ProgramTemplate("{name}"' not in template_source:
            return _json({"status": "ERROR", "error": "template_source must contain ProgramTemplate(\"name\")"})

        updated, replaced = _replace_template_block(templates_text, name, template_source)
        if not replaced:
            insert_at = updated.rfind("]")
            if insert_at == -1:
                return _json({"status": "ERROR", "error": "could not find template list terminator"})
            updated = updated[:insert_at].rstrip() + "\n\n" + template_source.rstrip() + "\n\n" + updated[insert_at:]
        template_backup = _backup_before_write(templates_file, "patch")
        templates_file.write_text(updated, encoding="utf-8")

        weights_text = weights_file.read_text(encoding="utf-8") if weights_file.exists() else "let programTemplateWeights: [String: Int] = [\n]\n"
        updated_weights = _upsert_weight(weights_text, name, weight)
        weight_backup = None
        if updated_weights != weights_text:
            weight_backup = _backup_before_write(weights_file, "patch")
            weights_file.write_text(updated_weights, encoding="utf-8")
        return _json({
            "status": "OK",
            "name": name,
            "replaced": replaced,
            "weight": weight,
            "backups": [path for path in [template_backup, weight_backup] if path],
        })
    except Exception as exc:
        return _json({"status": "ERROR", "error": str(exc)})


def remove_program_template(params: dict) -> str:
    try:
        name = params.get("name", "").strip()
        if not name:
            return _json({"status": "ERROR", "error": "name is required"})

        templates_file = _shared.PROGRAM_TEMPLATES_FILE
        templates_text = templates_file.read_text(encoding="utf-8")
        updated, replaced = _replace_template_block(templates_text, name, "")
        if replaced:
            template_backup = _backup_before_write(templates_file, "patch")
            templates_file.write_text(updated, encoding="utf-8")
        else:
            template_backup = None

        weights_file = _shared.PROGRAM_WEIGHTS_FILE
        weight_backup = None
        if weights_file.exists():
            marker = f'"{name}"'
            weights_text = weights_file.read_text(encoding="utf-8")
            lines = [line for line in weights_text.splitlines() if marker not in line]
            updated_weights = "\n".join(lines) + "\n"
            if updated_weights != weights_text:
                weight_backup = _backup_before_write(weights_file, "patch")
                weights_file.write_text(updated_weights, encoding="utf-8")
        return _json({
            "status": "OK",
            "name": name,
            "removed": replaced,
            "backups": [path for path in [template_backup, weight_backup] if path],
        })
    except Exception as exc:
        return _json({"status": "ERROR", "error": str(exc)})


_PATCH_PARAMS = {
    "type": "object",
    "properties": {
        "file_path": {"type": "string"},
        "old_string": {"type": "string"},
        "new_string": {"type": "string"},
    },
    "required": ["file_path", "old_string", "new_string"],
}

swift_patch_file_tool = IkaTools(
    id="swift_patch_file",
    name="swift_patch_file",
    description="Apply one exact old_string/new_string replacement to a Swift/Fuzzilli file.",
    parameters=_PATCH_PARAMS,
    execute_function=_swift_patch_executor,
)

swift_multi_patch_file_tool = IkaTools(
    id="swift_multi_patch_file",
    name="swift_multi_patch_file",
    description="Apply multiple exact replacements to Swift/Fuzzilli files.",
    parameters={"type": "object", "properties": {"patches": {"type": "array"}}, "required": ["patches"]},
    execute_function=_swift_multi_patch_executor,
)

v8_patch_file_tool = IkaTools(
    id="v8_patch_file",
    name="v8_patch_file",
    description="Apply one exact old_string/new_string replacement to a V8 source file.",
    parameters=_PATCH_PARAMS,
    execute_function=_v8_patch_executor,
)

v8_multi_patch_file_tool = IkaTools(
    id="v8_multi_patch_file",
    name="v8_multi_patch_file",
    description="Apply multiple exact replacements to V8 source files.",
    parameters={"type": "object", "properties": {"patches": {"type": "array"}}, "required": ["patches"]},
    execute_function=_v8_multi_patch_executor,
)

upsert_program_template_tool = IkaTools(
    id="upsert_program_template",
    name="upsert_program_template",
    description="Insert or replace a Fuzzilli ProgramTemplate and update its weight.",
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "template_source": {"type": "string"},
            "weight": {"type": "integer"},
        },
        "required": ["name", "template_source"],
    },
    execute_function=upsert_program_template,
)

remove_program_template_tool = IkaTools(
    id="remove_program_template",
    name="remove_program_template",
    description="Remove a Fuzzilli ProgramTemplate and its weight entry by name.",
    parameters={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    execute_function=remove_program_template,
)
