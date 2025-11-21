#!/usr/bin/env python3
'''
Ethiopian BG
L0 Manager Agent - Runtime analysis and issue solev
'''

from smolagents import LiteLLMModel, ToolCallingAgent
from agents.BaseAgent import Agent
from pathlib import Path
from tools.EBG_tools import *
from tools.rag_tools import (
    search_rag_db, 
    list_rag_db,
    get_rag_doc,
    search_knowledge_base,
    get_knowledge_doc,
    search_v8_source_rag,
    get_v8_source_rag_doc,
)
from config_loader import get_openai_api_key, get_anthropic_api_key, get_deepseek_api_key
from tools.FoG_tools import get_v8_path

import sys
import yaml 
import importlib.resources

sys.path.append(str(Path(__file__).parent.parent))
global root_manager_prompt
root_manager_prompt = None

class EBG(Agent): 
    """Verify and test seeds."""
    
    def setup_agents(self,root_manager_version: int=1):
        global root_manager_prompt
        if root_manager_version == 1:
            root_manager_prompt = self.get_prompt("plateau_manager.txt")

            # L2 Worker: V8 Search (under RuntimeAnalyzer and CorpusGenerator)
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
                ],
                model=LiteLLMModel(model_id="deepseek", api_key=self.api_key),  
                max_steps=50,
                planning_interval=20,
            )
            self.agents['v8_search'].prompt_templates["system_prompt"] = self.get_prompt("v8_search.txt") + "THIS IS THE CURRENT V8 PATH ASSUMING YOU ARE INSIDE THE V8 SOURCE CODE DIRECTORY FOR ALL TOOL CALLS ALREADY: " + get_v8_path()

            # L2 Worker: Corpus Validator (under RuntimeAnalyzer)
            self.agents['corpus_validator'] = ToolCallingAgent(
                name="CorpusValidator",
                description="L2 Worker responsible for validating corpus integrity and quality",
                tools=[
                    # Add corpus validation tools here
                ],
                model=LiteLLMModel(model_id="deepseek", api_key=self.api_key),
                max_steps=8,
                planning_interval=None,
            )
            self.agents['corpus_validator'].prompt_templates["system_prompt"] = self.get_prompt("corpus_validator.txt")
            
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
                    db_get_program_convergence,
                    db_get_execution_outcome_distribution,
                ],
                model=LiteLLMModel(model_id="deepseek", api_key=self.api_key),
                max_steps=8,
                planning_interval=None,
            )
            self.agents['db_analyzer'].prompt_templates["system_prompt"] = self.get_prompt("db_analyzer.txt")

            self.agents['debugger'] = ToolCallingAgent(
                name="Debugger",
                description="L2 Worker responsible for debugging a crash",
                tools=[],
                model=LiteLLMModel(model_id="deepseek", api_key=self.api_key),
                max_steps=8,
                planning_interval=None,
            )
            self.agents['debugger'].prompt_templates["system_prompt"] = self.get_prompt("debugger.txt")

            
            # L1 Manager: Corpus Generator
            self.agents['JS_Generator'] = ToolCallingAgent(
                name="JSGenerator",
                description="L1 Manager responsible for generating JavaScript program seeds from a crash PoC",
                tools=[],
                model=LiteLLMModel(model_id="deepseek", api_key=self.api_key),
                max_steps=8,
                planning_interval=None,
            )
            self.agents['JS_Generator'].prompt_templates["system_prompt"] = self.get_prompt("JSGenerator.txt")

            # L1 Manager: Runtime Analyzer  
            self.agents['runtime_analyzer'] = ToolCallingAgent(
                name="RuntimeAnalyzer",
                description="L2 Manager responsible for analyzing program runtime, coverage, and execution state",
                tools=[

                ],
                model=LiteLLMModel(model_id="deepseek", api_key=self.api_key),
                managed_agents=[
                    self.agents['v8_search'],
                    self.agents['db_analyzer']
                ],
                max_steps=10,
                planning_interval=None,
            )
            self.agents['runtime_analyzer'].prompt_templates["system_prompt"] = self.get_prompt("runtime_analyzer.txt")

        elif root_manager_version == 2:
            root_manager_prompt = self.get_prompt("variant_manager.txt")

            # L2 Worker: V8 Search (under RuntimeAnalyzer and CorpusGenerator)
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
                ],
                model=LiteLLMModel(model_id="deepseek", api_key=self.api_key),  
                max_steps=50,
                planning_interval=20,
            )
            self.agents['v8_search'].prompt_templates["system_prompt"] = self.get_prompt("v8_search.txt") + "THIS IS THE CURRENT V8 PATH ASSUMING YOU ARE INSIDE THE V8 SOURCE CODE DIRECTORY FOR ALL TOOL CALLS ALREADY: " + get_v8_path()

            # L2 Worker: Corpus Validator (under RuntimeAnalyzer)
            self.agents['corpus_validator'] = ToolCallingAgent(
                name="CorpusValidator",
                description="L2 Worker responsible for validating corpus integrity and quality",
                tools=[
                    # Add corpus validation tools here
                ],
                model=LiteLLMModel(model_id="deepseek", api_key=self.api_key),
                max_steps=8,
                planning_interval=None,
            )
            self.agents['corpus_validator'].prompt_templates["system_prompt"] = self.get_prompt("corpus_validator.txt")
            
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
                    db_get_program_convergence,
                    db_get_execution_outcome_distribution,
                    ],
                model=LiteLLMModel(model_id="deepseek", api_key=self.api_key),
                max_steps=8,
                planning_interval=None,
            )
            self.agents['db_analyzer'].prompt_templates["system_prompt"] = self.get_prompt("db_analyzer.txt")

            self.agents['debugger'] = ToolCallingAgent(
                name="Debugger",
                description="L2 Worker responsible for debugging a crash",
                tools=[],
                model=LiteLLMModel(model_id="deepseek", api_key=self.api_key),
                max_steps=8,
                planning_interval=None,
            )
            self.agents['debugger'].prompt_templates["system_prompt"] = self.get_prompt("debugger.txt")

            
            # L1 Manager: Corpus Generator
            self.agents['JS_Generator'] = ToolCallingAgent(
                name="JSGenerator",
                description="L1 Manager responsible for generating JavaScript program seeds from a crash PoC",
                tools=[],
                model=LiteLLMModel(model_id="deepseek", api_key=self.api_key),
                max_steps=8,
                planning_interval=None,
            )
            self.agents['JS_Generator'].prompt_templates["system_prompt"] = self.get_prompt("JSGenerator.txt")

            # L1 Manager: Runtime Analyzer  
            self.agents['runtime_analyzer'] = ToolCallingAgent(
                name="RuntimeAnalyzer",
                description="L2 Manager responsible for analyzing program runtime, coverage, and execution state",
                tools=[

                ],
                model=LiteLLMModel(model_id="deepseek", api_key=self.api_key),
                managed_agents=[
                    self.agents['v8_search'],
                    self.agents['db_analyzer']
                ],
                max_steps=10,
                planning_interval=None,
            )
            self.agents['runtime_analyzer'].prompt_templates["system_prompt"] = self.get_prompt("runtime_analyzer.txt")

        else:
            raise ValueError(f"Invalid root manager version: {root_manager_version}")

        # L0 Root Manager
        self.agents['root_manager'] = ToolCallingAgent(
            name="RootManager",
            description="L0 Root Manager", 
            tools=[

            ],
            model=LiteLLMModel(model_id="deepseek", api_key=self.api_key),
            managed_agents=[
                self.agents['runtime_analyzer'],
                self.agents['JS_Generator']
            ],
            max_steps=10,
            planning_interval=None,
        )
        self.agents['root_manager'].prompt_templates["system_prompt"] = root_manager_prompt

    def get_prompt(self, prompt_name: str) -> str:
        f = open(Path(__file__).parent.parent / "prompts" / "EBG-prompts" / prompt_name, 'r')
        prompt = f.read()
        f.close()
        return prompt

    def start_system(self):
        result = self.run_task(
            task_description="Initialize EBG orchestration for runtime analysis and seed verification",
            context={
                "RuntimeAnalyzer": "Analyze program runtime, coverage, and execution state",
                "CorpusValidator": "Validate corpus quality and integrity",
                "DBAnalyzer": "Analyze PostgreSQL database for execution information"
            }
        )
        print("EBG start result:")
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
        model_id="deepseek",
        api_key=deepseek_key
    )
    
    system = EBG(model, api_key=deepseek_key, anthropic_api_key=anthropic_key)
    
    # run task
    result = system.run_task(
        task_description="Verify and test JavaScript program seeds",
        context={
            "GeorgeForeman": "Orchestrate verification and testing of JavaScript programs",
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

###
# we need tool calls for EBG to be able to actually put the program templates hard coded into the actual execution of fuzzili  
###
