"""
FoG program template tools: write, list, remove, edit, compile, execute, list_d8_flags, remove_old_js.
"""

import os
import re
from pathlib import Path

_agentic_dir = Path(__file__).resolve().parent.parent.parent
_ikacore_src = _agentic_dir / "IkaCore" / "src"
import sys
if str(_ikacore_src) not in sys.path:
    sys.path.insert(0, str(_ikacore_src))

from IkaCore.tools import IkaTools

from ._shared import (
    SWIFT_PATH,
    D8_PATH,
    GENERATED_TEMPLATE_DIR,
    run_command,
    get_output,
)


def _write_program_template_executor(params: dict) -> str:
    program_template = params.get("program_template", "")
    if not program_template:
        return "Error: program_template parameter is required"
    program_templates_file = os.path.join(SWIFT_PATH, "CodeGen", "ProgramTemplates.swift")
    if not os.path.exists(program_templates_file):
        return f"Error: ProgramTemplates.swift not found at {program_templates_file}"
    try:
        with open(program_templates_file, "r") as f:
            content = f.read()
    except Exception as e:
        return f"Error reading ProgramTemplates.swift: {e}"
    content = content.rstrip()
    if not content.endswith("]"):
        return "Error: ProgramTemplates.swift does not end with closing bracket"
    template_code = program_template.strip()
    if not template_code.endswith(","):
        template_code += ","
    content = content[:-1] + "\n\n    " + template_code + "\n]"
    try:
        with open(program_templates_file, "w") as f:
            f.write(content)
        ret = f"OK: Successfully wrote program template to {program_templates_file}"
    except Exception as e:
        return f"Error writing to ProgramTemplates.swift: {e}"
    program_template_weights_file = os.path.join(SWIFT_PATH, "CodeGen", "ProgramTemplateWeights.swift")
    template_name_pattern = r'(?:WasmProgramTemplate|ProgramTemplate)\s*\("([^"]+)"\)'
    name_match = re.search(template_name_pattern, program_template)
    if not name_match:
        return ret + "\nWarning: Could not extract template name to update weights."
    template_name = name_match.group(1)
    if not os.path.exists(program_template_weights_file):
        return ret + "\nWarning: ProgramTemplateWeights.swift not found"
    try:
        with open(program_template_weights_file, "r") as f:
            content_weights = f.read()
        content_weights = content_weights.rstrip()
        if content_weights.endswith("]"):
            new_weight_entry = f'\n\t"{template_name}": 2,'
            content_weights = content_weights[:-1] + new_weight_entry + "\n]"
            with open(program_template_weights_file, "w") as f:
                f.write(content_weights)
            return ret + "\nOK: Successfully wrote template weight"
    except Exception as e:
        return ret + f"\nWarning: Error updating weights: {e}"
    return ret


def _list_program_templates_executor(params: dict) -> str:
    program_templates_file = os.path.join(SWIFT_PATH, "CodeGen", "ProgramTemplates.swift")
    if not os.path.exists(program_templates_file):
        return f"Error: ProgramTemplates.swift not found at {program_templates_file}"
    try:
        with open(program_templates_file, "r") as f:
            content = f.read()
    except Exception as e:
        return f"Error reading ProgramTemplates.swift: {e}"
    pattern = r'(?:WasmProgramTemplate|ProgramTemplate)\s*\("([^"]+)"\)'
    program_templates = re.findall(pattern, content)
    return f"Found program templates: {program_templates}"


def _remove_program_template_executor(params: dict) -> str:
    program_template = params.get("program_template", "")
    if not program_template:
        return "Error: program_template parameter is required"
    default_templates = ["Codegen100", "Codegen50", "WasmCodegen50", "WasmCodegen100",
                        "MixedJsAndWasm1", "MixedJsAndWasm2", "JSPI",
                        "ThrowInWasmCatchInJS", "WasmReturnCalls", "JIT1Function",
                        "JIT2Functions", "JITTrickyFunction", "JSONFuzzer"]
    if program_template in default_templates:
        return f"Cannot remove default template. Defaults: {default_templates}"
    program_templates_file = os.path.join(SWIFT_PATH, "CodeGen", "ProgramTemplates.swift")
    if not os.path.exists(program_templates_file):
        return "Error: ProgramTemplates.swift not found"
    try:
        with open(program_templates_file, "r") as f:
            content = f.read()
    except Exception as e:
        return f"Error reading file: {e}"
    block_start_pattern = re.compile(
        r'^\s*(?:WasmProgramTemplate|ProgramTemplate)\s*\(\s*"' + re.escape(program_template) + r'"\s*\)\s*\{',
        re.MULTILINE,
    )
    start_match = block_start_pattern.search(content)
    if not start_match:
        return f"Error: Template '{program_template}' not found"
    start_index = start_match.start()
    brace_count = 1
    end_index = -1
    for i in range(start_match.end(), len(content)):
        if content[i] == "{":
            brace_count += 1
        elif content[i] == "}":
            brace_count -= 1
            if brace_count == 0:
                end_index = i
                break
    if end_index == -1:
        return f"Error: Could not find closing brace for template '{program_template}'"
    separator_after_match = re.search(r"^\s*,\s*", content[end_index + 1 :], re.MULTILINE | re.DOTALL)
    if separator_after_match:
        end_of_block = end_index + 1 + separator_after_match.end()
    else:
        return "Failed to remove template - separator not found"
    content = content[:start_index] + content[end_of_block:]
    try:
        with open(program_templates_file, "w") as f:
            f.write(content)
        return f"OK: Removed template {program_template}"
    except Exception as e:
        return f"Error writing file: {e}"


def _remove_program_template_weight_executor(params: dict) -> str:
    program_template = params.get("program_template", "")
    if not program_template:
        return "Error: program_template parameter is required"
    default_templates = ["Codegen100", "Codegen50", "WasmCodegen50", "WasmCodegen100",
                        "MixedJsAndWasm1", "MixedJsAndWasm2", "JSPI",
                        "ThrowInWasmCatchInJS", "WasmReturnCalls", "JIT1Function",
                        "JIT2Functions", "JITTrickyFunction", "JSONFuzzer"]
    if program_template in default_templates:
        return "Cannot remove default template weight"
    weights_file = os.path.join(SWIFT_PATH, "CodeGen", "ProgramTemplateWeights.swift")
    if not os.path.exists(weights_file):
        return "Error: ProgramTemplateWeights.swift not found"
    try:
        with open(weights_file, "r") as f:
            content = f.read()
        pattern = re.compile(r'^\s*"' + re.escape(program_template) + r'"\s*:\s*\d+\s*,\s*$', re.MULTILINE)
        content = pattern.sub("", content)
        content = re.sub(r"\n\s*\n", "\n", content)
        with open(weights_file, "w") as f:
            f.write(content)
        return f"OK: Removed weight for {program_template}"
    except Exception as e:
        return f"Error: {e}"


def _edit_template_by_diff_executor(params: dict) -> str:
    old_text = params.get("old_text", "")
    new_text = params.get("new_text", "")
    start_line = params.get("start_line")
    end_line = params.get("end_line")
    if not old_text:
        return "Error: old_text parameter is required"
    filepath = os.path.join(SWIFT_PATH, "CodeGen", "ProgramTemplates.swift")
    if not os.path.exists(filepath):
        return f"Error: File not found at {filepath}"
    try:
        with open(filepath, "r") as f:
            content = f.read()
    except Exception as e:
        return f"Error reading file: {e}"
    lines = content.splitlines()
    total_lines = len(lines)
    if not (start_line and end_line):
        return "Error: Must provide start_line AND end_line"
    try:
        start_line = int(start_line)
        end_line = int(end_line)
    except (ValueError, TypeError):
        return "Error: start_line and end_line must be integers"
    if start_line < 1 or start_line > total_lines:
        return f"Error: Invalid start_line ({start_line}). File has {total_lines} lines."
    if end_line < 1 or end_line > total_lines:
        return f"Error: Invalid end_line ({end_line}). File has {total_lines} lines."
    if start_line > end_line:
        return f"Error: start_line ({start_line}) > end_line ({end_line})"
    section_lines = lines[start_line - 1 : end_line]
    section_content = "\n".join(section_lines)
    if old_text not in section_content:
        return f"Error: old_text not found in lines {start_line}-{end_line}"
    if section_content.count(old_text) > 1:
        return "Error: Multiple occurrences of old_text found. Make it more specific."
    updated_section = section_content.replace(old_text, new_text)
    updated_lines = lines[: start_line - 1] + updated_section.split("\n") + lines[end_line:]
    updated_content = "\n".join(updated_lines)
    if content.endswith("\n"):
        updated_content += "\n"
    try:
        with open(filepath, "w") as f:
            f.write(updated_content)
        return f"OK: Successfully updated lines {start_line}-{end_line}"
    except Exception as e:
        return f"Error writing file: {e}"


def edit_template_by_diff(old_text: str, new_text: str, start_line: int = None, end_line: int = None) -> str:
    return _edit_template_by_diff_executor({
        "old_text": old_text,
        "new_text": new_text,
        "start_line": start_line,
        "end_line": end_line,
    })


def _compile_program_template_executor(params: dict) -> str:
    template = params.get("template", "")
    if not template:
        return "Error: template parameter is required"
    build = run_command(f'swift run FuzzILTool --compileTemplate="{template}" fake_path')
    if build.stderr and not build.stdout:
        return f"Swift build failed: {build.stderr}"
    javascript = build.stdout
    path = f"{GENERATED_TEMPLATE_DIR}{template}-{hash(javascript)}.js"
    try:
        with open(path, "w") as f:
            f.write(javascript)
    except Exception as e:
        return f"Error writing JS file: {e}"
    return f"Generated JavaScript from {template}, stored at {path}.\nJavaScript:\n{javascript}"


def execute_javascript_program(template_js_path: str, d8_flags: str) -> str:
    if "--allow-natives-syntax" not in d8_flags:
        d8_flags += " --allow-natives-syntax"
    d8_flags = d8_flags.strip()
    result = run_command(f"{D8_PATH} {d8_flags} {template_js_path}")
    return f"Program execution result:\n[flags used] {d8_flags}\n{result.stderr}\n{result.stdout}"


def execute_javascript_program(template_js_path: str, d8_flags: str) -> str:
    if "--allow-natives-syntax" not in d8_flags:
        d8_flags += " --allow-natives-syntax"
    d8_flags = d8_flags.strip()
    result = run_command(f"{D8_PATH} {d8_flags} {template_js_path}")
    return f"Program execution result:\n[flags used] {d8_flags}\n{result.stderr}\n{result.stdout}"


def _execute_javascript_program_executor(params: dict) -> str:
    template_js_path = params.get("template_js_path", "")
    d8_flags = params.get("d8_flags", "")
    if not template_js_path:
        return "Error: template_js_path parameter is required"
    required_flags = [
        "--trace-opt",
        "--trace-deopt",
        "--trace-maglev-graph-building",
        "--print-bytecode",
    ]
    for flag in required_flags:
        if flag not in d8_flags:
            d8_flags += f" {flag}"
    return execute_javascript_program(template_js_path, d8_flags)


def _list_d8_flags_executor(params: dict) -> str:
    filter_str = params.get("filter", "")
    if not filter_str:
        return "Error: filter parameter is required"
    d8 = run_command(f'{D8_PATH} --help | grep -i "{filter_str}"')
    return f"Available flags for filter '{filter_str}':\n{d8.stdout}"


def _remove_old_javascript_programs_executor(params: dict) -> str:
    template_js_path = params.get("template_js_path", "")
    if not template_js_path:
        return "Error: template_js_path parameter is required"
    dir_path = os.path.dirname(template_js_path)
    filename = os.path.basename(template_js_path)
    if not dir_path:
        return f"Error: Could not extract directory from {template_js_path}"
    base_name = os.path.splitext(filename)[0]
    parts = base_name.rsplit("-", 1)
    if len(parts) != 2:
        return f"Error: Filename '{filename}' doesn't match expected format"
    template_name = parts[0]
    hash_to_keep = parts[1]
    removed_count = 0
    try:
        for item in os.listdir(dir_path):
            if item.endswith(".js"):
                item_base = os.path.splitext(item)[0]
                if item_base.startswith(f"{template_name}-"):
                    current_hash = item_base.rsplit("-", 1)[-1]
                    if current_hash != hash_to_keep:
                        try:
                            os.remove(os.path.join(dir_path, item))
                            removed_count += 1
                        except OSError:
                            pass
        return f"OK: Removed {removed_count} old JS files for '{template_name}'"
    except Exception as e:
        return f"Error: {e}"


write_program_template_tool = IkaTools(
    name="write_program_template",
    description="Add a new Swift program template to ProgramTemplates.swift and register its weight in ProgramTemplateWeights.swift",
    parameters={"program_template": {"type": "string", "description": "Full Swift template code (WasmProgramTemplate or ProgramTemplate closure)", "required": True}},
    execute_function=_write_program_template_executor,
)

list_program_templates_tool = IkaTools(
    name="list_program_templates",
    description="List all program template names defined in ProgramTemplates.swift",
    parameters={"input": {"type": "string", "description": "Unused. Leave empty.", "required": False}},
    execute_function=_list_program_templates_executor,
)

remove_program_template_tool = IkaTools(
    name="remove_program_template",
    description="Remove a non-default program template from ProgramTemplates.swift (defaults like Codegen100 cannot be removed)",
    parameters={"program_template": {"type": "string", "description": "Template name to remove (e.g. 'MyCustomTemplate')", "required": True}},
    execute_function=_remove_program_template_executor,
)

remove_program_template_weight_tool = IkaTools(
    name="remove_program_template_weight",
    description="Remove the weight entry for a template from ProgramTemplateWeights.swift",
    parameters={"program_template": {"type": "string", "description": "Template name whose weight to remove", "required": True}},
    execute_function=_remove_program_template_weight_executor,
)

edit_template_by_diff_tool = IkaTools(
    name="edit_template_by_diff",
    description="Replace exact text in ProgramTemplates.swift between start_line and end_line. Use when modifying existing template code.",
    parameters={
        "old_text": {"type": "string", "description": "Exact substring to find (must match uniquely in the line range)", "required": True},
        "new_text": {"type": "string", "description": "Replacement text (can be empty to delete)", "required": True},
        "start_line": {"type": "integer", "description": "First line of range (1-indexed)", "required": True},
        "end_line": {"type": "integer", "description": "Last line of range (1-indexed)", "required": True},
    },
    execute_function=_edit_template_by_diff_executor,
)

compile_program_template_tool = IkaTools(
    name="compile_program_template",
    description="Compile a Swift program template to JavaScript via FuzzILTool (writes JS to GENERATED_TEMPLATE_DIR)",
    parameters={"template": {"type": "string", "description": "Template name to compile (e.g. 'Codegen100')", "required": True}},
    execute_function=_compile_program_template_executor,
)

execute_javascript_program_tool = IkaTools(
    name="execute_javascript_program",
    description="Run a JavaScript file with d8 (adds --allow-natives-syntax and trace flags if needed). Use to test compiled templates.",
    parameters={
        "template_js_path": {"type": "string", "description": "Absolute path to the .js file", "required": True},
        "d8_flags": {"type": "string", "description": "Optional d8 flags (e.g. '--trace-opt --trace-deopt')", "required": False},
    },
    execute_function=_execute_javascript_program_executor,
)

list_d8_flags_tool = IkaTools(
    name="list_d8_flags",
    description="List d8 command-line flags that match a grep pattern. Use to discover trace/debug options.",
    parameters={"filter": {"type": "string", "description": "Substring to match in d8 --help (e.g. 'trace', 'maglev')", "required": True}},
    execute_function=_list_d8_flags_executor,
)

remove_old_javascript_programs_tool = IkaTools(
    name="remove_old_javascript_programs",
    description="Delete older JS files for a template, keeping only the one at template_js_path. Use after compile to avoid clutter.",
    parameters={"template_js_path": {"type": "string", "description": "Full path to the .js file to keep (others for same template deleted)", "required": True}},
    execute_function=_remove_old_javascript_programs_executor,
)
