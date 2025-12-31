OUTPUT_DIRECTORY = "/tmp/fog-output-samples"
import warnings
warnings.filterwarnings("ignore", module="pydantic")


# Try to import smolagents, but don't fail if it's not available
try:
    from smolagents import tool
except ImportError:
    # Define a dummy decorator if smolagents is not available
    def tool(func):
        return func
from openai import OpenAI
import subprocess
import os
import json
from pathlib import *
import sys
import re

try:
    from pygdbmi.gdbcontroller import GdbController as PygdbmiController
except Exception:
    PygdbmiController = None

from config_loader import get_openai_api_key, get_anthropic_api_key
client = OpenAI(api_key=get_openai_api_key())


if not os.getenv('V8_PATH'):
    print("V8_PATH environment variable not set. Do export V8_PATH='path to v8 base dir'")
    print("     Example: export V8_PATH=/path/to/v8/v8/src")
    sys.exit(0)
if not os.getenv('D8_PATH'):
    print("D8_PATH is not set")
    sys.exit(1)
if not os.getenv('FUZZILLI_TOOL_BIN'):
    print("FUZZILLI_TOOL_BIN is not set")
    sys.exit(1)
if not os.getenv('FUZZILLI_PATH'):
    print("FUZZILLI_PATH is not set")
    sys.exit(1)
D8_PATH = os.getenv('D8_PATH')
FUZZILLI_TOOL_BIN = os.getenv('FUZZILLI_TOOL_BIN')
V8_PATH = os.getenv('V8_PATH')
FUZZILLI_PATH = os.getenv('FUZZILLI_PATH')
SWIFT_PATH = os.path.join(FUZZILLI_PATH, 'Sources', 'Fuzzilli')
if "src" not in V8_PATH:
    print('V8_PATH is not a valid V8 source code directory')
    sys.exit(0)
if "fuzzillai" not in FUZZILLI_PATH:
    print(f"FUZZILLI_PATH is not a valid Fuzzilli path: {FUZZILLI_PATH}")
    sys.exit(0)


D8_COMMON_FLAGS = "--allow-natives-syntax --experimental-fuzzing --expose-gc"

# Try to import CFG tools, but don't fail if clang is not available
try:
    from tools.cfg_tool import *
    cfg_builder = CFGBuilder(V8_PATH)
    cfg_builder.parse_directory(V8_PATH, pattern='*.cc')
    # Ensure call graph parents are populated once parsing completes.
    try:
        if not hasattr(cfg_builder, "_finalized") or not cfg_builder._finalized:
            cfg_builder.finalize_call_graph()
            cfg_builder._finalized = True
    except Exception as e:
        print(f"[CFG] finalize_call_graph failed: {e}")
except Exception as e:
    # Define fallback functions if CFG tools can't be imported
    def find_function_cfg(function_name: str) -> str:
        return f"CFG analysis not available: {e}"
    
    cfg_builder = None

# Try to import from rag_tools, but handle import errors gracefully
try:
    from rag_tools import *
except ImportError as e:
    # Define fallback functions if rag_tools can't be imported
    def _get_embeddings():
        raise RuntimeError(f"RAG tools not available: {e}")
    
    def _get_active_collection(default="rag-chroma"):
        return default
    
    def _compute_doc_id(metadata, content):
        import hashlib
        key_fields = [
            metadata.get("challenge", ""),
            metadata.get("binary", ""),
            metadata.get("type", ""),
            metadata.get("label", ""),
            metadata.get("address", ""),
            metadata.get("file", ""),
            str(metadata.get("stage", "")),
        ]
        h = hashlib.sha1("|".join(key_fields).encode("utf-8"))
        return h.hexdigest()
    
    Chroma = None



# helper to get output from run_command
def get_output(completed_process) -> str:
    if not completed_process:
        return ""
    p_stdout = completed_process.stdout if completed_process.stdout else None
    p_stderr = completed_process.stderr if completed_process.stderr else None
    return p_stdout if p_stdout else p_stderr

def is_valid_regex(pattern: str) -> tuple[bool, re.error | None]:
    try:
        re.compile(pattern)
        return True, None
    except re.error as e:
        return False, e
        

@tool
def run_command(command: str) -> str:
    """
    Executes a command analysis tools.
    
    Args:
        command (str): The command to execute in the container.
    """
    return_val = subprocess.run(command, shell=True, capture_output=True, text=True)
    print(f"Command: {command}")
    return return_val

@tool
def read_rag_db(query: str) -> str:
    """
    Reads the RAG database and returns the most relevant information.

    Args:
        query (str): Natural language search query to retrieve relevant chunks.
    """
    try:
        # Lazy init if available
        if Chroma is None:
            return "RAG is not configured in this environment."
        try:
            embeddings = _get_embeddings()
        except Exception as ee:
            return f"RAG embedding not available: {ee}"
        RAG_DB_DIR = os.getenv("RAG_DB_DIR", "./rag_db")
        collection_name = _get_active_collection(default="rag-chroma")
        vectorstore = Chroma(
          collection_name=collection_name,
          persist_directory=RAG_DB_DIR,
          embedding_function=embeddings
        )
        retriever = vectorstore.as_retriever(search_kwargs={"k": 4})
        docs = retriever.invoke(query)
        return "\n---\n".join([getattr(d, 'page_content', str(d)) for d in docs])
    except Exception as e:
        return f"Error reading from RAG DB: {e}"


@tool
def write_rag_db(content: str, metadata_json: str = "") -> str:
    """
    Writes a text chunk to the RAG database, with metadata as JSON.

    Copy the following metadata format and fill in the values as appropriate:
    EXAMPLE:
    {"agent":"IDA","challenge":"...","file":"...","line":"..."}

    Args:
        content: Text to index.
        metadata_json: JSON string with metadata regarding the agent that wrote the content 
    """
    try:
        if Chroma is None:
            return "RAG is not configured in this environment."
        try:
            embeddings = _get_embeddings()
        except Exception as ee:
            return f"RAG embedding not available: {ee}"
        RAG_DB_DIR = os.getenv("RAG_DB_DIR", "./rag_db")
        collection_name = _get_active_collection(default="rag-chroma")
        vectorstore = Chroma(
          collection_name=collection_name,
          persist_directory=RAG_DB_DIR,
          embedding_function=embeddings
        )
        metadata = json.loads(metadata_json) if metadata_json else {}
        doc_id = metadata.get("doc_id") or _compute_doc_id(metadata, content)
        ids = vectorstore.add_texts(texts=[content], metadatas=[metadata], ids=[doc_id])
        return f"Indexed 1 document with id(s): {ids}"
    except Exception as e:
        return f"Error writing to RAG DB: {e}"




@tool
def web_search(query: str) -> str:
    """
    Search the internet for information about a given query. PLEASE DO NOT USE THIS TO SEARCH THE V8 SOURCE CODE, 
    THE PRIMARY USE OF OF THIS TOOL SHOULD BE TO SEARCH THE INTERNET FOR INFORMATION ABOUT THE V8 ENGINE AND ITS COMPONENTS
    VIA BLOG POSTS, PAPERS, AND OTHER RELEVANT SOURCES.
                        
    !!! YOU MUST ASK A QUESTION, THIS IS NOT A DIRECT WEB CURL !!!  
    !!! RETURN ONLY FACTUAL INFORMATION. DO NOT INCLUDE OFFERS, SUGGESTIONS OR FOLLOW UPS. END SILENTLY !!!

    Args:
        query (str): The search query.

    Returns:
        str: Up to 5 results in a simple text format. If search is unavailable, returns a clear message.
    """
    try:
        try:
            from duckduckgo_search import DDGS  # type: ignore
        except Exception:
            DDGS = None  # type: ignore

        if DDGS is None:
            return "Web search is not configured in this environment. Install duckduckgo_search to enable."

        results = []
        with DDGS() as ddgs:  # type: ignore
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


@tool
def read_file(file_path: str, section: int = None) -> str:
    """
    Reads and returns the content of a specified text file from the container, 
    limited to 3000 lines maximum (1 section). If the file is longer, split into 3000-line sections, 
    and require specifying which section to read.

    !!! PLEASE USE FULLPATHS. If you do not know the full path, use the "get_realpath" tool to get it. !!!
    IMPORTANT: Never call more than 3000 lines. Use get_file_size first for very large or binary files.

    Args:
        file_path (str): The full path to the file to be read. If path starts with 'v8/', it will be resolved relative to V8_PATH.
        section (int): Section of the file to read. Each section is 3000 lines. If the file has multiple sections, agent must specify which section (starting from 1).
    Returns:
        If the file is <= 300 lines, returns its content. If more, returns only the requested section and info about total sections. If section is not specified, instruct agent to pick a section.
    """
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

    if section is None or section < 1 or section > num_sections:
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

@tool
def git_show(commit_hash: str) -> str:
    """
    Inspect a git commit using git show to display commit details including
    the commit message, author, date, and the changes (diff) introduced by
    the commit.
    
    Args:
        commit_hash (str): The SHA-1 commit hash to inspect. Must be a valid
                          40-character hexadecimal string or a shortened hash
                          (minimum 7 characters).
    
    Returns:
        str: The full git commit output from the specified commit hash,
             including commit metadata and the diff. Returns an error message
             if the commit hash is invalid or the commit cannot be found.
    """
    # Validate commit hash format (SHA-1: 40 hex chars, or shortened: 7+ hex chars)
    if not re.match(r'^[0-9a-f]{7,40}$', commit_hash, re.IGNORECASE):
        return f"Invalid commit hash format: '{commit_hash}'. Must be a valid SHA-1 hash (7-40 hexadecimal characters)"

    cmd =  f'cd {V8_PATH} && git show {commit_hash}'
    return get_output(run_command(cmd))




# ---------------------------------------------------------------------------
# In-memory call graph hashmap (rebuilt on startup)
# ---------------------------------------------------------------------------
# Desired structure:
# call_graph = {
#     "v8::internal::func1": {
#         "entry_nodes": ["F1", "F2", "F3"],
#         "exit_nodes": ["F4", "F5", "F6"],
#         "file_path": "v8/src/heap/heap.cc",
#         "line_number": 100
#     },
#     ...
# }
# We map entry/exit to the CFG entry/exit node IDs (as strings) when available.
# ---------------------------------------------------------------------------


def _build_call_graph_hashmap():
    """
    Build the call graph hashmap from the cfg_builder's call graph
    representing the call graph of the V8 engine.
    
    Args:
        None
    Returns:
        dict: The call graph hashmap.
    """
    cg = {}
    if cfg_builder is None:
        return cg
        
    for full_name, info in cfg_builder.call_graph.items():
        cfg = cfg_builder.cfgs.get(full_name)
        entry_id = str(info.get("entry")) if info.get("entry") is not None else None
        exit_id = str(info.get("exit")) if info.get("exit") is not None else None
        file_path = None
        line_number = None
        if info.get("location"):
            file_path = info["location"].get("file")
            line_number = info["location"].get("line")

        cg[full_name] = {
            "entry_nodes": [entry_id] if entry_id else [],
            "exit_nodes": [exit_id] if exit_id else [],
            "file_path": file_path,
            "line_number": line_number,
        }
    return cg

CALL_GRAPH_HASHMAP = _build_call_graph_hashmap()


@tool
def get_call_graph_hashmap() -> str:
    """
    Return the in-memory call graph hashmap with entry/exit nodes and locations.

    Args:
        None
    Returns:
        str: The call graph hashmap in JSON format.
    """
    try:
        return json.dumps(CALL_GRAPH_HASHMAP, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Failed to serialize call graph hashmap: {e}"})

DEFAULT_CFG_JSON = Path(__file__).resolve().parent / "cfg" / "v8_cfg_output.json"
CFG_JSON_PATH = Path(os.getenv("V8_CFG_JSON_PATH", default=DEFAULT_CFG_JSON))


def _load_cfg_json():
    """
    Load precomputed CFGs from JSON representing the CFGs of the V8 engine.

    Args:
        None
    Returns:
        dict: The CFG hashmap in JSON format.
    """
    if not CFG_JSON_PATH.exists():
        print(f"[CFG] CFG JSON not found at {CFG_JSON_PATH}; CFG cache not loaded")
        return {}

    try:
        with open(CFG_JSON_PATH, "r") as f:
            data = json.load(f)
        cfg_map = data.get("cfgs", {})
        print(f"[CFG] Loaded {len(cfg_map)} CFGs from {CFG_JSON_PATH}")
        return cfg_map
    except Exception as e:
        print(f"[CFG] Error loading CFG JSON {CFG_JSON_PATH}: {e}")
        return {}


# Global, in-memory maps for fast access during this process' lifetime.
# CFG_HASHMAP = cfg_builder.cfgs if cfg_builder else {}
CFG_JSON_MAP = _load_cfg_json()

# Preferred lookup: hashmaps if present, else in-memory json.
CFG_MAP = CFG_JSON_MAP     # CFG_HASHMAP if CFG_HASHMAP else CFG_JSON_MAP
CALL_GRAPH_MAP = CALL_GRAPH_HASHMAP

@tool
def get_cfg_for(function_name: str) -> dict:
    """
    Get the Control Flow Graph (CFG) for a fully qualified function name of the V8 engine.

    Args:
        function_name (str): The name of the function to get the CFG for.
    Returns:
        dict: The CFG for the function in JSON format.
    """
    return json.dumps(CFG_MAP.get(function_name), indent=2)


@tool
def find_functions_by_simple_name(simple_name: str) -> list[str]:
    """
    Find all fully qualified functions matching a simple function name.
    Useful when you only know the spelling and want to map to the call graph keys of the V8 engine.

    Args:
        simple_name (str): The name of the function to find.
    Returns:
        list: A list of fully qualified function names that match the simple name.
    """
    matches = []
    for full_name, info in CALL_GRAPH_MAP.items():
        if info.get("function_name") == simple_name or simple_name in full_name:
            matches.append(full_name)
    return matches

@tool
def find_functions_by_fully_qualified_name(fully_qualified_name: str) -> list[str]:
    """
    Find all fully qualified functions matching a fully qualified name of the V8 engine.

    Args:
        fully_qualified_name (str): The fully qualified name of the function to find.
    Returns:
        list: A list of fully qualified function names that match the fully qualified name.
    """
    matches = []
    for full_name, info in CALL_GRAPH_MAP.items():
        if full_name == fully_qualified_name:
            matches.append(full_name)
    return matches

@tool
def get_call_graph_node(function_name: str) -> dict:
    """
    Get the call graph node for a fully qualified function name of the V8 engine.

    Args:
        function_name (str): The fully qualified name of the function to get the call graph node for.
    Returns:
        dict: The call graph node for the function.
    """
    return CALL_GRAPH_MAP.get(function_name)

#########################################################################################################
#
# GDB and pwndbg helpers for D8 with JS inputs (session-aware)
#

def _check_v8_binary() -> str | None:
    if not D8_PATH:
        return "Error: D8_PATH is not set"
    if not os.path.exists(D8_PATH):
        return f"Error: D8 binary not found at '{D8_PATH}'"
    if not os.access(D8_PATH, os.X_OK):
        return f"Error: D8 binary at '{D8_PATH}' is not executable"
    return None


def _check_js_path(js_path: str) -> str | None:
    if not js_path:
        return "Error: js_path is required"
    if not os.path.isabs(js_path):
        return f"Error: js_path must be absolute, got '{js_path}'"
    if not os.path.exists(js_path):
        return f"Error: JS file '{js_path}' not found"
    return None


def _format_args(js_path: str | None, d8_args: str | None) -> str:
    user_args = d8_args.strip() if d8_args else ""
    if js_path:
        base = f"{D8_COMMON_FLAGS} {user_args} {js_path}".strip()
    else:
        base = f"{D8_COMMON_FLAGS} {user_args}".strip()
    return base


# Cached session defaults for JS + args - must be set via start_mi_debug_session
DEBUG_SESSION: dict = {"js_path": "", "d8_args": ""}
MI_CONTROLLER = None


def _require_debug_session() -> tuple[str, str] | str:
    """Check that debug session is active and return js_path and d8_args, or error string."""
    js_path = DEBUG_SESSION.get("js_path", "")
    if not js_path:
        return "Error: No active debug session. Call start_debug_session first."
    d8_args = DEBUG_SESSION.get("d8_args", "")
    return js_path, d8_args


def _require_mi_available() -> str | None:
    if PygdbmiController is None:
        return "Error: pygdbmi is not installed; install pygdbmi to use MI session."
    return None


def _require_mi_controller():
    if MI_CONTROLLER is None:
        return "Error: No active MI session. Call start_mi_debug_session first."
    return None


def _format_mi_responses(responses) -> str:
    try:
        return json.dumps(responses, indent=2)
    except Exception:
        return str(responses)


@tool
def start_mi_debug_session(js_path: str, d8_args: str = "") -> str:
    """
    Start a persistent GDB/MI session (like pwno) against D8 with the given JS file.

    Args:
        js_path (str): Absolute path to a JS file to run with d8.
        d8_args (str): Extra args to pass to d8 before the JS path (optional).

    Returns:
        str: Status message.
    """
    global MI_CONTROLLER
    avail_err = _require_mi_available()
    if avail_err:
        return avail_err
    err = _check_v8_binary()
    if err:
        return err
    js_err = _check_js_path(js_path)
    if js_err:
        return js_err

    # Update session defaults
    DEBUG_SESSION["js_path"] = js_path
    DEBUG_SESSION["d8_args"] = d8_args or ""

    # Close any existing controller
    if MI_CONTROLLER is not None:
        try:
            MI_CONTROLLER.exit()
        except Exception:
            pass
        MI_CONTROLLER = None

    try:
        MI_CONTROLLER = PygdbmiController(command=["gdb", "--interpreter=mi4", "--quiet"])
        init_cmds = [
            "-gdb-set pagination off",
            "-gdb-set confirm off",
            "-gdb-set mi-async on",
            f"-file-exec-and-symbols {D8_PATH}",
        ]
        args = _format_args(js_path, d8_args)
        init_cmds.append(f"set args {args}")
        results = []
        for cmd in init_cmds:
            res = MI_CONTROLLER.write(cmd, timeout_sec=7.0)
            results.append({"cmd": cmd, "resp": res})
        return "MI debug session started.\n" + _format_mi_responses(results)
    except Exception as e:
        MI_CONTROLLER = None
        return f"Error starting MI session: {e}"


@tool
def stop_mi_debug_session() -> str:
    """
    Stop the persistent GDB/MI session.

    Returns:
        str: Status message.
    """
    global MI_CONTROLLER
    if MI_CONTROLLER is not None:
        try:
            MI_CONTROLLER.exit()
        except Exception as e:
            MI_CONTROLLER = None
            return f"Stopped MI session with warning: {e}"
        MI_CONTROLLER = None
        return "MI debug session stopped."
    return "No active MI session to stop."


@tool
def mi_exec(command: str) -> str:
    """
    Execute a raw GDB/MI command in the active session.

    Args:
        command (str): MI or classic GDB command to run (for example, \"-exec-continue\", \"info registers\").

    Returns:
        str: Command responses.
    """
    avail_err = _require_mi_available()
    if avail_err:
        return avail_err
    ctrl_err = _require_mi_controller()
    if ctrl_err:
        return ctrl_err
    if not command:
        return "Error: command is required"
    try:
        resp = MI_CONTROLLER.write(command, timeout_sec=7.0)
        return _format_mi_responses(resp)
    except Exception as e:
        return f"Error executing MI command: {e}"


@tool
def mi_run() -> str:
    """
    Run the loaded D8 + JS in the active MI session.

    Returns:
        str: The output of the command.
    """
    return mi_exec("-exec-run")


@tool
def mi_continue() -> str:
    """
    Continue execution in the active MI session.

    Returns:
        str: The output of the command.
    """
    return mi_exec("-exec-continue")


@tool
def mi_next() -> str:
    """
    Step over (next) in the active MI session.

    Returns:
        str: The output of the command.
    """
    return mi_exec("-exec-next")


@tool
def mi_step() -> str:
    """
    Step into in the active MI session.

    Returns:
        str: The output of the command.
    """
    return mi_exec("-exec-step")


@tool
def gdb_run_command(command: str) -> str:
    """
    Run a gdb or pwndbg command in the active MI session.

    Args:
        command (str): GDB or pwndbg command to execute (for example, "info registers", "context", "vmmap").

    Returns:
        str: The output of the command.
    """
    avail_err = _require_mi_available()
    if avail_err:
        return avail_err
    ctrl_err = _require_mi_controller()
    if ctrl_err:
        return ctrl_err
    if not command:
        return "Error: command is required"
    try:
        resp = MI_CONTROLLER.write(command, timeout_sec=7.0)
        return _format_mi_responses(resp)
    except Exception as e:
        return f"Error executing command: {e}"


@tool
def gdb_set_breakpoint(source_file: str, line: int) -> str:
    """
    Set a source-level breakpoint and list breakpoints using the active MI session.

    Args:
        source_file (str): Source file path (relative or absolute) to break on.
        line (int): Line number for the breakpoint.

    Returns:
        str: The output of the command.
    """
    if not source_file:
        return "Error: source_file is required"
    if line <= 0:
        return "Error: line must be positive"
    resp = gdb_run_command(f"break {source_file}:{line}")
    if resp.startswith("Error"):
        return resp
    return gdb_run_command("info breakpoints")


@tool
def gdb_print_value(expression: str) -> str:
    """
    Print an expression in the active MI session (runs program if needed).

    Args:
        expression (str): GDB expression to print (for example, "print some_var").

    Returns:
        str: The output of the command.
    """
    if not expression:
        return "Error: expression is required"
    # ensure program is run first
    run_resp = mi_run()
    if "Error" in run_resp:
        return run_resp
    return gdb_run_command(f"print {expression}")


@tool
def pwndbg_context() -> str:
    """
    Show pwndbg context (registers, code, stack, backtrace) in the active MI session.

    Returns:
        str: The output of the command.
    """
    run_resp = mi_run()
    if "Error" in run_resp:
        return run_resp
    return gdb_run_command("context")


@tool
def pwndbg_vmmap() -> str:
    """
    Display virtual memory mappings via pwndbg in the active MI session.

    Returns:
        str: The output of the command.
    """
    run_resp = mi_run()
    if "Error" in run_resp:
        return run_resp
    return gdb_run_command("vmmap")


@tool
def pwndbg_regs() -> str:
    """
    Show registers with pwndbg regs after running in the active MI session.

    Returns:
        str: The output of the command.
    """
    run_resp = mi_run()
    if "Error" in run_resp:
        return run_resp
    return gdb_run_command("regs")


@tool
def pwndbg_nearpc(count: int = 10) -> str:
    """
    Disassemble near the current PC after running in the active MI session.

    Args:
        count (int): Number of instructions to show near PC.

    Returns:
        str: The output of the command.
    """
    if count <= 0:
        return "Error: count must be positive"
    run_resp = mi_run()
    if "Error" in run_resp:
        return run_resp
    return gdb_run_command(f"nearpc {count}")

