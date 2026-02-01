#!/usr/bin/env python3
"""
FoG Tools using direct IkaTools class instantiation.
Each tool is defined as an IkaTools instance with an explicit executor function.
"""

import sys
from pathlib import Path
import subprocess
import json
import os
import re
import random
import time

_ikacore_src = Path(__file__).resolve().parent.parent / "IkaCore" / "src"
if str(_ikacore_src) not in sys.path:
    sys.path.insert(0, str(_ikacore_src))

from IkaCore.tools import IkaTools

# Try to import fuzzywuzzy, fallback if not available
try:
    from fuzzywuzzy import fuzz
    FUZZYWUZZY_AVAILABLE = True
except ImportError:
    FUZZYWUZZY_AVAILABLE = False
    class fuzz:
        @staticmethod
        def ratio(a, b):
            # Simple fallback - not as good but functional
            if a == b:
                return 100
            shorter = min(len(a), len(b))
            longer = max(len(a), len(b))
            if longer == 0:
                return 100
            return int((shorter / longer) * 100)

# =============================================================================
# Environment and Helper Functions (self-contained to avoid import issues)
# =============================================================================

# Get required environment variables
V8_PATH = os.getenv('V8_PATH', '')
D8_PATH = os.getenv('D8_PATH', '')
FUZZILLI_PATH = os.getenv('FUZZILLI_PATH', '')
FUZZILLI_TOOL_BIN = os.getenv('FUZZILLI_TOOL_BIN', '')
SWIFT_PATH = os.path.join(FUZZILLI_PATH, 'Sources', 'Fuzzilli') if FUZZILLI_PATH else ''


def run_command(command: str):
    """Execute a shell command and return the result."""
    return subprocess.run(command, shell=True, capture_output=True, text=True)


def get_output(completed_process) -> str:
    """Extract output from a completed subprocess."""
    if not completed_process:
        return ""
    p_stdout = completed_process.stdout if completed_process.stdout else None
    p_stderr = completed_process.stderr if completed_process.stderr else None
    return p_stdout if p_stdout else (p_stderr if p_stderr else "")


def is_valid_regex(pattern: str) -> tuple:
    """Check if a pattern is a valid regex."""
    try:
        re.compile(pattern)
        return True, None
    except re.error as e:
        return False, e


# Directory constants
OUTPUT_DIRECTORY = "/tmp/fog-d8-records"
RAG_DB_DIR = (Path(__file__).parent.parent / "rag_db").resolve()
GENERATED_TEMPLATE_DIR = f"{FUZZILLI_PATH}/Sources/Agentic_System/generated_templates/" if FUZZILLI_PATH else "/tmp/generated_templates/"

try:
    os.makedirs(GENERATED_TEMPLATE_DIR)
except FileExistsError:
    pass

# Cached data
_REGRESSIONS_PATH = (Path(__file__).parent.parent / "regressions" / "regressions.json").resolve()
_REGRESSIONS_CACHE = None
_TEMPLATES_PATH = (Path(__file__).parent.parent / "templates" / "templates.json").resolve()
_TEMPLATES_CACHE = None
RUNTIME_DB_IDS = []


def _load_regressions_once():
    global _REGRESSIONS_CACHE
    if _REGRESSIONS_CACHE is not None:
        return _REGRESSIONS_CACHE
    try:
        with open(_REGRESSIONS_PATH, "r") as f:
            _REGRESSIONS_CACHE = json.load(f)
    except Exception:
        _REGRESSIONS_CACHE = {}
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


def _rag_db_path(rag_db_id: str) -> Path:
    return (RAG_DB_DIR / f"{rag_db_id}.json").resolve()


def _ensure_rag_db_initialized(rag_db_id: str) -> None:
    RAG_DB_DIR.mkdir(parents=True, exist_ok=True)
    path = _rag_db_path(rag_db_id)
    if not path.exists():
        with open(path, "w") as f:
            json.dump({}, f)


def _load_rag_db(rag_db_id: str) -> dict:
    _ensure_rag_db_initialized(rag_db_id)
    path = _rag_db_path(rag_db_id)
    try:
        with open(path, "r") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
            return {}
    except Exception:
        return {}


def _save_rag_db(rag_db_id: str, data: dict) -> None:
    _ensure_rag_db_initialized(rag_db_id)
    path = _rag_db_path(rag_db_id)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


# =============================================================================
# Tool Executors and IkaTools Definitions
# =============================================================================

# --- run_python ---
def _run_python_executor(params: dict) -> str:
    code = params.get("code", "")
    if not code:
        return "Error: code parameter is required"
    return get_output(run_command(f"python3 -c '{code}' | head -n 1000"))

run_python_tool = IkaTools(
    name="run_python",
    description="Execute Python code using the Python interpreter. MAX OUTPUT 1000 lines.",
    parameters={
        "code": {
            "type": "string",
            "description": "The Python code to execute",
            "required": True
        }
    },
    execute_function=_run_python_executor
)


# --- get_v8_path ---
def _get_v8_path_executor(params: dict) -> str:
    return V8_PATH

get_v8_path_tool = IkaTools(
    name="get_v8_path",
    description="Get the V8 source code directory path",
    parameters={
        "input": {
            "type": "string",
            "description": "No input required",
            "required": False
        }
    },
    execute_function=_get_v8_path_executor
)


# --- get_realpath ---
def _get_realpath_executor(params: dict) -> str:
    path = params.get("path", "")
    if not path:
        return "Error: path parameter is required"
    return get_output(run_command(f"cd {V8_PATH} && realpath {path}"))

get_realpath_tool = IkaTools(
    name="get_realpath",
    description="Get the realpath of a given path. MAX OUTPUT 1000 lines.",
    parameters={
        "path": {
            "type": "string",
            "description": "The path to get the realpath of",
            "required": True
        }
    },
    execute_function=_get_realpath_executor
)


# --- tree ---
def _tree_executor(params: dict) -> str:
    options = params.get("options", "")
    opts = options or ""
    parts = opts.split()
    if parts:
        last = parts[-1]
        if not last.startswith("-"):
            candidate = os.path.join(V8_PATH, last)
            if not os.path.isdir(candidate):
                parts[-1] = "."
                opts = " ".join(parts)
    final_opts = opts if opts else "-L 2 -f ."
    return get_output(run_command(f"cd {V8_PATH} && tree {final_opts} | head -n 1000"))

tree_tool = IkaTools(
    name="tree",
    description="Display directory structure using tree command to explore the v8 code base layout. V8_PATH already points to the `src/` directory. MAX OUTPUT 1000 lines.",
    parameters={
        "options": {
            "type": "string",
            "description": "Additional tree command options. Common options: -L NUM (limit depth), -f (show full path), PATH (directory path)",
            "required": False
        }
    },
    execute_function=_tree_executor
)


# --- ripgrep ---
def _ripgrep_executor(params: dict) -> str:
    pattern = params.get("pattern", "")
    options = params.get("options", "")

    if not pattern:
        return "Error: pattern parameter is required"

    valid, error = is_valid_regex(pattern)
    if not valid:
        return f"Invalid regex passed in as pattern with error: {error}"

    if not options:
        return get_output(run_command(f"cd {V8_PATH} && rg '{pattern}' | head -n 10000"))

    parts = options.split()
    flags = []

    i = 0
    while i < len(parts):
        part = parts[i]
        if part.startswith('-'):
            flags.append(part)
            if part in ['--type', '--glob'] and i + 1 < len(parts):
                next_part = parts[i + 1]
                if not next_part.startswith('-') and not next_part.startswith('v8/'):
                    i += 1
                    flags.append(parts[i])
        else:
            flags.append(part)
        i += 1

    flags_str = ' '.join(flags) if flags else ''
    cmd = f"cd {V8_PATH} && rg '{pattern}' {flags_str} | head -n 1000"
    return get_output(run_command(cmd))

ripgrep_tool = IkaTools(
    name="ripgrep",
    description="Search for text patterns in files using ripgrep (rg) for fast text searching. MAX OUTPUT 1000 lines.",
    parameters={
        "pattern": {
            "type": "string",
            "description": "The text or regular expression pattern to search for",
            "required": True
        },
        "options": {
            "type": "string",
            "description": "Additional ripgrep options: --files, --type, --glob, --ignore-case, --line-number, --context=NUM, etc.",
            "required": False
        }
    },
    execute_function=_ripgrep_executor
)


# --- fuzzy_finder ---
def _fuzzy_finder_executor(params: dict) -> str:
    pattern = params.get("pattern", "")
    options = params.get("options", "")

    if not pattern:
        return "Error: pattern parameter is required"

    file_list_cmd = "rg --hidden --no-follow --no-ignore-vcs --files 2>/dev/null"
    return get_output(run_command(f"cd {V8_PATH} && {file_list_cmd} | fzf {options} '{pattern}' | head -n 1000"))

fuzzy_finder_tool = IkaTools(
    name="fuzzy_finder",
    description="Use fuzzy finding to locate files and content by approximate name matching. MAX OUTPUT 1000 lines.",
    parameters={
        "pattern": {
            "type": "string",
            "description": "The search pattern to match against files and content",
            "required": True
        },
        "options": {
            "type": "string",
            "description": "Additional fzf options: --filter, --exact, --delimiter, --nth, --bind",
            "required": False
        }
    },
    execute_function=_fuzzy_finder_executor
)


# --- read_file ---
def _read_file_executor(params: dict) -> str:
    file_path = params.get("file_path", "")
    section = params.get("section")

    if not file_path:
        return "Error: file_path parameter is required"

    if file_path.startswith('v8/'):
        resolved_path = os.path.join(V8_PATH, file_path[3:])
    elif not os.path.isabs(file_path):
        resolved_path = os.path.join(V8_PATH, file_path)
    else:
        resolved_path = file_path

    line_count_result = get_output(run_command(f"cd {V8_PATH} && wc -l '{resolved_path}'"))
    try:
        line_count = int(line_count_result.strip().split()[0])
    except Exception:
        return f"Could not determine number of lines in file. wc -l output: {line_count_result}"

    lines_per_section = 3000
    num_sections = (line_count + lines_per_section - 1) // lines_per_section

    if line_count <= lines_per_section:
        return get_output(run_command(f"cd {V8_PATH} && cat '{resolved_path}'"))

    if section is None:
        try:
            section = int(params.get("section", 0))
        except (ValueError, TypeError):
            section = 0

    if section < 1 or section > num_sections:
        return (
            f"File '{file_path}' has {line_count} lines and is divided into {num_sections} sections "
            f"(each section is 3000 lines).\n"
            f"To read this file, please specify a section number between 1 and {num_sections} "
            f"using the 'section' argument."
        )

    start_line = 1 + (section - 1) * lines_per_section
    end_line = min(start_line + lines_per_section - 1, line_count)
    read_cmd = f"cd {V8_PATH} && sed -n '{start_line},{end_line}p' '{resolved_path}'"
    content = get_output(run_command(read_cmd))
    return (
        f"Showing section {section}/{num_sections} (lines {start_line}-{end_line}) of '{file_path}':\n"
        f"{content}"
    )

read_file_tool = IkaTools(
    name="read_file",
    description="Read a specified text file, limited to 3000 lines per section. Use full paths or paths relative to V8_PATH.",
    parameters={
        "file_path": {
            "type": "string",
            "description": "The full path to the file to be read",
            "required": True
        },
        "section": {
            "type": "integer",
            "description": "Section number (1-indexed) for files with multiple sections",
            "required": False
        }
    },
    execute_function=_read_file_executor
)


# --- lift_fuzzil_to_js ---
def _lift_fuzzil_to_js_executor(params: dict) -> str:
    target = params.get("target", "")
    if not target:
        return "Error: target parameter is required"
    return get_output(run_command(f"{FUZZILLI_TOOL_BIN} --liftToJS {target}"))

lift_fuzzil_to_js_tool = IkaTools(
    name="lift_fuzzil_to_js",
    description="Use FuzzILTool to lift a FuzzIL protobuf to a JavaScript program",
    parameters={
        "target": {
            "type": "string",
            "description": "The path to the target FuzzIL program (.fzil) to be lifted to JS",
            "required": True
        }
    },
    execute_function=_lift_fuzzil_to_js_executor
)


# --- compile_js_to_fuzzil ---
def _compile_js_to_fuzzil_executor(params: dict) -> str:
    target = params.get("target", "")
    if not target:
        return "Error: target parameter is required"
    return get_output(run_command(f"{FUZZILLI_TOOL_BIN} --compile {target}"))

compile_js_to_fuzzil_tool = IkaTools(
    name="compile_js_to_fuzzil",
    description="Use FuzzILTool to compile a JavaScript program to FuzzIL (requires Node.js)",
    parameters={
        "target": {
            "type": "string",
            "description": "The path to the JavaScript program to compile to FuzzIL",
            "required": True
        }
    },
    execute_function=_compile_js_to_fuzzil_executor
)


# --- write_rag_db_id ---
def _write_rag_db_id_executor(params: dict) -> str:
    id_val = params.get("id", "")
    body = params.get("Body", "")
    context = params.get("Context", [])
    explanation = params.get("Explanation", "")
    file_line = params.get("FileLine", "")

    if not id_val:
        return "Error: id parameter is required"

    data = {
        "body": body,
        "context": context if isinstance(context, list) else [],
        "explanation": explanation,
        "file_line": file_line
    }
    _save_rag_db(id_val, data)
    RUNTIME_DB_IDS.append(id_val)
    return f"OK: wrote {id_val} to {_rag_db_path(id_val)}"

write_rag_db_id_tool = IkaTools(
    name="write_rag_db_id",
    description="Write data to the RAG database. Provide detailed explanations and accurate context.",
    parameters={
        "id": {
            "type": "string",
            "description": "The ID of the RAG database entry (e.g., 'jsadd_pipeline')",
            "required": True
        },
        "Body": {
            "type": "string",
            "description": "The body of the entry - should be detailed and interesting code",
            "required": True
        },
        "Context": {
            "type": "array",
            "description": "List of related IDs for context",
            "required": False
        },
        "Explanation": {
            "type": "string",
            "description": "Explanation of why this is interesting and relevant",
            "required": True
        },
        "FileLine": {
            "type": "string",
            "description": "The file lines related to the body",
            "required": True
        }
    },
    execute_function=_write_rag_db_id_executor
)


# --- read_rag_db_id ---
def _read_rag_db_id_executor(params: dict) -> str:
    id_val = params.get("id", "")
    if not id_val:
        return "Error: id parameter is required"
    db = _load_rag_db(id_val)
    return json.dumps(db)

read_rag_db_id_tool = IkaTools(
    name="read_rag_db_id",
    description="Read data from the RAG database by ID",
    parameters={
        "id": {
            "type": "string",
            "description": "The ID of the RAG database entry to read",
            "required": True
        }
    },
    execute_function=_read_rag_db_id_executor
)


# --- get_runtime_db_ids ---
def _get_runtime_db_ids_executor(params: dict) -> str:
    return json.dumps(RUNTIME_DB_IDS)

get_runtime_db_ids_tool = IkaTools(
    name="get_runtime_db_ids",
    description="Get the list of RAG database IDs created during this runtime session",
    parameters={
        "input": {
            "type": "string",
            "description": "No input required",
            "required": False
        }
    },
    execute_function=_get_runtime_db_ids_executor
)


# --- search_js_file_name_by_pattern ---
def _search_js_file_name_by_pattern_executor(params: dict) -> str:
    pattern = params.get("pattern", "")
    if not pattern:
        return "Error: pattern parameter is required"

    data = _load_regressions_once()
    found = []
    for key in data.keys():
        if pattern.lower() in key.lower():
            found.append(key)

    return "\n".join(found) if found else "No results found"

search_js_file_name_by_pattern_tool = IkaTools(
    name="search_js_file_name_by_pattern",
    description="Search the regressions.json file for file names matching a pattern",
    parameters={
        "pattern": {
            "type": "string",
            "description": "The pattern to search for in file names",
            "required": True
        }
    },
    execute_function=_search_js_file_name_by_pattern_executor
)


# --- get_js_entry_data_by_name ---
def _get_js_entry_data_by_name_executor(params: dict) -> str:
    file_name = params.get("file_name", "")
    if not file_name:
        return "Error: file_name parameter is required"

    data = _load_regressions_once()
    entry = data.get(file_name)
    return json.dumps(entry) if entry else f"No results found for {file_name}"

get_js_entry_data_by_name_tool = IkaTools(
    name="get_js_entry_data_by_name",
    description="Get the entry data for a given JS file name from regressions.json",
    parameters={
        "file_name": {
            "type": "string",
            "description": "The name of the JS file to get entry data for",
            "required": True
        }
    },
    execute_function=_get_js_entry_data_by_name_executor
)


# --- get_all_js_file_names ---
def _get_all_js_file_names_executor(params: dict) -> str:
    data = _load_regressions_once()
    return json.dumps(list(data.keys()))

get_all_js_file_names_tool = IkaTools(
    name="get_all_js_file_names",
    description="Get all JS file names from the regressions.json file",
    parameters={
        "input": {
            "type": "string",
            "description": "No input required",
            "required": False
        }
    },
    execute_function=_get_all_js_file_names_executor
)


# --- get_random_entry_data ---
def _get_random_entry_data_executor(params: dict) -> str:
    data = _load_regressions_once()
    if not data:
        return "No entries found"
    key = random.choice(list(data.keys()))
    return f"Entry data for {key}\n{json.dumps(data[key])}"

get_random_entry_data_tool = IkaTools(
    name="get_random_entry_data",
    description="Get a random entry from the regressions.json file",
    parameters={
        "input": {
            "type": "string",
            "description": "No input required",
            "required": False
        }
    },
    execute_function=_get_random_entry_data_executor
)


# --- get_all_template_names_from_json ---
def _get_all_template_names_from_json_executor(params: dict) -> str:
    data = _load_templates_once()
    return json.dumps(list(data.keys()))

get_all_template_names_from_json_tool = IkaTools(
    name="get_all_template_names_from_json",
    description="List all template keys in templates.json",
    parameters={
        "input": {
            "type": "string",
            "description": "No input required",
            "required": False
        }
    },
    execute_function=_get_all_template_names_from_json_executor
)


# --- get_template_from_json_by_name ---
def _get_template_from_json_by_name_executor(params: dict) -> str:
    name = params.get("name", "")
    if not name:
        return "Error: name parameter is required"

    data = _load_templates_once()
    entry = data.get(name)
    if entry is None:
        return "No results found"
    return f"Template data for {name}\n{json.dumps(entry)}"

get_template_from_json_by_name_tool = IkaTools(
    name="get_template_from_json_by_name",
    description="Get full template entry by key from templates.json",
    parameters={
        "name": {
            "type": "string",
            "description": "The template key to retrieve",
            "required": True
        }
    },
    execute_function=_get_template_from_json_by_name_executor
)


# --- search_template_file_json ---
def _search_template_file_json_executor(params: dict) -> str:
    pattern = params.get("pattern", "")
    return_topic = params.get("return_topic", 0)

    if not pattern:
        return "Error: pattern parameter is required"

    try:
        return_topic = int(return_topic)
    except (ValueError, TypeError):
        return_topic = 0

    data = _load_templates_once()
    for key, value in data.items():
        if pattern in key:
            if return_topic == 0:
                return json.dumps(value)
            elif return_topic == 1:
                return value.get("ProgramTemplateSwift", "")
            elif return_topic == 2:
                return value.get("ProgramTemplateFuzzIL", "")
            elif return_topic == 3:
                return value.get("ProgramTemplateName", "")
    return "No results found"

search_template_file_json_tool = IkaTools(
    name="search_template_file_json",
    description="Search templates.json for a given key pattern",
    parameters={
        "pattern": {
            "type": "string",
            "description": "Substring to match against template key",
            "required": True
        },
        "return_topic": {
            "type": "integer",
            "description": "0=full entry, 1=ProgramTemplateSwift, 2=ProgramTemplateFuzzIL, 3=ProgramTemplateName",
            "required": False
        }
    },
    execute_function=_search_template_file_json_executor
)


# --- search_regex_template_swift ---
def _search_regex_template_swift_executor(params: dict) -> str:
    regex = params.get("regex", "")
    if not regex:
        return "Error: regex parameter is required"

    try:
        pattern = re.compile(regex, re.MULTILINE)
    except re.error as e:
        return f"Invalid regex: {e}"

    results = []
    data = _load_templates_once()
    for key, value in data.items():
        txt = value.get("ProgramTemplateSwift", "")
        if pattern.search(txt):
            results.append(f"Swift template for {key}\n{txt}\n")

    return "\n".join(results) if results else "No matches found"

search_regex_template_swift_tool = IkaTools(
    name="search_regex_template_swift",
    description="Regex search over ProgramTemplateSwift in templates.json",
    parameters={
        "regex": {
            "type": "string",
            "description": "The regex pattern to search for",
            "required": True
        }
    },
    execute_function=_search_regex_template_swift_executor
)


# --- search_regex_template_fuzzil ---
def _search_regex_template_fuzzil_executor(params: dict) -> str:
    regex = params.get("regex", "")
    if not regex:
        return "Error: regex parameter is required"

    try:
        pattern = re.compile(regex, re.MULTILINE)
    except re.error as e:
        return f"Invalid regex: {e}"

    results = []
    data = _load_templates_once()
    for key, value in data.items():
        txt = value.get("ProgramTemplateFuzzIL", "")
        if pattern.search(txt):
            results.append(f"FuzzIL template for {key}\n{txt}\n")

    return "\n".join(results) if results else "No matches found"

search_regex_template_fuzzil_tool = IkaTools(
    name="search_regex_template_fuzzil",
    description="Regex search over ProgramTemplateFuzzIL in templates.json",
    parameters={
        "regex": {
            "type": "string",
            "description": "The regex pattern to search for",
            "required": True
        }
    },
    execute_function=_search_regex_template_fuzzil_executor
)


# --- get_random_template_swift ---
def _get_random_template_swift_executor(params: dict) -> str:
    data = _load_templates_once()
    keys = list(data.keys())
    if not keys:
        return "No templates found"
    name = random.choice(keys)
    return f"Swift template for {name}\n{data[name].get('ProgramTemplateSwift', '')}"

get_random_template_swift_tool = IkaTools(
    name="get_random_template_swift",
    description="Return a random ProgramTemplateSwift from templates.json",
    parameters={
        "input": {
            "type": "string",
            "description": "No input required",
            "required": False
        }
    },
    execute_function=_get_random_template_swift_executor
)


# --- get_random_template_fuzzil ---
def _get_random_template_fuzzil_executor(params: dict) -> str:
    data = _load_templates_once()
    keys = list(data.keys())
    if not keys:
        return "No templates found"
    name = random.choice(keys)
    return f"FuzzIL template for {name}\n{data[name].get('ProgramTemplateFuzzIL', '')}"

get_random_template_fuzzil_tool = IkaTools(
    name="get_random_template_fuzzil",
    description="Return a random ProgramTemplateFuzzIL from templates.json",
    parameters={
        "input": {
            "type": "string",
            "description": "No input required",
            "required": False
        }
    },
    execute_function=_get_random_template_fuzzil_executor
)


# --- similar_template_swift ---
def _similar_template_swift_executor(params: dict) -> str:
    template_name = params.get("template_name", "")
    if not template_name:
        return "Error: template_name parameter is required"

    data = _load_templates_once()
    if template_name not in data:
        return "No results found"

    base = data[template_name].get("ProgramTemplateSwift", "")
    sims = []
    for key, value in data.items():
        if key == template_name:
            continue
        score = fuzz.ratio(base, value.get("ProgramTemplateSwift", ""))
        if score > 80:
            sims.append((key, score))
    sims.sort(key=lambda x: x[1], reverse=True)
    return f"Most similar Swift templates to {template_name}: {str(sims)}"

similar_template_swift_tool = IkaTools(
    name="similar_template_swift",
    description="Find similar ProgramTemplateSwift entries to the given template key",
    parameters={
        "template_name": {
            "type": "string",
            "description": "The template key to compare against",
            "required": True
        }
    },
    execute_function=_similar_template_swift_executor
)


# --- similar_template_fuzzil ---
def _similar_template_fuzzil_executor(params: dict) -> str:
    template_name = params.get("template_name", "")
    if not template_name:
        return "Error: template_name parameter is required"

    data = _load_templates_once()
    if template_name not in data:
        return "No results found"

    base = data[template_name].get("ProgramTemplateFuzzIL", "")
    sims = []
    for key, value in data.items():
        if key == template_name:
            continue
        score = fuzz.ratio(base, value.get("ProgramTemplateFuzzIL", ""))
        if score > 80:
            sims.append((key, score))
    sims.sort(key=lambda x: x[1], reverse=True)
    return f"Most similar FuzzIL templates to {template_name}: {str(sims)}"

similar_template_fuzzil_tool = IkaTools(
    name="similar_template_fuzzil",
    description="Find similar ProgramTemplateFuzzIL entries to the given template key",
    parameters={
        "template_name": {
            "type": "string",
            "description": "The template key to compare against",
            "required": True
        }
    },
    execute_function=_similar_template_fuzzil_executor
)


# --- swift_fuzzy_finder ---
def _swift_fuzzy_finder_executor(params: dict) -> str:
    pattern = params.get("pattern", "")
    options = params.get("options", "")

    if not pattern:
        return "Error: pattern parameter is required"

    file_list_cmd = "rg --hidden --no-follow --no-ignore-vcs --files 2>/dev/null"
    return get_output(run_command(f"cd {SWIFT_PATH} && {file_list_cmd} | fzf {options} '{pattern}' | head -n 1000"))

swift_fuzzy_finder_tool = IkaTools(
    name="swift_fuzzy_finder",
    description="Use fuzzy finding to locate files in the Fuzzilli Swift codebase. MAX OUTPUT 1000 lines.",
    parameters={
        "pattern": {
            "type": "string",
            "description": "The search pattern to match against files",
            "required": True
        },
        "options": {
            "type": "string",
            "description": "Additional fzf options",
            "required": False
        }
    },
    execute_function=_swift_fuzzy_finder_executor
)


# --- swift_tree ---
def _swift_tree_executor(params: dict) -> str:
    options = params.get("options", "")
    opts = options or ""
    parts = opts.split()
    if parts:
        last = parts[-1]
        if not last.startswith("-"):
            candidate = os.path.join(SWIFT_PATH, last)
            if not os.path.isdir(candidate):
                parts[-1] = "."
                opts = " ".join(parts)
    final_opts = opts if opts else "-L 2 -f ."
    return get_output(run_command(f"cd {SWIFT_PATH} && tree {final_opts} | head -n 1000"))

swift_tree_tool = IkaTools(
    name="swift_tree",
    description="Display directory structure of the Fuzzilli Swift codebase. MAX OUTPUT 1000 lines.",
    parameters={
        "options": {
            "type": "string",
            "description": "Additional tree command options",
            "required": False
        }
    },
    execute_function=_swift_tree_executor
)


# --- swift_ripgrep ---
def _swift_ripgrep_executor(params: dict) -> str:
    pattern = params.get("pattern", "")
    options = params.get("options", "")

    if not pattern:
        return "Error: pattern parameter is required"

    valid, error = is_valid_regex(pattern)
    if not valid:
        return f"Invalid regex: {error}"

    if "ProgramTemplate" in pattern:
        resolved_path = os.path.join(SWIFT_PATH, "CodeGen")
    else:
        resolved_path = SWIFT_PATH

    if not options:
        return get_output(run_command(f"cd {resolved_path} && rg '{pattern}' | head -n 10000"))

    parts = options.split()
    flags = []
    i = 0
    while i < len(parts):
        part = parts[i]
        if part.startswith('-'):
            flags.append(part)
            if part in ['--type', '--glob'] and i + 1 < len(parts):
                next_part = parts[i + 1]
                if not next_part.startswith('-') and not next_part.startswith('v8/'):
                    i += 1
                    flags.append(parts[i])
        else:
            flags.append(part)
        i += 1

    flags_str = ' '.join(flags) if flags else ''
    cmd = f"cd {resolved_path} && rg '{pattern}' {flags_str} | head -n 1000"
    return get_output(run_command(cmd))

swift_ripgrep_tool = IkaTools(
    name="swift_ripgrep",
    description="Search for patterns in the Fuzzilli Swift codebase using ripgrep. MAX OUTPUT 1000 lines.",
    parameters={
        "pattern": {
            "type": "string",
            "description": "The text or regex pattern to search for",
            "required": True
        },
        "options": {
            "type": "string",
            "description": "Additional ripgrep options",
            "required": False
        }
    },
    execute_function=_swift_ripgrep_executor
)


# --- swift_read_file ---
def _swift_read_file_executor(params: dict) -> str:
    file_path = params.get("file_path", "")
    section = params.get("section")

    if not file_path:
        return "Error: file_path parameter is required"

    if file_path.startswith('Sources/') or file_path.startswith('Fuzzilli/'):
        resolved_path = os.path.join(FUZZILLI_PATH, file_path)
    elif not os.path.isabs(file_path):
        resolved_path = os.path.join(SWIFT_PATH, file_path)
    else:
        resolved_path = file_path

    line_count_result = get_output(run_command(f"wc -l '{resolved_path}'"))
    try:
        line_count = int(line_count_result.strip().split()[0])
    except Exception:
        return f"Could not determine file line count. wc -l output: {line_count_result}"

    lines_per_section = 3000
    num_sections = (line_count + lines_per_section - 1) // lines_per_section

    if line_count <= lines_per_section:
        return get_output(run_command(f"cat '{resolved_path}'"))

    if section is None:
        try:
            section = int(params.get("section", 0))
        except (ValueError, TypeError):
            section = 0

    if section < 1 or section > num_sections:
        return (
            f"File '{file_path}' has {line_count} lines and is divided into {num_sections} sections.\n"
            f"Please specify a section number between 1 and {num_sections}."
        )

    start_line = 1 + (section - 1) * lines_per_section
    end_line = min(start_line + lines_per_section - 1, line_count)
    content = get_output(run_command(f"sed -n '{start_line},{end_line}p' '{resolved_path}'"))
    return f"Section {section}/{num_sections} (lines {start_line}-{end_line}) of '{file_path}':\n{content}"

swift_read_file_tool = IkaTools(
    name="swift_read_file",
    description="Read a file from the Fuzzilli Swift codebase, limited to 3000 lines per section.",
    parameters={
        "file_path": {
            "type": "string",
            "description": "The path to the file to read",
            "required": True
        },
        "section": {
            "type": "integer",
            "description": "Section number for multi-section files",
            "required": False
        }
    },
    execute_function=_swift_read_file_executor
)


# --- write_program_template ---
def _write_program_template_executor(params: dict) -> str:
    program_template = params.get("program_template", "")
    if not program_template:
        return "Error: program_template parameter is required"

    program_templates_file = os.path.join(SWIFT_PATH, "CodeGen", "ProgramTemplates.swift")

    if not os.path.exists(program_templates_file):
        return f"Error: ProgramTemplates.swift not found at {program_templates_file}"

    try:
        with open(program_templates_file, 'r') as f:
            content = f.read()
    except Exception as e:
        return f"Error reading ProgramTemplates.swift: {e}"

    content = content.rstrip()
    if not content.endswith(']'):
        return "Error: ProgramTemplates.swift does not end with closing bracket"

    template_code = program_template.strip()
    if not template_code.endswith(','):
        template_code += ','

    content = content[:-1] + '\n\n    ' + template_code + '\n]'

    try:
        with open(program_templates_file, 'w') as f:
            f.write(content)
        ret = f"OK: Successfully wrote program template to {program_templates_file}"
    except Exception as e:
        return f"Error writing to ProgramTemplates.swift: {e}"

    # Update weights
    program_template_weights_file = os.path.join(SWIFT_PATH, "CodeGen", "ProgramTemplateWeights.swift")
    template_name_pattern = r'(?:WasmProgramTemplate|ProgramTemplate)\s*\("([^"]+)"\)'
    name_match = re.search(template_name_pattern, program_template)
    if not name_match:
        return ret + "\nWarning: Could not extract template name to update weights."
    template_name = name_match.group(1)

    if not os.path.exists(program_template_weights_file):
        return ret + f"\nWarning: ProgramTemplateWeights.swift not found"

    try:
        with open(program_template_weights_file, 'r') as f:
            content_weights = f.read()

        content_weights = content_weights.rstrip()
        if content_weights.endswith(']'):
            new_weight_entry = f'\n\t"{template_name}": 2,'
            content_weights = content_weights[:-1] + new_weight_entry + '\n]'

            with open(program_template_weights_file, 'w') as f:
                f.write(content_weights)
            return ret + f"\nOK: Successfully wrote template weight"
    except Exception as e:
        return ret + f"\nWarning: Error updating weights: {e}"

    return ret

write_program_template_tool = IkaTools(
    name="write_program_template",
    description="Write a program template to ProgramTemplates.swift and add its weight entry",
    parameters={
        "program_template": {
            "type": "string",
            "description": "The complete Swift program template code to add",
            "required": True
        }
    },
    execute_function=_write_program_template_executor
)


# --- list_program_templates ---
def _list_program_templates_executor(params: dict) -> str:
    program_templates_file = os.path.join(SWIFT_PATH, "CodeGen", "ProgramTemplates.swift")

    if not os.path.exists(program_templates_file):
        return f"Error: ProgramTemplates.swift not found at {program_templates_file}"

    try:
        with open(program_templates_file, 'r') as f:
            content = f.read()
    except Exception as e:
        return f"Error reading ProgramTemplates.swift: {e}"

    pattern = r'(?:WasmProgramTemplate|ProgramTemplate)\s*\("([^"]+)"\)'
    program_templates = re.findall(pattern, content)

    return f"Found program templates: {program_templates}"

list_program_templates_tool = IkaTools(
    name="list_program_templates",
    description="List all existing program templates in ProgramTemplates.swift",
    parameters={
        "input": {
            "type": "string",
            "description": "No input required",
            "required": False
        }
    },
    execute_function=_list_program_templates_executor
)


# --- remove_program_template ---
def _remove_program_template_executor(params: dict) -> str:
    program_template = params.get("program_template", "")
    if not program_template:
        return "Error: program_template parameter is required"

    default_templates = ['Codegen100', 'Codegen50', 'WasmCodegen50', 'WasmCodegen100',
                        'MixedJsAndWasm1', 'MixedJsAndWasm2', 'JSPI',
                        'ThrowInWasmCatchInJS', 'WasmReturnCalls', 'JIT1Function',
                        'JIT2Functions', 'JITTrickyFunction', 'JSONFuzzer']

    if program_template in default_templates:
        return f"Cannot remove default template. Defaults: {default_templates}"

    program_templates_file = os.path.join(SWIFT_PATH, "CodeGen", "ProgramTemplates.swift")

    if not os.path.exists(program_templates_file):
        return f"Error: ProgramTemplates.swift not found"

    try:
        with open(program_templates_file, 'r') as f:
            content = f.read()
    except Exception as e:
        return f"Error reading file: {e}"

    block_start_pattern = re.compile(
        r'^\s*(?:WasmProgramTemplate|ProgramTemplate)\s*\(\s*"' + re.escape(program_template) + r'"\s*\)\s*\{',
        re.MULTILINE
    )
    start_match = block_start_pattern.search(content)
    if not start_match:
        return f"Error: Template '{program_template}' not found"

    start_index = start_match.start()
    brace_count = 1
    end_index = -1
    for i in range(start_match.end(), len(content)):
        if content[i] == '{':
            brace_count += 1
        elif content[i] == '}':
            brace_count -= 1
            if brace_count == 0:
                end_index = i
                break

    if end_index == -1:
        return f"Error: Could not find closing brace for template '{program_template}'"

    separator_after_match = re.search(r'^\s*,\s*', content[end_index + 1:], re.MULTILINE | re.DOTALL)
    if separator_after_match:
        end_of_block = end_index + 1 + separator_after_match.end()
    else:
        return f"Failed to remove template - separator not found"

    content = content[:start_index] + content[end_of_block:]

    try:
        with open(program_templates_file, 'w') as f:
            f.write(content)
        return f"OK: Removed template {program_template}"
    except Exception as e:
        return f"Error writing file: {e}"

remove_program_template_tool = IkaTools(
    name="remove_program_template",
    description="Remove a dynamically-added program template from ProgramTemplates.swift",
    parameters={
        "program_template": {
            "type": "string",
            "description": "The name of the template to remove",
            "required": True
        }
    },
    execute_function=_remove_program_template_executor
)


# --- remove_program_template_weight ---
def _remove_program_template_weight_executor(params: dict) -> str:
    program_template = params.get("program_template", "")
    if not program_template:
        return "Error: program_template parameter is required"

    default_templates = ['Codegen100', 'Codegen50', 'WasmCodegen50', 'WasmCodegen100',
                        'MixedJsAndWasm1', 'MixedJsAndWasm2', 'JSPI',
                        'ThrowInWasmCatchInJS', 'WasmReturnCalls', 'JIT1Function',
                        'JIT2Functions', 'JITTrickyFunction', 'JSONFuzzer']

    if program_template in default_templates:
        return f"Cannot remove default template weight"

    weights_file = os.path.join(SWIFT_PATH, "CodeGen", "ProgramTemplateWeights.swift")

    if not os.path.exists(weights_file):
        return f"Error: ProgramTemplateWeights.swift not found"

    try:
        with open(weights_file, 'r') as f:
            content = f.read()

        pattern = re.compile(r'^\s*"' + re.escape(program_template) + r'"\s*:\s*\d+\s*,\s*$', re.MULTILINE)
        content = pattern.sub('', content)
        content = re.sub(r'\n\s*\n', '\n', content)

        with open(weights_file, 'w') as f:
            f.write(content)
        return f"OK: Removed weight for {program_template}"
    except Exception as e:
        return f"Error: {e}"

remove_program_template_weight_tool = IkaTools(
    name="remove_program_template_weight",
    description="Remove a program template weight from ProgramTemplateWeights.swift",
    parameters={
        "program_template": {
            "type": "string",
            "description": "The name of the template whose weight to remove",
            "required": True
        }
    },
    execute_function=_remove_program_template_weight_executor
)


# --- edit_template_by_diff ---
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
        with open(filepath, 'r') as f:
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

    section_lines = lines[start_line - 1:end_line]
    section_content = '\n'.join(section_lines)

    if old_text not in section_content:
        return f"Error: old_text not found in lines {start_line}-{end_line}"

    if section_content.count(old_text) > 1:
        return f"Error: Multiple occurrences of old_text found. Make it more specific."

    updated_section = section_content.replace(old_text, new_text)
    updated_lines = lines[:start_line - 1] + updated_section.split('\n') + lines[end_line:]
    updated_content = '\n'.join(updated_lines)

    if content.endswith('\n'):
        updated_content += '\n'

    try:
        with open(filepath, 'w') as f:
            f.write(updated_content)
        return f"OK: Updated lines {start_line}-{end_line}"
    except Exception as e:
        return f"Error writing file: {e}"

edit_template_by_diff_tool = IkaTools(
    name="edit_template_by_diff",
    description="Edit ProgramTemplates.swift by finding and replacing exact text within a line range",
    parameters={
        "old_text": {
            "type": "string",
            "description": "The exact text to find and replace",
            "required": True
        },
        "new_text": {
            "type": "string",
            "description": "The text to replace with",
            "required": True
        },
        "start_line": {
            "type": "integer",
            "description": "Start line number (1-indexed)",
            "required": True
        },
        "end_line": {
            "type": "integer",
            "description": "End line number (1-indexed)",
            "required": True
        }
    },
    execute_function=_edit_template_by_diff_executor
)


# --- compile_program_template ---
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

compile_program_template_tool = IkaTools(
    name="compile_program_template",
    description="Compile a program template to JavaScript using FuzzILTool",
    parameters={
        "template": {
            "type": "string",
            "description": "The name of the program template to compile",
            "required": True
        }
    },
    execute_function=_compile_program_template_executor
)


# --- execute_javascript_program ---
def _execute_javascript_program_executor(params: dict) -> str:
    template_js_path = params.get("template_js_path", "")
    d8_flags = params.get("d8_flags", "")

    if not template_js_path:
        return "Error: template_js_path parameter is required"

    if "--allow-natives-syntax" not in d8_flags:
        d8_flags += " --allow-natives-syntax"

    d8 = run_command(f"{D8_PATH} {d8_flags} {template_js_path}")
    return f"Program execution result:\n{d8.stderr}\n{d8.stdout}"

execute_javascript_program_tool = IkaTools(
    name="execute_javascript_program",
    description="Execute a JavaScript program generated from a program template using d8",
    parameters={
        "template_js_path": {
            "type": "string",
            "description": "Path to the JavaScript program",
            "required": True
        },
        "d8_flags": {
            "type": "string",
            "description": "Additional d8 command line flags",
            "required": False
        }
    },
    execute_function=_execute_javascript_program_executor
)


# --- list_d8_flags ---
def _list_d8_flags_executor(params: dict) -> str:
    filter_str = params.get("filter", "")
    if not filter_str:
        return "Error: filter parameter is required"

    d8 = run_command(f'{D8_PATH} --help | grep -i "{filter_str}"')
    return f"Available flags for filter '{filter_str}':\n{d8.stdout}"

list_d8_flags_tool = IkaTools(
    name="list_d8_flags",
    description="List available d8 flags matching a filter",
    parameters={
        "filter": {
            "type": "string",
            "description": "Filter string to grep for in d8 help",
            "required": True
        }
    },
    execute_function=_list_d8_flags_executor
)


# --- remove_old_javascript_programs ---
def _remove_old_javascript_programs_executor(params: dict) -> str:
    template_js_path = params.get("template_js_path", "")
    if not template_js_path:
        return "Error: template_js_path parameter is required"

    dir_path = os.path.dirname(template_js_path)
    filename = os.path.basename(template_js_path)

    if not dir_path:
        return f"Error: Could not extract directory from {template_js_path}"

    base_name = os.path.splitext(filename)[0]
    parts = base_name.rsplit('-', 1)

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
                    current_hash = item_base.rsplit('-', 1)[-1]
                    if current_hash != hash_to_keep:
                        try:
                            os.remove(os.path.join(dir_path, item))
                            removed_count += 1
                        except OSError:
                            pass

        return f"OK: Removed {removed_count} old JS files for '{template_name}'"
    except Exception as e:
        return f"Error: {e}"

remove_old_javascript_programs_tool = IkaTools(
    name="remove_old_javascript_programs",
    description="Remove older generated JavaScript programs, keeping only the specified one",
    parameters={
        "template_js_path": {
            "type": "string",
            "description": "Full path to the JavaScript file to KEEP",
            "required": True
        }
    },
    execute_function=_remove_old_javascript_programs_executor
)


# =============================================================================
# Export all tools as a dictionary for easy access
# =============================================================================

ALL_FOG_TOOLS = {
    "run_python": run_python_tool,
    "get_v8_path": get_v8_path_tool,
    "get_realpath": get_realpath_tool,
    "tree": tree_tool,
    "ripgrep": ripgrep_tool,
    "fuzzy_finder": fuzzy_finder_tool,
    "read_file": read_file_tool,
    "lift_fuzzil_to_js": lift_fuzzil_to_js_tool,
    "compile_js_to_fuzzil": compile_js_to_fuzzil_tool,
    "write_rag_db_id": write_rag_db_id_tool,
    "read_rag_db_id": read_rag_db_id_tool,
    "get_runtime_db_ids": get_runtime_db_ids_tool,
    "search_js_file_name_by_pattern": search_js_file_name_by_pattern_tool,
    "get_js_entry_data_by_name": get_js_entry_data_by_name_tool,
    "get_all_js_file_names": get_all_js_file_names_tool,
    "get_random_entry_data": get_random_entry_data_tool,
    "get_all_template_names_from_json": get_all_template_names_from_json_tool,
    "get_template_from_json_by_name": get_template_from_json_by_name_tool,
    "search_template_file_json": search_template_file_json_tool,
    "search_regex_template_swift": search_regex_template_swift_tool,
    "search_regex_template_fuzzil": search_regex_template_fuzzil_tool,
    "get_random_template_swift": get_random_template_swift_tool,
    "get_random_template_fuzzil": get_random_template_fuzzil_tool,
    "similar_template_swift": similar_template_swift_tool,
    "similar_template_fuzzil": similar_template_fuzzil_tool,
    "swift_fuzzy_finder": swift_fuzzy_finder_tool,
    "swift_tree": swift_tree_tool,
    "swift_ripgrep": swift_ripgrep_tool,
    "swift_read_file": swift_read_file_tool,
    "write_program_template": write_program_template_tool,
    "list_program_templates": list_program_templates_tool,
    "remove_program_template": remove_program_template_tool,
    "remove_program_template_weight": remove_program_template_weight_tool,
    "edit_template_by_diff": edit_template_by_diff_tool,
    "compile_program_template": compile_program_template_tool,
    "execute_javascript_program": execute_javascript_program_tool,
    "list_d8_flags": list_d8_flags_tool,
    "remove_old_javascript_programs": remove_old_javascript_programs_tool,
}
