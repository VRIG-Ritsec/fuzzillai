#!/usr/bin/env python3
"""
Common Tools using direct IkaTools class instantiation.
Each tool is defined as an IkaTools instance with an explicit executor function.
"""

import sys
import os
import json
import subprocess
import re
from pathlib import Path

_ikacore_src = Path(__file__).resolve().parent.parent / "IkaCore" / "src"
if str(_ikacore_src) not in sys.path:
    sys.path.insert(0, str(_ikacore_src))

from IkaCore.tools import IkaTools

# =============================================================================
# Environment Variables
# =============================================================================
V8_PATH = os.getenv('V8_PATH', '')
D8_PATH = os.getenv('D8_PATH', '')
FUZZILLI_PATH = os.getenv('FUZZILLI_PATH', '')
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


# =============================================================================
# Try to import CFG/Call Graph data from common_tools (gracefully handle failure)
# =============================================================================
try:
    from tools.common_tools import CFG_MAP, CALL_GRAPH_MAP, CALL_GRAPH_HASHMAP
except ImportError:
    # Fallback: empty data structures if common_tools can't be imported
    CFG_MAP = {}
    CALL_GRAPH_MAP = {}
    CALL_GRAPH_HASHMAP = {}

# Try to import DuckDuckGo search
try:
    from duckduckgo_search import DDGS
    DDGS_AVAILABLE = True
except Exception:
    DDGS_AVAILABLE = False
    DDGS = None


# =============================================================================
# Tool Executors and IkaTools Definitions
# =============================================================================

# --- web_search ---
def _web_search_executor(params: dict) -> str:
    query = params.get("query", "")

    if not query:
        return "Error: query parameter is required"

    if not DDGS_AVAILABLE or DDGS is None:
        return "Web search is not configured. Install duckduckgo_search to enable."

    try:
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=5):
                title = r.get("title", "")
                href = r.get("href", r.get("link", ""))
                body = r.get("body", r.get("snippet", ""))
                results.append(f"- {title}\n  {href}\n  {body}")

        if not results:
            return "No results found."
        return "\n\n".join(results)
    except Exception as e:
        return f"Error performing web search: {e}"

web_search_tool = IkaTools(
    name="web_search",
    description="Search the internet for information. Use for V8 engine info via blog posts, papers, etc. NOT for V8 source code search.",
    parameters={
        "query": {
            "type": "string",
            "description": "The search query",
            "required": True
        }
    },
    execute_function=_web_search_executor
)


# --- get_cfg_for ---
def _get_cfg_for_executor(params: dict) -> str:
    function_name = params.get("function_name", "")

    if not function_name:
        return json.dumps({"error": "function_name parameter is required"})

    cfg = CFG_MAP.get(function_name)
    return json.dumps(cfg, indent=2) if cfg else json.dumps({"error": f"CFG not found for {function_name}"})

get_cfg_for_tool = IkaTools(
    name="get_cfg_for",
    description="Get the Control Flow Graph (CFG) for a fully qualified function name in V8",
    parameters={
        "function_name": {
            "type": "string",
            "description": "The fully qualified function name to get the CFG for",
            "required": True
        }
    },
    execute_function=_get_cfg_for_executor
)


# --- get_call_graph_hashmap ---
def _get_call_graph_hashmap_executor(params: dict) -> str:
    try:
        return json.dumps(CALL_GRAPH_HASHMAP, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Failed to serialize call graph hashmap: {e}"})

get_call_graph_hashmap_tool = IkaTools(
    name="get_call_graph_hashmap",
    description="Return the in-memory call graph hashmap with entry/exit nodes and locations",
    parameters={
        "input": {
            "type": "string",
            "description": "No input required",
            "required": False
        }
    },
    execute_function=_get_call_graph_hashmap_executor
)


# --- find_functions_by_simple_name ---
def _find_functions_by_simple_name_executor(params: dict) -> str:
    simple_name = params.get("simple_name", "")

    if not simple_name:
        return json.dumps([])

    matches = []
    for full_name, info in CALL_GRAPH_MAP.items():
        if info.get("function_name") == simple_name or simple_name in full_name:
            matches.append(full_name)

    return json.dumps(matches)

find_functions_by_simple_name_tool = IkaTools(
    name="find_functions_by_simple_name",
    description="Find all fully qualified functions matching a simple function name in V8",
    parameters={
        "simple_name": {
            "type": "string",
            "description": "The simple function name to search for",
            "required": True
        }
    },
    execute_function=_find_functions_by_simple_name_executor
)


# --- find_functions_by_fully_qualified_name ---
def _find_functions_by_fully_qualified_name_executor(params: dict) -> str:
    fully_qualified_name = params.get("fully_qualified_name", "")

    if not fully_qualified_name:
        return json.dumps([])

    matches = []
    for full_name in CALL_GRAPH_MAP.keys():
        if full_name == fully_qualified_name:
            matches.append(full_name)

    return json.dumps(matches)

find_functions_by_fully_qualified_name_tool = IkaTools(
    name="find_functions_by_fully_qualified_name",
    description="Find all fully qualified functions matching a fully qualified name in V8",
    parameters={
        "fully_qualified_name": {
            "type": "string",
            "description": "The fully qualified function name to search for",
            "required": True
        }
    },
    execute_function=_find_functions_by_fully_qualified_name_executor
)


# --- get_call_graph_node ---
def _get_call_graph_node_executor(params: dict) -> str:
    function_name = params.get("function_name", "")

    if not function_name:
        return json.dumps({"error": "function_name parameter is required"})

    node = CALL_GRAPH_MAP.get(function_name)
    return json.dumps(node, indent=2) if node else json.dumps({"error": f"Call graph node not found for {function_name}"})

get_call_graph_node_tool = IkaTools(
    name="get_call_graph_node",
    description="Get the call graph node for a fully qualified function name in V8",
    parameters={
        "function_name": {
            "type": "string",
            "description": "The fully qualified function name",
            "required": True
        }
    },
    execute_function=_get_call_graph_node_executor
)


# =============================================================================
# Export all tools as a dictionary
# =============================================================================

ALL_COMMON_TOOLS = {
    "web_search": web_search_tool,
    "get_cfg_for": get_cfg_for_tool,
    "get_call_graph_hashmap": get_call_graph_hashmap_tool,
    "find_functions_by_simple_name": find_functions_by_simple_name_tool,
    "find_functions_by_fully_qualified_name": find_functions_by_fully_qualified_name_tool,
    "get_call_graph_node": get_call_graph_node_tool,
}
