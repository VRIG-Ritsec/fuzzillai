"""
FoG FuzzIL tools: lift_fuzzil_to_js, compile_js_to_fuzzil.
"""

from pathlib import Path

_agentic_dir = Path(__file__).resolve().parent.parent.parent
_ikacore_src = _agentic_dir / "IkaCore" / "src"
import sys
if str(_ikacore_src) not in sys.path:
    sys.path.insert(0, str(_ikacore_src))

from IkaCore.tools import IkaTools

from ._shared import FUZZILLI_TOOL_BIN, run_command, get_output


def _lift_fuzzil_to_js_executor(params: dict) -> str:
    target = params.get("target", "")
    if not target:
        return "Error: target parameter is required"
    return get_output(run_command(f"{FUZZILLI_TOOL_BIN} --liftToJS {target}"))


def _compile_js_to_fuzzil_executor(params: dict) -> str:
    target = params.get("target", "")
    if not target:
        return "Error: target parameter is required"
    return get_output(run_command(f"{FUZZILLI_TOOL_BIN} --compile {target}"))


lift_fuzzil_to_js_tool = IkaTools(
    name="lift_fuzzil_to_js",
    description="Convert a base64-encoded FuzzIL protobuf to executable JavaScript using FuzzILTool. Use when you have a FuzzIL program from the database or templates.",
    parameters={"target": {"type": "string", "description": "Path to the FuzzIL program (.fzil)", "required": True}},
    execute_function=_lift_fuzzil_to_js_executor,
)

compile_js_to_fuzzil_tool = IkaTools(
    name="compile_js_to_fuzzil",
    description="Compile a JavaScript program into FuzzIL form using FuzzILTool. Requires Node.js. Use when reverse-engineering JS into template-compatible FuzzIL.",
    parameters={"target": {"type": "string", "description": "Path to the JavaScript program", "required": True}},
    execute_function=_compile_js_to_fuzzil_executor,
)
