#!/usr/bin/env python3
'''
EBG Plateau
L0 Manager Agent - Plateau analysis
'''

from agents.BaseAgent import Agent
from IkaCore.agents import IkaBaseAgent
from pathlib import Path
from tools.EBG_tools import (
    base64_program_to_js_tool,
    db_get_crash_program_as_js_tool,
    db_query_tool,
    db_list_programs_tool,
    db_resolve_fuzzer_id_tool,
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
    write_and_execute_js_tool,
    db_store_generated_program_tool,
    read_file_tool,
)
from tools.RAG_tools import search_v8_source_rag_tool, search_v8_source_rag_hybrid_tool, get_v8_source_rag_doc_tool
from config_loader import get_openai_api_key, get_anthropic_api_key, get_deepseek_api_key
from tools.FoG_tools import (
    fuzzy_finder_tool,
    ripgrep_tool,
    tree_tool,
    get_realpath_tool,
    execute_javascript_program_tool,
    list_d8_flags_tool,
    read_agent_memory_tool,
    write_agent_memory_tool,
    list_agent_memory_ids_tool,
)
from tools._shared import (
    get_cfg_for_tool,
    get_call_graph_hashmap_tool,
    find_functions_by_simple_name_tool,
    find_functions_by_fully_qualified_name_tool,
    get_call_graph_node_tool,
)
from tools.FoG_tools import get_v8_path

import sys
import os
from typing import Optional
import logging
from agent_logging import configure_process_logging

MANAGER_MODEL = os.environ.get("EBG_MANAGER_MODEL", "gpt-5.4")
WORKER_MODEL = os.environ.get("EBG_WORKER_MODEL", "gpt-5-mini")
TOKENS = 30000 # 10k max output in an given message

FUZZILLI_PATH = os.environ.get("FUZZILLI_PATH", str(Path(__file__).resolve().parents[3]))
_AGENTIC_ROOT = Path(__file__).resolve().parents[1]

sys.path.append(str(Path(__file__).parent.parent))
logger = logging.getLogger("ebg_plateau")
if not logger.handlers:
    logger.addHandler(logging.NullHandler())
logger.propagate = False
logger.disabled = True

class EBG_Plateau(Agent):

    def __init__(self, model=None, api_key: str = None, anthropic_api_key: str = None, fuzzer_id: Optional[str] = None):
        if fuzzer_id is None:
            raise ValueError("fuzzer_id must be provided for EBG_Plateau")
        self.fuzzer_id = fuzzer_id
        super().__init__(model, api_key, anthropic_api_key)

    def setup_agents(self, fuzzer_id: Optional[str] = None):
        if fuzzer_id is None:
            fuzzer_id = getattr(self, 'fuzzer_id', None)
        if fuzzer_id is None:
            return
        checkpoint_kwargs = self.get_checkpoint_kwargs("ebg_plateau")

        root_manager_prompt = self.get_prompt("plateau_manager.txt")
        root_manager_prompt = root_manager_prompt.replace("[ENTER THE PLATEAUED FUZZER]", fuzzer_id)

        v8_sys = self.get_prompt("v8_search.txt") + "THIS IS THE CURRENT V8 PATH ASSUMING YOU ARE INSIDE THE V8 SOURCE CODE DIRECTORY FOR ALL TOOL CALLS ALREADY: " + get_v8_path()

        worker_model = self.model_id if "/" in self.model_id else WORKER_MODEL
        manager_model = self.model_id if "/" in self.model_id else MANAGER_MODEL

        self.agents['v8_search'] = IkaBaseAgent(
            name="V8Search",
            description="L2 Worker responsible for searching V8 source code using fuzzy find, regex, and compilation tools",
            prompt=v8_sys,
            system_prompt="You are V8Search.",
            tools=[
                fuzzy_finder_tool,
                ripgrep_tool,
                tree_tool,
                read_agent_memory_tool,
                write_agent_memory_tool,
                read_file_tool,
                get_realpath_tool,
                list_agent_memory_ids_tool,
                get_cfg_for_tool,
                get_call_graph_hashmap_tool,
                find_functions_by_simple_name_tool,
                find_functions_by_fully_qualified_name_tool,
                get_call_graph_node_tool,
            ],
            model_id=worker_model,
            api_key=self.api_key,
            maxsteps=50,
            max_tokens=TOKENS,
            logging_level=self.logging_level,
            **checkpoint_kwargs,
        )

        self.agents['validator'] = IkaBaseAgent(
            name="Validator",
            description="L2 Worker responsible for validating generated program seeds actually hit target V8 code paths through execution tracing and debugging",
            prompt=self.get_prompt("validator.txt"),
            system_prompt="You are Validator.",
            tools=[
                execute_javascript_program_tool,
                list_d8_flags_tool,
                list_v8_trace_options_tool,
                trace_v8_analysis_tool,
                write_to_generate_folder_tool,
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
                read_file_tool,
                get_realpath_tool,
                fuzzy_finder_tool,
                ripgrep_tool,
                tree_tool,
                search_v8_source_rag_tool,
                search_v8_source_rag_hybrid_tool,
                get_v8_source_rag_doc_tool,
            ],
            model_id=worker_model,
            api_key=self.api_key,
            maxsteps=30,
            max_tokens=TOKENS,
            logging_level=self.logging_level,
            **checkpoint_kwargs,
        )

        db_prompt = self.get_prompt("db_analyzer.txt")
        with open(_AGENTIC_ROOT / "postgres-init.sql", "r") as f:
            db_prompt = db_prompt + "\nHere is the current PostgreSQL schema/init SQL for reference:\n" + f.read()

        self.agents['db_analyzer'] = IkaBaseAgent(
            name="DBAnalyzer",
            description="L2 Worker responsible for analyzing PostgreSQL database for corpus, flags, coverage, and execution state",
            prompt=db_prompt,
            system_prompt="You are DBAnalyzer.",
            tools=[
                db_get_crash_program_as_js_tool,
                base64_program_to_js_tool,
                db_query_tool,
                db_list_programs_tool,
                db_resolve_fuzzer_id_tool,
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
            ],
            model_id=worker_model,
            api_key=self.api_key,
            maxsteps=30,
            max_tokens=TOKENS,
            logging_level=self.logging_level,
            **checkpoint_kwargs,
        )

        self.agents['debugger'] = IkaBaseAgent(
            name="Debugger",
            description="L2 Worker responsible for plateau-focused V8 debugging and execution-state inspection",
            prompt=self.get_prompt("debugger.txt"),
            system_prompt="You are Debugger.",
            tools=[
                execute_javascript_program_tool,
                list_d8_flags_tool,
                list_v8_trace_options_tool,
                trace_v8_analysis_tool,
                get_program_js_from_hash_tool,
                read_from_generate_folder_tool,
                write_to_generate_folder_tool,
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
            model_id=worker_model,
            api_key=self.api_key,
            maxsteps=30,
            max_tokens=TOKENS,
            logging_level=self.logging_level,
            **checkpoint_kwargs,
        )

        self.agents['JS_Generator'] = IkaBaseAgent(
            name="JSGenerator",
            description="L1 Manager responsible for generating JavaScript program seeds to break a coverage plateau",
            prompt=self.get_prompt("JS_generator.txt"),
            system_prompt="You are JSGenerator.",
            tools=[
                db_store_generated_program_tool,
                db_list_programs_tool,
                db_query_tool,
                execute_javascript_program_tool,
                list_d8_flags_tool,
                list_v8_trace_options_tool,
                trace_v8_analysis_tool,
                get_program_js_from_hash_tool,
                read_from_generate_folder_tool,
                list_generate_folder_tool,
                write_and_execute_js_tool,
            ],
            model_id=manager_model,
            api_key=self.api_key,
            subagents=[self.agents['validator']],
            maxsteps=100,
            max_tokens=TOKENS,
            logging_level=self.logging_level,
            **checkpoint_kwargs,
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
                get_program_js_from_hash_tool,
                read_from_generate_folder_tool,
                write_to_generate_folder_tool,
                list_generate_folder_tool,
                search_v8_source_rag_tool,
                search_v8_source_rag_hybrid_tool,
                get_v8_source_rag_doc_tool,
            ],
            model_id=manager_model,
            api_key=self.api_key,
            subagents=[self.agents['v8_search'], self.agents['db_analyzer'], self.agents['debugger']],
            maxsteps=60,
            max_tokens=TOKENS,
            logging_level=self.logging_level,
            **checkpoint_kwargs,
        )

        root_managed = [self.agents['runtime_analyzer'], self.agents['JS_Generator'], self.agents['v8_search']]

        self.agents['root_manager'] = IkaBaseAgent(
            name="RootManager",
            description="L0 Root Manager",
            prompt=root_manager_prompt,
            system_prompt="You are RootManager.",
            tools=[],
            model_id=manager_model,
            api_key=self.api_key,
            subagents=root_managed,
            maxsteps=60,
            max_tokens=TOKENS,
            logging_level=self.logging_level,
            **checkpoint_kwargs,
        )

    def get_prompt(self, prompt_name: str) -> str:
        with open(Path(__file__).parent.parent / "prompts" / "EBG-plateau-prompts" / prompt_name, 'r') as f:
            return f.read()

    def start_system(self):
        result = self.run_task(
            task_description="Initialize EBG Plateau orchestration for runtime analysis and seed verification",
            context={
                "RuntimeAnalyzer": "Analyze program runtime, coverage, and execution state (delegates V8Search, DBAnalyzer, Debugger)",
                "JSGenerator": "Generate and validate JS seeds (delegates Validator)",
                "V8Search": "Locate exact V8 source locations for target code paths",
                "DBAnalyzer": "Retrieve PostgreSQL corpus and execution data for the plateaued fuzzer",
            }
        )
        logger.info("EBG Plateau start result:")
        logger.info(f"Completed: {result['completed']}")
        if result['output']:
            logger.info(f"Output: {result['output']}")
        if result['error']:
            logger.error(f"Error: {result['error']}")
        return result


def main():
    log_path = configure_process_logging("ebg_plateau", "EBG_plateau", logger=logger)
    logger.info(f"Writing logs to {log_path}")
    openai_key = get_openai_api_key()
    anthropic_key = get_anthropic_api_key()
    deepseek_key = get_deepseek_api_key()

    if not openai_key:
        raise RuntimeError("EBG Plateau now requires OPENAI_API_KEY for gpt-5-mini/gpt-5.4 execution")

    os.environ["OPENAI_API_KEY"] = openai_key
    if deepseek_key:
        os.environ["DEEPSEEK_API_KEY"] = deepseek_key

    system = EBG_Plateau(model=None, api_key=openai_key, anthropic_api_key=anthropic_key, fuzzer_id="fuzzer-1")

    result = system.run_task(
        task_description="Verify and test JavaScript program seeds",
        context={
            "RuntimeAnalyzer": "Analyze program execution and coverage",
            "JSGenerator": "Generate and validate JS seeds",
            "V8Search": "Search V8 source for code locations",
            "DBAnalyzer": "Analyze database for execution information",
        }
    )

    logger.info("Task Result:")
    logger.info(f"Completed: {result['completed']}")
    logger.info(f"Output: {result['output']}")
    if result['error']:
        logger.error(f"Error: {result['error']}")


if __name__ == "__main__":
    main()
