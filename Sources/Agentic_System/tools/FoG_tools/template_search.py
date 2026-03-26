"""
FoG template search tools: templates.json search, regex, similar, random.
"""

import json
import re
import random
from pathlib import Path

_agentic_dir = Path(__file__).resolve().parent.parent.parent
_ikacore_src = _agentic_dir / "IkaCore" / "src"
import sys
if str(_ikacore_src) not in sys.path:
    sys.path.insert(0, str(_ikacore_src))

from IkaCore.tools import IkaTools

from ._shared import _load_templates_once, fuzz


def _get_all_template_names_from_json_executor(params: dict) -> str:
    data = _load_templates_once()
    return json.dumps(list(data.keys()))


def _get_template_from_json_by_name_executor(params: dict) -> str:
    name = params.get("name", "")
    if not name:
        return "Error: name parameter is required"
    data = _load_templates_once()
    entry = data.get(name)
    if entry is None:
        return "No results found"
    return f"Template data for {name}\n{json.dumps(entry)}"


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


def _get_random_template_swift_executor(params: dict) -> str:
    data = _load_templates_once()
    keys = list(data.keys())
    if not keys:
        return "No templates found"
    name = random.choice(keys)
    return f"Swift template for {name}\n{data[name].get('ProgramTemplateSwift', '')}"


def _get_random_template_fuzzil_executor(params: dict) -> str:
    data = _load_templates_once()
    keys = list(data.keys())
    if not keys:
        return "No templates found"
    name = random.choice(keys)
    return f"FuzzIL template for {name}\n{data[name].get('ProgramTemplateFuzzIL', '')}"


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


get_all_template_names_from_json_tool = IkaTools(
    name="get_all_template_names_from_json",
    description="List all template keys (names) in templates.json. Use before searching or retrieving specific templates.",
    parameters={"input": {"type": "string", "description": "Leave empty", "required": False}},
    execute_function=_get_all_template_names_from_json_executor,
)

get_template_from_json_by_name_tool = IkaTools(
    name="get_template_from_json_by_name",
    description="Retrieve a complete template entry (Swift, FuzzIL, metadata) by its key from templates.json.",
    parameters={"name": {"type": "string", "description": "Exact template key from get_all_template_names or search results", "required": True}},
    execute_function=_get_template_from_json_by_name_executor,
)

search_template_file_json_tool = IkaTools(
    name="search_template_file_json",
    description="Search templates.json for keys matching a substring. Returns matching entries with optional topic filtering.",
    parameters={
        "pattern": {"type": "string", "description": "Substring to match against template key", "required": True},
        "return_topic": {"type": "integer", "description": "0=full, 1=Swift, 2=FuzzIL, 3=Name", "required": False},
    },
    execute_function=_search_template_file_json_executor,
)

search_regex_template_swift_tool = IkaTools(
    name="search_regex_template_swift",
    description="Regex search over Swift program template code in templates.json. Use for pattern-based discovery.",
    parameters={"regex": {"type": "string", "description": "The regex pattern", "required": True}},
    execute_function=_search_regex_template_swift_executor,
)

search_regex_template_fuzzil_tool = IkaTools(
    name="search_regex_template_fuzzil",
    description="Regex search over FuzzIL program template code in templates.json. Use for bytecode-level discovery.",
    parameters={"regex": {"type": "string", "description": "The regex pattern", "required": True}},
    execute_function=_search_regex_template_fuzzil_executor,
)

get_random_template_swift_tool = IkaTools(
    name="get_random_template_swift",
    description="Return a random Swift program template from templates.json. Useful for sampling or exploration.",
    parameters={"input": {"type": "string", "description": "Leave empty", "required": False}},
    execute_function=_get_random_template_swift_executor,
)

get_random_template_fuzzil_tool = IkaTools(
    name="get_random_template_fuzzil",
    description="Return a random FuzzIL program template from templates.json. Useful for sampling or exploration.",
    parameters={"input": {"type": "string", "description": "Leave empty", "required": False}},
    execute_function=_get_random_template_fuzzil_executor,
)

similar_template_swift_tool = IkaTools(
    name="similar_template_swift",
    description="Find Swift templates similar to the given key. Uses fuzzy matching for related examples.",
    parameters={"template_name": {"type": "string", "description": "The template key to compare", "required": True}},
    execute_function=_similar_template_swift_executor,
)

similar_template_fuzzil_tool = IkaTools(
    name="similar_template_fuzzil",
    description="Find FuzzIL templates similar to the given key. Uses fuzzy matching for related examples.",
    parameters={"template_name": {"type": "string", "description": "The template key to compare", "required": True}},
    execute_function=_similar_template_fuzzil_executor,
)
