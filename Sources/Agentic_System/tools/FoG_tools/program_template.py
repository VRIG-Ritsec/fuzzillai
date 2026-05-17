"""
FoG program template tools: list, edit, compile, execute, list_d8_flags.
"""

import glob
import os
import re
import shlex
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

_agentic_dir = Path(__file__).resolve().parent.parent.parent
_ikacore_src = _agentic_dir / "IkaCore" / "src"
import sys
if str(_ikacore_src) not in sys.path:
    sys.path.insert(0, str(_ikacore_src))

from IkaCore.tools import IkaTools

from tools._shared import resolve_js_path

from ._shared import (
    FOG_SESSION_ID,
    FUZZILLI_PATH,
    GENERATED_TEMPLATE_DIR,
    PROGRAM_TEMPLATES_FILE,
    PROGRAM_WEIGHTS_FILE,
    TEMPLATE_BACKUP_DIR,
    _init_session,
    _next_attempt,
    _runtime_artifact_dir,
    get_output,
    run_command,
    run_d8_command,
)


_ALLOWED_RELATIVE_PATHS = (
    "Sources/Fuzzilli/CodeGen/ProgramTemplates.swift",
    "Sources/Fuzzilli/CodeGen/ProgramTemplateWeights.swift",
)
_UNIFIED_HUNK_HEADER_RE = re.compile(
    r"^@@\s*-\d+(?:,\d+)?\s+\+\d+(?:,\d+)?\s*@@(?:\s*(.*))?$"
)
_PATCH_METADATA_PREFIXES = (
    "diff --git ",
    "index ",
    "--- ",
    "+++ ",
    "new file mode ",
    "deleted file mode ",
    "rename from ",
    "rename to ",
    "similarity index ",
    "dissimilarity index ",
    "old mode ",
    "new mode ",
)
_SUPPORTED_D8_FLAGS_CACHE: set[str] | None = None


@dataclass
class _PatchHunk:
    anchor: str | None
    old_lines: list[str]
    new_lines: list[str]


def _allowed_program_template_paths() -> dict[str, Path]:
    return {
        _ALLOWED_RELATIVE_PATHS[0]: PROGRAM_TEMPLATES_FILE,
        _ALLOWED_RELATIVE_PATHS[1]: PROGRAM_WEIGHTS_FILE,
    }


def _take_file_snapshot(path: Path, label: str) -> str:
    """
    Copy a target file into TEMPLATE_BACKUP_DIR before modification.
    Returns the snapshot path, or empty string on failure.
    """
    if not path.exists():
        return ""
    TEMPLATE_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    dest = TEMPLATE_BACKUP_DIR / f"{label}_{path.stem}_{timestamp}{path.suffix}"
    try:
        shutil.copy2(str(path), str(dest))
        return str(dest)
    except Exception:
        return ""


def _resolve_program_template_path(raw_path: str) -> tuple[str, Path]:
    candidate = (raw_path or "").strip()
    if not candidate:
        raise ValueError("path parameter is required")

    path_obj = Path(candidate)
    if path_obj.is_absolute():
        resolved = path_obj.resolve()
        for rel_path, actual_path in _allowed_program_template_paths().items():
            if resolved == actual_path.resolve():
                return rel_path, actual_path

    for rel_path, actual_path in _allowed_program_template_paths().items():
        if candidate == rel_path or path_obj.name == Path(rel_path).name:
            return rel_path, actual_path

    allowed = ", ".join(_ALLOWED_RELATIVE_PATHS)
    raise ValueError(f"path must target one of: {allowed}")


def _is_diff_content_line(line: str) -> bool:
    if not line:
        return False
    if line.startswith(" "):
        return True
    if line.startswith("+"):
        return not line.startswith("+++ ")
    if line.startswith("-"):
        return not line.startswith("--- ")
    return False


def _normalize_patch_diff(diff: str) -> list[str]:
    lines = diff.splitlines()

    while lines and lines[-1].strip() == "" and not _is_diff_content_line(lines[-1]):
        lines.pop()

    if lines and lines[0].strip().startswith("*** Begin Patch"):
        lines = lines[1:]
    if lines and lines[0].strip() == "***":
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("*** End Patch"):
        lines = lines[:-1]
    if lines and lines[-1].strip() == "***":
        lines = lines[:-1]

    normalized: list[str] = []
    for line in lines:
        if _is_diff_content_line(line):
            normalized.append(line)
            continue

        trimmed = line.strip()
        if trimmed.startswith("*** Update File:"):
            continue
        if trimmed.startswith("*** Add File:"):
            continue
        if trimmed.startswith("*** Delete File:"):
            continue
        if any(trimmed.startswith(prefix) for prefix in _PATCH_METADATA_PREFIXES):
            continue

        normalized.append(line)

    return normalized


def _parse_hunk_anchor(header: str) -> str | None:
    trimmed = header.strip()
    if trimmed in {"@@", "@@ @@"}:
        return None

    unified_match = _UNIFIED_HUNK_HEADER_RE.match(trimmed)
    if unified_match:
        anchor = (unified_match.group(1) or "").strip()
        return anchor or None

    anchor = trimmed[2:].strip()
    if anchor.endswith("@@"):
        anchor = anchor[:-2].strip()
    return anchor or None


def _parse_patch_hunks(diff: str) -> list[_PatchHunk]:
    lines = _normalize_patch_diff(diff)
    if not lines:
        raise ValueError("Diff does not contain any hunks.")

    hunks: list[_PatchHunk] = []
    index = 0
    while index < len(lines):
        header = lines[index].rstrip()
        if not header.startswith("@@"):
            raise ValueError(
                f"Invalid diff format at line {index + 1}: expected a hunk header starting with @@."
            )

        anchor = _parse_hunk_anchor(header)
        index += 1

        body: list[str] = []
        while index < len(lines) and not lines[index].startswith("@@"):
            if lines[index].strip() == "*** End of File":
                index += 1
                continue
            body.append(lines[index])
            index += 1

        if not body:
            raise ValueError("Each hunk must include at least one body line.")

        old_lines: list[str] = []
        new_lines: list[str] = []
        saw_change = False
        for body_line in body:
            if not body_line:
                raise ValueError("Diff body lines must start with a space, +, or -.")
            prefix = body_line[0]
            if prefix not in {" ", "+", "-"}:
                raise ValueError(
                    f"Invalid diff line '{body_line}'. Body lines must start with a space, +, or -."
                )
            text = body_line[1:]
            if prefix in {" ", "-"}:
                old_lines.append(text)
            if prefix in {" ", "+"}:
                new_lines.append(text)
            if prefix in {"+", "-"}:
                saw_change = True

        if not saw_change:
            raise ValueError("Each hunk must include at least one addition or removal.")

        hunks.append(_PatchHunk(anchor=anchor, old_lines=old_lines, new_lines=new_lines))

    return hunks


def _read_lines_with_format(path: Path) -> tuple[list[str], str, bool]:
    content = path.read_text()
    line_ending = "\r\n" if "\r\n" in content else "\n"
    has_trailing_newline = content.endswith("\n") or content.endswith("\r")
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    if has_trailing_newline and lines and lines[-1] == "":
        lines.pop()
    return lines, line_ending, has_trailing_newline


def _write_lines_with_format(
    path: Path,
    lines: list[str],
    line_ending: str,
    has_trailing_newline: bool,
) -> None:
    content = "\n".join(lines)
    if has_trailing_newline:
        content += "\n"
    content = content.replace("\n", line_ending)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _find_exact_matches(lines: list[str], needle: list[str]) -> list[int]:
    if not needle:
        return []
    matches: list[int] = []
    limit = len(lines) - len(needle) + 1
    for start in range(max(limit, 0)):
        if lines[start : start + len(needle)] == needle:
            matches.append(start)
    return matches


def _filter_matches_by_anchor(
    lines: list[str],
    matches: list[int],
    needle: list[str],
    anchor: str | None,
) -> list[int]:
    if not anchor:
        return matches

    filtered: list[int] = []
    for start in matches:
        end = start + max(len(needle), 1)
        window = lines[max(0, start - 2) : min(len(lines), end + 2)]
        if any(anchor in line for line in window):
            filtered.append(start)
    return filtered


def _apply_patch_hunks(lines: list[str], hunks: list[_PatchHunk]) -> list[str]:
    updated = list(lines)

    for index, hunk in enumerate(hunks, start=1):
        if hunk.old_lines:
            matches = _find_exact_matches(updated, hunk.old_lines)
            matches = _filter_matches_by_anchor(updated, matches, hunk.old_lines, hunk.anchor)
            if not matches:
                raise ValueError(
                    f"No match found for hunk {index}. Re-read the file and use a more exact anchor/context."
                )
            if len(matches) > 1:
                raise ValueError(
                    f"Found multiple matches for hunk {index}. Add more context lines or a more specific anchor."
                )

            start = matches[0]
            end = start + len(hunk.old_lines)
            updated = updated[:start] + hunk.new_lines + updated[end:]
            continue

        if not hunk.anchor:
            raise ValueError(
                f"Hunk {index} has no removable context. Add context lines or a specific @@ anchor."
            )

        anchor_matches = [line_index for line_index, line in enumerate(updated) if hunk.anchor in line]
        if not anchor_matches:
            raise ValueError(
                f"No match found for hunk {index}. Re-read the file and use a more exact anchor/context."
            )
        if len(anchor_matches) > 1:
            raise ValueError(
                f"Found multiple matches for hunk {index}. Add more context lines or a more specific anchor."
            )

        insert_at = anchor_matches[0] + 1
        updated = updated[:insert_at] + hunk.new_lines + updated[insert_at:]

    return updated


def _edit_program_template_file_executor(params: dict) -> str:
    raw_path = params.get("path", "")
    op = (params.get("op") or "update").strip()
    rename = params.get("rename")
    diff = params.get("diff")

    if op not in {"create", "delete", "update"}:
        return f"Error: Unsupported op '{op}'. Use create, update, or delete."

    try:
        rel_path, target_path = _resolve_program_template_path(raw_path)
        rename_rel_path = None
        rename_path = None
        if rename:
            rename_rel_path, rename_path = _resolve_program_template_path(rename)
    except ValueError as exc:
        return f"Error: {exc}"

    if op in {"create", "update"} and not isinstance(diff, str):
        return "Error: diff parameter is required for create and update operations."
    if op == "delete" and rename:
        return "Error: rename is only supported together with op='update'."

    _init_session()
    snapshot = _take_file_snapshot(target_path, "pre_edit")

    try:
        if op == "create":
            if target_path.exists():
                return f"Error: {rel_path} already exists."
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(diff)
            message = f"OK: Created {rel_path}."
        elif op == "delete":
            if not target_path.exists():
                return f"Error: {rel_path} does not exist."
            target_path.unlink()
            message = f"OK: Deleted {rel_path}."
        else:
            if not target_path.exists():
                return f"Error: {rel_path} does not exist."

            current_lines, line_ending, has_trailing_newline = _read_lines_with_format(target_path)
            hunks = _parse_patch_hunks(diff)
            updated_lines = _apply_patch_hunks(current_lines, hunks)

            output_path = rename_path or target_path
            _write_lines_with_format(output_path, updated_lines, line_ending, has_trailing_newline)
            if rename_path and rename_path != target_path:
                target_path.unlink()
                message = f"OK: Updated and renamed {rel_path} to {rename_rel_path}."
            else:
                message = f"OK: Updated {rel_path}."

        snapshot_msg = f" Snapshot: {snapshot}." if snapshot else ""
        return message + snapshot_msg
    except Exception as exc:
        return f"Error: {exc}"


def _list_program_templates_executor(params: dict) -> str:
    if not PROGRAM_TEMPLATES_FILE.exists():
        return f"Error: ProgramTemplates.swift not found at {PROGRAM_TEMPLATES_FILE}"
    try:
        content = PROGRAM_TEMPLATES_FILE.read_text()
    except Exception as exc:
        return f"Error reading ProgramTemplates.swift: {exc}"
    pattern = r'(?:WasmProgramTemplate|ProgramTemplate)\s*\("([^"]+)"\)'
    program_templates = re.findall(pattern, content)
    return f"Found program templates: {program_templates}"


def _clear_spm_locks() -> list[str]:
    """Remove stale Swift Package Manager lock files that block compilation."""
    import subprocess

    patterns = [
        "/tmp/_mnt_vdc_fuzzillai_*lock*",
        "/tmp/_tmp_fzlai_*lock*",
    ]
    removed: list[str] = []
    for pattern in patterns:
        for lock_path in glob.glob(pattern):
            try:
                os.remove(lock_path)
                removed.append(lock_path)
            except PermissionError:
                try:
                    subprocess.run(
                        ["sudo", "-n", "rm", "-f", lock_path],
                        capture_output=True, timeout=5,
                    )
                    if not os.path.exists(lock_path):
                        removed.append(lock_path)
                except Exception:
                    pass
            except OSError:
                pass
    return removed


def _ensure_build_writable() -> None:
    """Make .build/ artifacts writable so swift run can update them."""
    import subprocess

    build_dir = os.path.join(FUZZILLI_PATH, ".build")
    if not os.path.isdir(build_dir):
        return
    try:
        subprocess.run(
            ["sudo", "-n", "chmod", "-R", "a+w", build_dir],
            capture_output=True, timeout=30,
        )
    except Exception:
        pass


_COMPILE_MAX_RETRIES = 5
_COMPILE_RETRY_DELAY = 3


def _compile_program_template_executor(params: dict) -> str:
    template = params.get("template", "")
    if not template:
        return "Error: template parameter is required"

    last_error = ""
    for attempt_num in range(_COMPILE_MAX_RETRIES):
        _clear_spm_locks()
        _ensure_build_writable()

        build = run_command(
            f'cd {shlex.quote(FUZZILLI_PATH)} && swift run FuzzILTool --compileTemplate="{template}" fake_path',
            timeout=180,
        )
        stdout = build.stdout or ""
        stderr = build.stderr or ""
        return_code = getattr(build, "returncode", 0)

        if return_code == 0 and stdout.strip():
            break

        last_error = stderr.strip() or stdout.strip() or f"exit code {return_code}"
        is_lock_error = "lock" in last_error.lower() or "unable to load manifest" in last_error.lower()
        is_permission_error = "operation not permitted" in last_error.lower() or "permission denied" in last_error.lower()
        if not is_lock_error and not is_permission_error and return_code != 0:
            return f"Swift build failed: {last_error}"
        if attempt_num < _COMPILE_MAX_RETRIES - 1:
            _clear_spm_locks()
            time.sleep(_COMPILE_RETRY_DELAY)
    else:
        return f"Swift build failed after {_COMPILE_MAX_RETRIES} retries: {last_error}"

    javascript = stdout
    if not javascript.strip():
        details = stderr.strip() or "FuzzILTool completed without emitting JavaScript."
        return f"Swift build failed: {details}"

    attempt = _next_attempt(template)
    filename = f"{template}_{FOG_SESSION_ID}_{attempt:03d}.js"
    gen_dir = Path(GENERATED_TEMPLATE_DIR.rstrip(os.sep))
    gen_dir.mkdir(parents=True, exist_ok=True)
    path = str(gen_dir / filename)
    try:
        with open(path, "w") as file_handle:
            file_handle.write(javascript)
    except Exception as exc:
        return f"Error writing JS file: {exc}"
    return f"Generated JavaScript from {template}, stored at {path}.\nJavaScript:\n{javascript}"


def _execute_javascript_program_executor(params: dict) -> str:
    template_js_path = params.get("template_js_path", "")
    d8_flags = params.get("d8_flags", "")
    if not template_js_path:
        return "Error: template_js_path parameter is required"
    resolved_js_path = resolve_js_path(template_js_path) or template_js_path
    requested_flags = shlex.split(d8_flags) if d8_flags else []
    supported_flags = _get_supported_d8_flags()
    requested_flags, dropped_flags = _sanitize_d8_flags(requested_flags, supported_flags)
    requested_flag_names = {flag.split("=", 1)[0] for flag in requested_flags if flag.startswith("--")}

    default_flags = ["--allow-natives-syntax", "--print-bytecode", "--trace-opt", "--trace-deopt"]
    optional_defaults = ["--trace-osr", "--trace-turbo", "--trace-maglev-graph-building"]
    for flag in default_flags + optional_defaults:
        if supported_flags and flag not in supported_flags:
            continue
        if flag not in requested_flag_names:
            requested_flags.append(flag)
            requested_flag_names.add(flag)

    result = run_d8_command(
        requested_flags + [resolved_js_path],
        cwd=_runtime_artifact_dir(resolved_js_path),
    )
    used_flags = " ".join(requested_flags)
    notes = ""
    if dropped_flags:
        notes = f"\n[dropped unsupported/problematic flags] {' '.join(dropped_flags)}"
    return f"Program execution result:\n[flags used] {used_flags}{notes}\n{result.stderr}\n{result.stdout}"


def _list_d8_flags_executor(params: dict) -> str:
    filter_str = params.get("filter", "")
    if not filter_str:
        return "Error: filter parameter is required"
    d8 = run_d8_command(["--help"])
    if d8.returncode != 0:
        return get_output(d8)
    matches = [line for line in d8.stdout.splitlines() if filter_str.lower() in line.lower()]
    return f"Available flags for filter '{filter_str}':\n" + "\n".join(matches)


def _get_supported_d8_flags() -> set[str]:
    global _SUPPORTED_D8_FLAGS_CACHE
    if _SUPPORTED_D8_FLAGS_CACHE is not None:
        return _SUPPORTED_D8_FLAGS_CACHE

    d8 = run_d8_command(["--help"])
    if d8.returncode != 0:
        _SUPPORTED_D8_FLAGS_CACHE = set()
        return _SUPPORTED_D8_FLAGS_CACHE

    supported_flags = set()
    for token in re.findall(r"--[A-Za-z0-9][A-Za-z0-9-]*(?:=[A-Za-z0-9_<>,.-]+)?", get_output(d8)):
        supported_flags.add(token.split("=", 1)[0])

    _SUPPORTED_D8_FLAGS_CACHE = supported_flags
    return _SUPPORTED_D8_FLAGS_CACHE


def _sanitize_d8_flags(flags: list[str], supported_flags: set[str]) -> tuple[list[str], list[str]]:
    sanitized: list[str] = []
    dropped: list[str] = []
    for flag in flags:
        flag_name = flag.split("=", 1)[0]
        if flag_name == "--print-code":
            dropped.append(flag)
            continue
        if supported_flags and flag_name.startswith("--") and flag_name not in supported_flags:
            dropped.append(flag)
            continue
        sanitized.append(flag)
    return sanitized, dropped


edit_program_template_file_tool = IkaTools(
    name="edit_program_template_file",
    description=(
        "Patch ProgramTemplates.swift or ProgramTemplateWeights.swift using an "
        "oh-my-pi-style file editor interface. For updates, pass diff hunks with "
        "@@ headers and lines prefixed by space, +, or -. Read the file first and "
        "copy anchors/context verbatim. A session snapshot is saved before edits."
    ),
    parameters={
        "path": {
            "type": "string",
            "description": (
                "Target file. Use Sources/Fuzzilli/CodeGen/ProgramTemplates.swift or "
                "Sources/Fuzzilli/CodeGen/ProgramTemplateWeights.swift."
            ),
            "required": True,
        },
        "op": {
            "type": "string",
            "description": "Operation: update (default), create, or delete.",
            "required": False,
        },
        "rename": {
            "type": "string",
            "description": "Optional rename destination for update operations.",
            "required": False,
        },
        "diff": {
            "type": "string",
            "description": (
                "For update: diff hunks with @@ headers. For create: full file content. "
                "Delete operations do not use diff."
            ),
            "required": False,
        },
    },
    execute_function=_edit_program_template_file_executor,
)

list_program_templates_tool = IkaTools(
    name="list_program_templates",
    description="List all program template names defined in ProgramTemplates.swift",
    parameters={"input": {"type": "string", "description": "Unused. Leave empty.", "required": False}},
    execute_function=_list_program_templates_executor,
)

compile_program_template_tool = IkaTools(
    name="compile_program_template",
    description="Compile a Swift program template to JavaScript via FuzzILTool. Output is saved to the session's generated_templates directory with a unique name.",
    parameters={"template": {"type": "string", "description": "Template name to compile (e.g. 'Codegen100')", "required": True}},
    execute_function=_compile_program_template_executor,
)

execute_javascript_program_tool = IkaTools(
    name="execute_javascript_program",
    description="Run a JavaScript file with d8 (adds --allow-natives-syntax and trace flags automatically). Accepts absolute paths or file names written into the current generate folder.",
    parameters={
        "template_js_path": {"type": "string", "description": "Absolute path or generated file name for the .js file", "required": True},
        "d8_flags": {"type": "string", "description": "Optional extra d8 flags (e.g. '--turbofan')", "required": False},
    },
    execute_function=_execute_javascript_program_executor,
)

list_d8_flags_tool = IkaTools(
    name="list_d8_flags",
    description="List d8 command-line flags that match a grep pattern. Use to discover trace/debug options.",
    parameters={"filter": {"type": "string", "description": "Substring to match in d8 --help (e.g. 'trace', 'maglev')", "required": True}},
    execute_function=_list_d8_flags_executor,
)
