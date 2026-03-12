#!/usr/bin/env python3
'''
EBG Crash
L0 Manager Agent - Crash analysis and variant generation
'''

from pathlib import Path
import sys

# Ensure the Agentic_System package root is on sys.path
_agentic_root = Path(__file__).resolve().parents[1]
if str(_agentic_root) not in sys.path:
    sys.path.insert(0, str(_agentic_root))
_ikacore_src = _agentic_root / "IkaCore" / "src"
if _ikacore_src.exists() and str(_ikacore_src) not in sys.path:
    sys.path.insert(0, str(_ikacore_src))

from agents.BaseAgent import Agent
from IkaCore.agents import IkaBaseAgent
from tools.EBG_tools_ika import (
    base64_program_to_js_tool,
    db_query_tool,
    db_list_programs_tool,
    db_get_fuzzer_performance_summary_tool,
    db_list_fuzzers_tool,
    db_get_crash_diversity_tool,
    db_get_mutator_effectiveness_tool,
    db_get_program_grouping_tool,
    db_get_execution_outcome_distribution_tool,
    read_from_generate_folder_tool,
    write_to_generate_folder_tool,
    delete_files_from_generate_folder_tool,
    list_generate_folder_tool,
    create_generate_folder_tool,
    trace_v8_analysis_tool,
    list_v8_trace_options_tool,
    get_program_js_from_hash_tool,
    start_mi_debug_session_tool,
    stop_mi_debug_session_tool,
    mi_exec_tool,
    mi_run_tool,
    mi_step_tool,
    mi_next_tool,
    mi_continue_tool,
    gdb_run_command_tool,
    gdb_set_breakpoint_tool,
    gdb_print_value_tool,
    pwndbg_context_tool,
    pwndbg_vmmap_tool,
    pwndbg_regs_tool,
    pwndbg_nearpc_tool,
    read_file_tool,
)
from tools.FoG_tools_ika import (
    fuzzy_finder_tool,
    ripgrep_tool,
    tree_tool,
    get_realpath_tool,
    execute_javascript_program_tool,
    list_d8_flags_tool,
)
from tools.common_tools_ika import (
    get_cfg_for_tool,
    get_call_graph_hashmap_tool,
    find_functions_by_simple_name_tool,
    find_functions_by_fully_qualified_name_tool,
    get_call_graph_node_tool,
)
from tools.FoG_tools_ika import read_rag_db_id_tool, write_rag_db_id_tool, get_runtime_db_ids_tool
from config_loader import get_openai_api_key, get_anthropic_api_key, get_deepseek_api_key, get_openrouter_api_key
from tools.FoG_tools import get_v8_path

import os
from typing import Optional
import argparse
import logging
import pytz

# Default to OpenAI, keep DeepSeek/OpenRouter available via env overrides.
MANAGER_MODEL = os.environ.get("EBG_MANAGER_MODEL", "gpt-4o")
WORKER_MODEL = os.environ.get("EBG_WORKER_MODEL", "gpt-4o-mini")
API_URL = os.environ.get("EBG_API_URL", "https://api.openai.com/v1/chat/completions")


logger = logging.getLogger("boiled_eggs")
if not logger.handlers:
    logger.addHandler(logging.NullHandler())
logger.propagate = False
logger.disabled = True
est_timezone = pytz.timezone('US/Eastern')

sys.path.append(str(Path(__file__).parent.parent))

if os.environ.get("FUZZILLI_PATH"):
    FUZZILLI_PATH = os.environ.get("FUZZILLI_PATH")

class EBG_Crash(Agent):

    def __init__(
        self,
        model=None,
        api_key: str = None,
        anthropic_api_key: str = None,
        crash_program_hash: Optional[str] = "Manual File System Scanning",
    ):
        if crash_program_hash is None:
            print("crash_program_hash is not provided for EBG_Crash\n Using file system scanning instead...")
            crash_program_hash = "Manual File System Scanning"
        self.crash_program_hash = crash_program_hash
        super().__init__(model, api_key, anthropic_api_key)

    def setup_agents(self, crash_program_hash: Optional[str] = None):
        if crash_program_hash is None:
            crash_program_hash = getattr(self, 'crash_program_hash', None)
        if crash_program_hash is None:
            crash_program_hash = "Manual File System Scanning"

        root_manager_prompt = self.get_prompt("variant_manager.txt")
        root_manager_prompt = root_manager_prompt.replace("[ENTER SELECTED CRASH NAME]", crash_program_hash)

        v8_sys = self.get_prompt("v8_search.txt") + "THIS IS THE CURRENT V8 PATH ASSUMING YOU ARE INSIDE THE V8 SOURCE CODE DIRECTORY FOR ALL TOOL CALLS ALREADY: " + get_v8_path()

        self.agents['v8_search'] = IkaBaseAgent(
            name="V8Search",
            description="L2 Worker responsible for searching V8 source code using fuzzy find, regex, and compilation tools",
            prompt=v8_sys,
            system_prompt="You are V8Search.",
            tools=[
                fuzzy_finder_tool,
                ripgrep_tool,
                tree_tool,
                read_rag_db_id_tool,
                write_rag_db_id_tool,
                read_file_tool,
                get_realpath_tool,
                get_runtime_db_ids_tool,
                get_cfg_for_tool,
                get_call_graph_hashmap_tool,
                find_functions_by_simple_name_tool,
                find_functions_by_fully_qualified_name_tool,
                get_call_graph_node_tool,
            ],
            model_id=WORKER_MODEL,
            api_key=self.api_key,
            api_url=API_URL,
            maxsteps=50,
            step_timeout=300,
            logging_level=self.logging_level,
        )

        db_prompt = self.get_prompt("db_analyzer.txt")
        with open(FUZZILLI_PATH + "/Sources/Agentic_System/postgres-init.sql", "r") as f:
            db_prompt = db_prompt + "\n Here is the latest programs from the database: " + f.read()

        self.agents['db_analyzer'] = IkaBaseAgent(
            name="DBAnalyzer",
            description="L2 Worker responsible for analyzing PostgreSQL database for corpus, flags, coverage, and execution state",
            prompt=db_prompt,
            system_prompt="You are DBAnalyzer.",
            tools=[
                base64_program_to_js_tool,
                db_query_tool,
                db_list_programs_tool,
                db_get_fuzzer_performance_summary_tool,
                db_list_fuzzers_tool,
                db_get_crash_diversity_tool,
                db_get_mutator_effectiveness_tool,
                db_get_program_grouping_tool,
                db_get_execution_outcome_distribution_tool,
                read_from_generate_folder_tool,
                write_to_generate_folder_tool,
                delete_files_from_generate_folder_tool,
                list_generate_folder_tool,
                create_generate_folder_tool,
            ],
            model_id=WORKER_MODEL,
            api_key=self.api_key,
            maxsteps=30,
            step_timeout=300,
            api_url=API_URL,
            logging_level=self.logging_level,
        )

        self.agents['debugger'] = IkaBaseAgent(
            name="Debugger",
            description="L2 Worker responsible for debugging a crash",
            prompt=self.get_prompt("debugger.txt"),
            system_prompt="You are Debugger.",
            tools=[
                execute_javascript_program_tool,
                list_d8_flags_tool,
                list_v8_trace_options_tool,
                trace_v8_analysis_tool,
                get_program_js_from_hash_tool,
                read_from_generate_folder_tool,
                list_generate_folder_tool,
                start_mi_debug_session_tool,
                stop_mi_debug_session_tool,
                mi_exec_tool,
                mi_run_tool,
                mi_step_tool,
                mi_next_tool,
                mi_continue_tool,
                gdb_run_command_tool,
                gdb_set_breakpoint_tool,
                gdb_print_value_tool,
                pwndbg_context_tool,
                pwndbg_vmmap_tool,
                pwndbg_regs_tool,
                pwndbg_nearpc_tool,
            ],
            model_id=WORKER_MODEL,
            api_key=self.api_key,
            maxsteps=30,
            step_timeout=180,
            api_url=API_URL,
            logging_level=self.logging_level,
        )

        self.agents['JS_Generator'] = IkaBaseAgent(
            name="JSGenerator",
            description="L2 Worker responsible for generating JavaScript program seeds from a crash PoC",
            prompt=self.get_prompt("JS_generator.txt"),
            system_prompt="You are JSGenerator.",
            tools=[],
            model_id=WORKER_MODEL,
            api_key=self.api_key,
            maxsteps=30,
            step_timeout=180,
            api_url=API_URL,
            logging_level=self.logging_level,
        )

        self.agents['runtime_analyzer'] = IkaBaseAgent(
            name="RuntimeAnalyzer",
            description="L1 Manager responsible for analyzing program runtime, coverage, and execution state",
            prompt=self.get_prompt("runtime_analyzer.txt"),
            system_prompt="You are RuntimeAnalyzer.",
            tools=[
                execute_javascript_program_tool,
                list_d8_flags_tool,
                list_v8_trace_options_tool,
                trace_v8_analysis_tool,
                read_from_generate_folder_tool,
                list_generate_folder_tool,
            ],
            model_id=MANAGER_MODEL,
            api_key=self.api_key,
            subagents=[self.agents['v8_search'], self.agents['db_analyzer'], self.agents['debugger']],
            maxsteps=30,
            step_timeout=420,
            api_url=API_URL,
            logging_level=self.logging_level,
        )

        self.agents['variant_analysis'] = IkaBaseAgent(
            name="VariantAnalysis",
            description="L1 Manager responsible for performing variant analysis on crashes",
            prompt=self.get_prompt("variant_analysis.txt"),
            system_prompt="You are VariantAnalysis.",
            tools=[
                execute_javascript_program_tool,
                list_d8_flags_tool,
                list_v8_trace_options_tool,
                trace_v8_analysis_tool,
                read_from_generate_folder_tool,
                list_generate_folder_tool,
            ],
            model_id=MANAGER_MODEL,
            api_key=self.api_key,
            subagents=[self.agents['v8_search'], self.agents['debugger'], self.agents['JS_Generator']],
            maxsteps=30,
            step_timeout=300,
            api_url=API_URL,
            logging_level=self.logging_level,
        )

        root_managed = [self.agents['runtime_analyzer'], self.agents['variant_analysis']]

        self.agents['root_manager'] = IkaBaseAgent(
            name="RootManager",
            description="L0 Root Manager",
            prompt=root_manager_prompt,
            system_prompt="You are RootManager.",
            tools=[],
            model_id=MANAGER_MODEL,
            api_key=self.api_key,
            subagents=root_managed,
            maxsteps=30,
            step_timeout=600,
            api_url=API_URL,
            logging_level=self.logging_level,
        )

    def get_prompt(self, prompt_name: str) -> str:
        with open(Path(__file__).parent.parent / "prompts" / "EBG-crash-prompts" / prompt_name, 'r') as f:
            return f.read()

    def start_system(self):
        result = self.run_task(
            task_description="Initialize EBG Crash orchestration for crash variant analysis",
            context={
                "RuntimeAnalyzer": "Analyze program runtime, coverage, and execution state",
                "VariantAnalysis": "Perform variant analysis on crashes",
                "DBAnalyzer": "Analyze PostgreSQL database for execution information"
            }
        )
        print("EBG Crash start result:")
        print(f"Completed: {result['completed']}")
        if result['output']:
            print(f"Output: {result['output']}")
        if result['error']:
            print(f"Error: {result['error']}")
        return result


def main():
    parser = argparse.ArgumentParser(description="Run EBG Crash system for a specific crash program hash")
    parser.add_argument("--crash_program_hash", required=False, help="Program hash for the crashing corpus entry")
    args = parser.parse_args()
    args.debug = True

    if args.debug:
        # Logs live under Agentic_System/agents/ebg_logs even though this file moved into start_scripts
        agentic_root = Path(__file__).resolve().parents[1]
        log_dir = agentic_root / 'agents' / 'ebg_logs'
        log_dir.mkdir(parents=True, exist_ok=True)
        latest_num = 0
        if os.path.exists(log_dir / 'EBG_crash.log'):
            for root, dirs, files in os.walk(log_dir, topdown=False):
                for name in files:
                    if not name.endswith('.log'):
                        continue
                    if name == 'EBG_crash.log':
                        continue
                    if not name.startswith('EBG_crash'):
                        continue

                    suffix = name[len('EBG_crash'):-len('.log')]
                    if suffix.isdigit():
                        num = int(suffix)
                        if num > latest_num:
                            latest_num = num
            log_path = str(log_dir / f'EBG_crash{latest_num + 1}.log')
        else:
            log_path = str(log_dir / f'EBG_crash.log')

        if os.path.exists(log_path):
            print(f"Log file already exists: {log_path}")

        # Configure logger to write messages as-is (no prefixes) for 1:1 capture
        logger.handlers.clear()
        file_handler = logging.FileHandler(log_path, mode='a', encoding='utf-8')
        file_handler.setFormatter(logging.Formatter('%(message)s'))
        logger.addHandler(file_handler)
        logger.setLevel(logging.INFO)
        logger.disabled = False

        class _StreamToLogger:
            def __init__(self, log_fn):
                self.log_fn = log_fn
                self._buffer = ''

            def write(self, message):
                if not isinstance(message, str):
                    message = message.decode('utf-8', errors='ignore')
                self._buffer += message
                while '\n' in self._buffer:
                    line, self._buffer = self._buffer.split('\n', 1)
                    self.log_fn(line)

            def flush(self):
                if self._buffer:
                    self.log_fn(self._buffer)
                    self._buffer = ''

            def isatty(self):
                return False

        sys.stdout = _StreamToLogger(logger.info)
        sys.stderr = _StreamToLogger(logger.error)

        # Signal BaseAgent to enable its own logging lazily and ensure directory exists
        os.environ["EBG_DEBUG"] = "1"


    openai_key = get_openai_api_key()
    anthropic_key = get_anthropic_api_key()
    deepseek_key = get_deepseek_api_key()
    openrouter_key = get_openrouter_api_key()

    if openai_key:
        os.environ["OPENAI_API_KEY"] = openai_key
    if deepseek_key:
        os.environ["DEEPSEEK_API_KEY"] = deepseek_key
    if openrouter_key:
        os.environ["OPENROUTER_API_KEY"] = openrouter_key
        os.environ["OPENROUTER_API_URL"] = "https://openrouter.ai/api/v1/chat/completions"

    system = EBG_Crash(
        model=None,
        api_key=openai_key or deepseek_key or openrouter_key,
        anthropic_api_key=anthropic_key,
        crash_program_hash=args.crash_program_hash,
    )

    result = system.run_task(
        task_description="Perform variant analysis on crash",
        context={
            "VariantAnalysis": "Perform variant analysis on crashes",
            "RuntimeAnalyzer": "Analyze program execution and coverage",
            "DBAnalyzer": "Analyze database for execution information"
        }
    )

    print("Task Result:")
    print(f"Completed: {result['completed']}")
    print(f"Output: {result['output']}")
    if result['error']:
        print(f"Error: {result['error']}")


if __name__ == "__main__":
    main()
