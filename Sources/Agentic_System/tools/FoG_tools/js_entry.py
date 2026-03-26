"""
FoG JS entry tools: search_js_file_name_by_pattern, get_js_entry_data_by_name, get_all_js_file_names, get_random_entry_data.
"""

import json
import random
from pathlib import Path

_agentic_dir = Path(__file__).resolve().parent.parent.parent
_ikacore_src = _agentic_dir / "IkaCore" / "src"
import sys
if str(_ikacore_src) not in sys.path:
    sys.path.insert(0, str(_ikacore_src))

from IkaCore.tools import IkaTools

from ._shared import _load_regressions_once


def _search_js_file_name_by_pattern_executor(params: dict) -> str:
    pattern = str(params.get("pattern", ""))
    if not pattern:
        return "Error: pattern parameter is required"
    data = _load_regressions_once()
    found = [str(k) for k in data.keys() if pattern.lower() in str(k).lower()]
    return "\n".join(found) if found else "No results found"


def _get_js_entry_data_by_name_executor(params: dict) -> str:
    file_name = str(params.get("file_name", ""))
    if not file_name:
        return "Error: file_name parameter is required"
    data = _load_regressions_once()
    entry = data.get(file_name)
    if entry is None:
        for key, value in data.items():
            if str(key) == file_name:
                entry = value
                break
    return json.dumps(entry) if entry else f"No results found for {file_name}"


def _get_all_js_file_names_executor(params: dict) -> str:
    data = _load_regressions_once()
    return json.dumps(list(data.keys()))


def _get_random_entry_data_executor(params: dict) -> str:
    data = _load_regressions_once()
    if not data:
        return "No entries found"
    key = random.choice(list(data.keys()))
    return f"Entry data for {key}\n{json.dumps(data[key])}"


search_js_file_name_by_pattern_tool = IkaTools(
    name="search_js_file_name_by_pattern",
    description="Search the regression corpus for JS file names matching a pattern. Returns matching keys from regressions.json.",
    parameters={"pattern": {"type": "string", "description": "The pattern to search for", "required": True}},
    execute_function=_search_js_file_name_by_pattern_executor,
)

get_js_entry_data_by_name_tool = IkaTools(
    name="get_js_entry_data_by_name",
    description="Fetch the full entry (Swift, FuzzIL, JS, metadata) for a regression test by its exact file name. Use after search_js_file_name_by_pattern.",
    parameters={"file_name": {"type": "string", "description": "The name of the JS file", "required": True}},
    execute_function=_get_js_entry_data_by_name_executor,
)

get_all_js_file_names_tool = IkaTools(
    name="get_all_js_file_names",
    description="Retrieve all JS file names (keys) from the regression corpus. Useful when you need a full list to pick from.",
    parameters={"input": {"type": "string", "description": "No input required", "required": False}},
    execute_function=_get_all_js_file_names_executor,
)

get_random_entry_data_tool = IkaTools(
    name="get_random_entry_data",
    description="Sample a random regression test entry. Use for exploration or when you need a varied starting point.",
    parameters={"input": {"type": "string", "description": "No input required", "required": False}},
    execute_function=_get_random_entry_data_executor,
)
