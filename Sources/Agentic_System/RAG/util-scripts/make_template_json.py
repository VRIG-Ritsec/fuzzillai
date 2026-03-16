#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from generator_common import LIFTED_TEMPLATES_DIR, SWIFT_TEMPLATE_SOURCES, TEMPLATES_JSON, require_env, run_d8


def extract_swift_templates(swift_content: str) -> dict[str, str]:
    templates: dict[str, str] = {}
    regex = r'(Program|WasmProgram)Template\("(?P<name>[^"]+)"\)\s*\{(?P<code>.*?)\n\s*\},?'
    for match in re.finditer(regex, swift_content, re.DOTALL):
        templates[match.group("name").strip()] = match.group("code").strip()
    return templates


def load_swift_templates(paths: list[Path]) -> dict[str, str]:
    templates: dict[str, str] = {}
    for path in paths:
        if not path.exists():
            continue
        templates.update(extract_swift_templates(path.read_text(encoding="utf-8", errors="ignore")))
    return templates


def extract_section(content: str, marker: str, next_marker: str | None = None) -> str:
    start = content.find(marker)
    if start == -1:
        return ""
    start += len(marker)
    end = len(content) if next_marker is None else content.find(next_marker, start)
    if end == -1:
        end = len(content)
    return content[start:end].strip()


def parse_template(template_path: Path) -> tuple[str, dict[str, str]]:
    content = template_path.read_text(encoding="utf-8", errors="ignore")
    first_line = content.splitlines()[0] if content else ""
    if ": " in first_line:
        template_name = first_line.split(": ", 1)[1].strip()
    else:
        template_name = template_path.stem

    data = {
        "ProgramTemplateName": template_name,
        "ProgramTemplateSwift": "",
        "ProgramTemplateFuzzIL": extract_section(content, "FuzzIL:\n"),
        "ProgramTemplateJS": extract_section(content, "Program:\n", "FuzzIL:\n"),
        "ProgramTemplateExecution": run_d8(template_path),
    }
    return template_name, data


def main() -> int:
    parser = argparse.ArgumentParser(description="Build templates/templates.json from lifted templates.")
    parser.add_argument("--templates-dir", default=str(LIFTED_TEMPLATES_DIR), help="Lifted template directory")
    parser.add_argument("--output", default=str(TEMPLATES_JSON), help="Output json path")
    parser.add_argument(
        "--swift-source",
        action="append",
        dest="swift_sources",
        help="Swift template source file. May be passed multiple times.",
    )
    args = parser.parse_args()

    templates_dir = Path(args.templates_dir).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    swift_sources = [Path(p).expanduser().resolve() for p in (args.swift_sources or [str(p) for p in SWIFT_TEMPLATE_SOURCES])]

    if not templates_dir.exists():
        print(f"Template directory does not exist: {templates_dir}")
        return 1

    try:
        require_env("D8_PATH")
    except RuntimeError as exc:
        print(exc)
        return 1

    swift_templates = load_swift_templates(swift_sources)
    files_data: dict[str, dict[str, str]] = {}

    for template_path in sorted(path for path in templates_dir.iterdir() if path.is_file()):
        template_name, data = parse_template(template_path)
        data["ProgramTemplateSwift"] = swift_templates.get(template_name, "")
        files_data[template_name] = data

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(files_data, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Wrote {output_path} with {len(files_data)} templates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
