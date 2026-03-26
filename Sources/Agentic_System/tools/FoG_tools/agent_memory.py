"""
FoG agent memory tools: write_agent_memory, read_agent_memory, list_agent_memory_ids.
"""

import json
from pathlib import Path

_agentic_dir = Path(__file__).resolve().parent.parent.parent
_ikacore_src = _agentic_dir / "IkaCore" / "src"
import sys
if str(_ikacore_src) not in sys.path:
    sys.path.insert(0, str(_ikacore_src))

from IkaCore.tools import IkaTools

from tools.agent_memory_db import load as agent_memory_load, save as agent_memory_save, list_ids as agent_memory_list_ids, entry_path as agent_memory_entry_path


def _write_agent_memory_executor(params: dict) -> str:
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
        "file_line": file_line,
    }
    agent_memory_save(id_val, data)
    return f"OK: wrote {id_val} to {agent_memory_entry_path(id_val)}"


def _read_agent_memory_executor(params: dict) -> str:
    id_val = params.get("id", "")
    if not id_val:
        return "Error: id parameter is required"
    db = agent_memory_load(id_val)
    return json.dumps(db)


def _list_agent_memory_ids_executor(params: dict) -> str:
    return json.dumps(agent_memory_list_ids())


write_agent_memory_tool = IkaTools(
    name="write_agent_memory",
    description="Store a key-value entry in the session-scoped agent memory. Use for code snippets, analysis results, or context that other agents may need. Include clear IDs and concise content.",
    parameters={
        "id": {"type": "string", "description": "The ID of the agent memory entry", "required": True},
        "Body": {"type": "string", "description": "The body of the entry", "required": True},
        "Context": {"type": "array", "items": {"type": "string"}, "description": "List of related IDs", "required": False},
        "Explanation": {"type": "string", "description": "Explanation of relevance", "required": True},
        "FileLine": {"type": "string", "description": "File lines related to the body", "required": True},
    },
    execute_function=_write_agent_memory_executor,
)

read_agent_memory_tool = IkaTools(
    name="read_agent_memory",
    description="Retrieve a stored entry from agent memory by its ID. Use list_agent_memory_ids first to discover available IDs.",
    parameters={"id": {"type": "string", "description": "The ID of the agent memory entry to read", "required": True}},
    execute_function=_read_agent_memory_executor,
)

list_agent_memory_ids_tool = IkaTools(
    name="list_agent_memory_ids",
    description="List all stored entry IDs in the current session. Call before read_agent_memory to see what context other agents have saved.",
    parameters={"input": {"type": "string", "description": "No input required", "required": False}},
    execute_function=_list_agent_memory_ids_executor,
)
