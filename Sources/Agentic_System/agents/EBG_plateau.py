#!/usr/bin/env python3
'''
EBG Plateau
L0 Manager Agent - Plateau analysis
'''

from smolagents import LiteLLMModel, ToolCallingAgent
from agents.BaseAgent import Agent
from pathlib import Path
from tools.EBG_tools import *
from tools.rag_tools import (
    set_rag_collection,
    get_rag_collection,
    search_rag_db,
    update_rag_db,
    delete_rag_db,
    list_rag_db,
    get_rag_doc,
    search_knowledge_base,
    get_knowledge_doc,
    search_v8_source_rag,
    get_v8_source_rag_doc, 
    FAISSKnowledgeBase,
)
from config_loader import get_openai_api_key, get_anthropic_api_key, get_deepseek_api_key
from tools.FoG_tools import get_v8_path

import sys
import os
import yaml 
import importlib.resources
from typing import Optional


MANAGER_MODEL = "deepseek"
WORKER_MODEL = "deepseek"
ANALYZER_MODEL = "deepseek"

sys.path.append(str(Path(__file__).parent.parent))
global root_manager_prompt
root_manager_prompt = None

class EBG_Plateau(Agent): 
    def __init__(self, model: LiteLLMModel, api_key: str = None, anthropic_api_key: str = None, fuzzer_id: Optional[str] = None):
        if fuzzer_id is None:
            raise ValueError("fuzzer_id must be provided for EBG_Plateau")
        self.fuzzer_id = fuzzer_id
        super().__init__(model, api_key, anthropic_api_key)
    
    def setup_agents(self, fuzzer_id: Optional[str] = None):
        if fuzzer_id is None:
            fuzzer_id = getattr(self, 'fuzzer_id', None)
        if fuzzer_id is None:
            return
        """
        Plateau Manager

        This is the version of EBG that gets called after a fuzzing instance has hit a plateau in coverage.
        Its call is to figure out why the plateau is happening and how to escape it by finding new variants of the code that are not already in the corpus.

        Root Manager (L0)
        ├── Runtime Analyzer (L1)
        │   ├── V8 Search (L2)
        │   ├── DB Analyzer (L2)
        │   └── Debugger (L2)
        └── JS Generator (L1)
            └── Corpus Validator (L2)
        """
        global root_manager_prompt
        root_manager_prompt = self.get_prompt("plateau_manager.txt")
        root_manager_prompt = root_manager_prompt.replace("[ENTER THE PLATEAUED FUZZER]", fuzzer_id)

        # L2 Worker: V8 Search 
        self.agents['v8_search'] = ToolCallingAgent(
            name="V8Search",
            description="L2 Worker responsible for searching V8 source code using fuzzy find, regex, and compilation tools",
            tools=[
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
            ],
            model=LiteLLMModel(model_id=WORKER_MODEL, api_key=self.api_key),  
            max_steps=50,
            planning_interval=20,
        )
        self.agents['v8_search'].prompt_templates["system_prompt"] = self.get_prompt("v8_search.txt") + "THIS IS THE CURRENT V8 PATH ASSUMING YOU ARE INSIDE THE V8 SOURCE CODE DIRECTORY FOR ALL TOOL CALLS ALREADY: " + get_v8_path()

        # L2 Worker: DB Analyzer  
        self.agents['db_analyzer'] = ToolCallingAgent(
            name="DBAnalyzer",
            description="L2 Worker responsible for analyzing PostgreSQL database for corpus, flags, coverage, and execution state",
            tools=[
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
            ],
            model=LiteLLMModel(model_id=WORKER_MODEL, api_key=self.api_key),
            max_steps=30,
            planning_interval=None,
        )
        prompt = self.get_prompt("db_analyzer.txt")
        f = open(FUZZILLI_PATH + "/postgres-init.sql", "r")
        sql_file = f.read()
        f.close()
        prompt = prompt + "\n Here is the latest programs from the database: " + sql_file
        self.agents['db_analyzer'].prompt_templates["system_prompt"] = prompt

        # L2 Worker: Debugger 
        self.agents['debugger'] = ToolCallingAgent(
            name="Debugger",
            description="L2 Worker responsible for debugging a crash",
            tools=[],
            model=LiteLLMModel(model_id=WORKER_MODEL, api_key=self.api_key),
            max_steps=30,
            planning_interval=None,
        )
        self.agents['debugger'].prompt_templates["system_prompt"] = self.get_prompt("debugger.txt")

        # L1 Manager: JS Generator
        self.agents['JS_Generator'] = ToolCallingAgent(
            name="JSGenerator",
            description="L1 Manager responsible for generating JavaScript program seeds from a crash PoC",
            tools=[],
            model=LiteLLMModel(model_id=MANAGER_MODEL, api_key=self.api_key),
            max_steps=30,
            planning_interval=None,
        )
        self.agents['JS_Generator'].prompt_templates["system_prompt"] = self.get_prompt("JS_generator.txt")
        self.agents['JS_Generator'].managed_agents = []

        # L1 Manager: Runtime Analyzer  
        self.agents['runtime_analyzer'] = ToolCallingAgent(
            name="RuntimeAnalyzer",
            description="L1 Manager responsible for analyzing program runtime, coverage, and execution state",
            tools=[
                execute_javascript_program,
                list_d8_flags,
                list_v8_trace_options,
                trace_v8_analysis,
                read_from_generate_folder,
                list_generate_folder,
            ],
            model=LiteLLMModel(model_id=MANAGER_MODEL, api_key=self.api_key),
            managed_agents=[
                self.agents['v8_search'],
                self.agents['db_analyzer'],
                self.agents['debugger']
            ],
            max_steps=30,
            planning_interval=None,
        )
        self.agents['runtime_analyzer'].prompt_templates["system_prompt"] = self.get_prompt("runtime_analyzer.txt")

        # L0 Root Manager
        root_managed_agents = [
            self.agents['runtime_analyzer'],
            self.agents['JS_Generator']
        ]

        self.agents['root_manager'] = ToolCallingAgent(
            name="RootManager",
            description="L0 Root Manager", 
            tools=[

            ],
            model=LiteLLMModel(model_id=MANAGER_MODEL, api_key=self.api_key),
            managed_agents=root_managed_agents,
            max_steps=30,
            planning_interval=None,
        )
        self.agents['root_manager'].prompt_templates["system_prompt"] = root_manager_prompt

    def get_prompt(self, prompt_name: str) -> str:
        f = open(Path(__file__).parent.parent / "prompts" / "EBG-plateau-prompts" / prompt_name, 'r')
        prompt = f.read()
        f.close()
        return prompt

    def start_system(self):
        result = self.run_task(
            task_description="Initialize EBG Plateau orchestration for runtime analysis and seed verification",
            context={
                "RuntimeAnalyzer": "Analyze program runtime, coverage, and execution state",
                "CorpusValidator": "Validate corpus quality and integrity",
                "DBAnalyzer": "Analyze PostgreSQL database for execution information"
            }
        )
        print("EBG Plateau start result:")
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
    
    model = LiteLLMModel(
        model_id=MANAGER_MODEL,
        api_key=deepseek_key
    )

    system = EBG_Plateau(model, api_key=deepseek_key, anthropic_api_key=anthropic_key, fuzzer_id="fuzzer-1")
    
    result = system.run_task(
        task_description="Verify and test JavaScript program seeds",
        context={
            "RuntimeAnalyzer": "Analyze program execution and coverage",
            "CorpusValidator": "Validate corpus quality and integrity",
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
