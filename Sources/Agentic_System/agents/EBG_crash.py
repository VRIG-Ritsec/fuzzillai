#!/usr/bin/env python3
'''
EBG Crash
L0 Manager Agent - Crash analysis and variant generation
'''

from agents.BaseAgent import Agent
from IkaCore.agents import IkaBaseAgent
from Adapter.adapter import function_to_ika_tool
from pathlib import Path
from tools.EBG_tools import (
    FUZZILLI_PATH,
    create_generate_folder,
    base64_program_to_js,
    db_query,
    db_list_programs,
    db_get_fuzzer_performance_summary,
    db_list_fuzzers,
    db_get_crash_diversity,
    db_get_mutator_effectiveness,
    db_get_program_grouping,
    db_get_execution_outcome_distribution,
    read_from_generate_folder,
    write_to_generate_folder,
    delete_files_from_generate_folder,
    list_generate_folder,
    execute_javascript_program,
    list_d8_flags,
    list_v8_trace_options,
    trace_v8_analysis,
    get_program_js_from_hash,
    start_mi_debug_session,
    stop_mi_debug_session,
    mi_exec,
    mi_run,
    mi_step,
    mi_next,
    mi_continue,
    gdb_run_command,
    gdb_set_breakpoint,
    gdb_print_value,
    pwndbg_context,
    pwndbg_vmmap,
    pwndbg_regs,
    pwndbg_nearpc,
    read_file,
    read_rag_db_id,
    write_rag_db_id,
    get_runtime_db_ids,
    get_cfg_for,
    get_call_graph_hashmap,
    find_functions_by_simple_name,
    find_functions_by_fully_qualified_name,
    get_call_graph_node,
)
from config_loader import get_openai_api_key, get_anthropic_api_key, get_deepseek_api_key
from tools.FoG_tools import get_v8_path, fuzzy_finder, ripgrep, tree, get_realpath

import sys
import os
from typing import Optional

MANAGER_MODEL = "deepseek"
WORKER_MODEL = "deepseek"

sys.path.append(str(Path(__file__).parent.parent))

def _tools(*fs):
    return [function_to_ika_tool(f) for f in fs]

class EBG_Crash(Agent):

    def __init__(self, model=None, api_key: str = None, anthropic_api_key: str = None, crash_program_hash: Optional[str] = None):
        if crash_program_hash is None:
            raise ValueError("crash_program_hash must be provided for EBG_Crash")
        self.crash_program_hash = crash_program_hash
        super().__init__(model, api_key, anthropic_api_key)

    def setup_agents(self, crash_program_hash: Optional[str] = None):
        if crash_program_hash is None:
            crash_program_hash = getattr(self, 'crash_program_hash', None)
        if crash_program_hash is None:
            return

        root_manager_prompt = self.get_prompt("variant_manager.txt")
        root_manager_prompt = root_manager_prompt.replace("[ENTER SELECTED CRASH NAME]", crash_program_hash)

        v8_sys = self.get_prompt("v8_search.txt") + "THIS IS THE CURRENT V8 PATH ASSUMING YOU ARE INSIDE THE V8 SOURCE CODE DIRECTORY FOR ALL TOOL CALLS ALREADY: " + get_v8_path()

        self.agents['v8_search'] = IkaBaseAgent(
            name="V8Search",
            description="L2 Worker responsible for searching V8 source code using fuzzy find, regex, and compilation tools",
            prompt="Complete the delegated task.",
            system_prompt=v8_sys,
            tools=_tools(
                fuzzy_finder,
                ripgrep,
                tree,
                read_rag_db_id,
                write_rag_db_id,
                read_file,
                get_realpath,
                get_runtime_db_ids,
                get_cfg_for,
                get_call_graph_hashmap,
                find_functions_by_simple_name,
                find_functions_by_fully_qualified_name,
                get_call_graph_node,
            ),
            model_id=WORKER_MODEL,
            api_key=self.api_key,
            maxsteps=50,
        )

        db_prompt = self.get_prompt("db_analyzer.txt")
        with open(FUZZILLI_PATH + "/postgres-init.sql", "r") as f:
            db_prompt = db_prompt + "\n Here is the latest programs from the database: " + f.read()

        self.agents['db_analyzer'] = IkaBaseAgent(
            name="DBAnalyzer",
            description="L2 Worker responsible for analyzing PostgreSQL database for corpus, flags, coverage, and execution state",
            prompt="Complete the delegated task.",
            system_prompt=db_prompt,
            tools=_tools(
                base64_program_to_js,
                db_query,
                db_list_programs,
                db_get_fuzzer_performance_summary,
                db_list_fuzzers,
                db_get_crash_diversity,
                db_get_mutator_effectiveness,
                db_get_program_grouping,
                db_get_execution_outcome_distribution,
                read_from_generate_folder,
                write_to_generate_folder,
                delete_files_from_generate_folder,
                list_generate_folder,
                create_generate_folder,
            ),
            model_id=WORKER_MODEL,
            api_key=self.api_key,
            maxsteps=30,
        )

        self.agents['debugger'] = IkaBaseAgent(
            name="Debugger",
            description="L2 Worker responsible for debugging a crash",
            prompt="Complete the delegated task.",
            system_prompt=self.get_prompt("debugger.txt"),
            tools=_tools(
                execute_javascript_program,
                list_d8_flags,
                list_v8_trace_options,
                trace_v8_analysis,
                get_program_js_from_hash,
                read_from_generate_folder,
                list_generate_folder,
                start_mi_debug_session,
                stop_mi_debug_session,
                mi_exec,
                mi_run,
                mi_step,
                mi_next,
                mi_continue,
                gdb_run_command,
                gdb_set_breakpoint,
                gdb_print_value,
                pwndbg_context,
                pwndbg_vmmap,
                pwndbg_regs,
                pwndbg_nearpc,
            ),
            model_id=WORKER_MODEL,
            api_key=self.api_key,
            maxsteps=30,
        )

        self.agents['JS_Generator'] = IkaBaseAgent(
            name="JSGenerator",
            description="L2 Worker responsible for generating JavaScript program seeds from a crash PoC",
            prompt="Complete the delegated task.",
            system_prompt=self.get_prompt("JS_generator.txt"),
            tools=[],
            model_id=WORKER_MODEL,
            api_key=self.api_key,
            maxsteps=30,
        )

        self.agents['runtime_analyzer'] = IkaBaseAgent(
            name="RuntimeAnalyzer",
            description="L1 Manager responsible for analyzing program runtime, coverage, and execution state",
            prompt="Complete the delegated task.",
            system_prompt=self.get_prompt("runtime_analyzer.txt"),
            tools=_tools(
                execute_javascript_program,
                list_d8_flags,
                list_v8_trace_options,
                read_from_generate_folder,
                list_generate_folder,
            ),
            model_id=MANAGER_MODEL,
            api_key=self.api_key,
            subagents=[self.agents['v8_search'], self.agents['db_analyzer'], self.agents['debugger']],
            maxsteps=30,
        )

        self.agents['variant_analysis'] = IkaBaseAgent(
            name="VariantAnalysis",
            description="L1 Manager responsible for performing variant analysis on crashes",
            prompt="Complete the delegated task.",
            system_prompt=self.get_prompt("variant_analysis.txt"),
            tools=_tools(
                execute_javascript_program,
                list_d8_flags,
                list_v8_trace_options,
                trace_v8_analysis,
                read_from_generate_folder,
                list_generate_folder,
            ),
            model_id=MANAGER_MODEL,
            api_key=self.api_key,
            subagents=[self.agents['v8_search'], self.agents['debugger'], self.agents['JS_Generator']],
            maxsteps=30,
        )

        root_managed = [self.agents['runtime_analyzer'], self.agents['variant_analysis']]

        self.agents['root_manager'] = IkaBaseAgent(
            name="RootManager",
            description="L0 Root Manager",
            prompt="Orchestrate the following subagents to complete the task. The task will be provided at runtime.",
            system_prompt=root_manager_prompt,
            tools=[],
            model_id=MANAGER_MODEL,
            api_key=self.api_key,
            subagents=root_managed,
            maxsteps=30,
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
    openai_key = get_openai_api_key()
    anthropic_key = get_anthropic_api_key()
    deepseek_key = get_deepseek_api_key()

    if deepseek_key:
        os.environ["DEEPSEEK_API_KEY"] = deepseek_key

    system = EBG_Crash(model=None, api_key=deepseek_key, anthropic_api_key=anthropic_key, crash_program_hash="test_crash")

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
