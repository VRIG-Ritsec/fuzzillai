#!/usr/bin/env python3

from agents.BaseAgent import Agent
from IkaCore.agents import IkaBaseAgent
from pathlib import Path
from tools.FoG_tools import (
    run_python_tool,
    get_v8_path_tool,
    get_realpath_tool,
    tree_tool,
    ripgrep_tool,
    fuzzy_finder_tool,
    read_file_tool,
    lift_fuzzil_to_js_tool,
    compile_js_to_fuzzil_tool,
    write_agent_memory_tool,
    read_agent_memory_tool,
    list_agent_memory_ids_tool,
    search_js_file_name_by_pattern_tool,
    get_js_entry_data_by_name_tool,
    get_all_js_file_names_tool,
    get_random_entry_data_tool,
    get_all_template_names_from_json_tool,
    get_template_from_json_by_name_tool,
    search_template_file_json_tool,
    search_regex_template_swift_tool,
    search_regex_template_fuzzil_tool,
    get_random_template_swift_tool,
    get_random_template_fuzzil_tool,
    similar_template_swift_tool,
    similar_template_fuzzil_tool,
    swift_fuzzy_finder_tool,
    swift_tree_tool,
    swift_ripgrep_tool,
    swift_read_file_tool,
    edit_program_template_file_tool,
    list_program_templates_tool,
    compile_program_template_tool,
    execute_javascript_program_tool,
    list_d8_flags_tool,
)
from tools.RAG_tools import (
    search_knowledge_base_tool,
    search_knowledge_base_hybrid_tool,
    get_knowledge_doc_tool,
    search_v8_source_rag_tool,
    search_v8_source_rag_hybrid_tool,
    get_v8_source_rag_doc_tool,
    search_chromium_issues_rag_tool,
    search_chromium_issues_rag_hybrid_tool,
)
from tools._shared import (
    web_search_tool,
    get_cfg_for_tool,
    get_call_graph_hashmap_tool,
    find_functions_by_simple_name_tool,
    find_functions_by_fully_qualified_name_tool,
    get_call_graph_node_tool,
)
from tools.FoG_tools import get_v8_path
from tools.FoG_tools._shared import _init_session
from config_loader import get_openai_api_key, get_anthropic_api_key, get_deepseek_api_key

import sys
import os
import logging
from agent_logging import configure_process_logging

sys.path.append(str(Path(__file__).parent.parent))

WORKER_MODEL = os.environ.get("FOG_WORKER_MODEL", "gpt-5.4-mini")
MANAGER_MODEL = os.environ.get("FOG_MANAGER_MODEL", "gpt-5.4-mini")
ROOT_MODEL = os.environ.get("FOG_ROOT_MODEL", "gpt-5.4")
logger = logging.getLogger("fog")
if not logger.handlers:
    logger.addHandler(logging.NullHandler())
logger.propagate = False
logger.disabled = True

class Father(Agent):

    def setup_agents(self):
        _init_session()
        checkpoint_kwargs = self.get_checkpoint_kwargs("fog")
        self.agents['static_verfication'] = IkaBaseAgent(
            name="static_verfication",
            description="L2 Worker responsible for static verification of program templates produced by ProgramBuilder",
            prompt=self.get_prompt("static_verfication.txt"),
            system_prompt="You are static_verfication, the static verification worker.",
            tools=[
                get_all_template_names_from_json_tool,
                get_template_from_json_by_name_tool,
                search_template_file_json_tool,
                search_regex_template_swift_tool,
                search_regex_template_fuzzil_tool,
                similar_template_swift_tool,
                similar_template_fuzzil_tool,
                web_search_tool,
                read_agent_memory_tool,
                list_agent_memory_ids_tool,
                search_knowledge_base_tool,
                search_knowledge_base_hybrid_tool,
                get_knowledge_doc_tool,
                search_v8_source_rag_tool,
                search_v8_source_rag_hybrid_tool,
                get_v8_source_rag_doc_tool,
                search_chromium_issues_rag_tool,
                search_chromium_issues_rag_hybrid_tool,
            ],
            model_id=WORKER_MODEL,
            api_key=self.api_key,
            step_timeout=9000,
            maxsteps=20,
            logging_level=self.logging_level,
            **checkpoint_kwargs,
        )
        self.agents['static_verfication']._base_prompt = self.get_prompt("static_verfication.txt")

        self.agents['compiler'] = IkaBaseAgent(
            name="Compiler",
            description="L2 Worker responsible for compiling program templates built by the program builder",
            prompt=self.get_prompt("compiler.txt"),
            system_prompt="You are Compiler.",
            tools=[
                swift_fuzzy_finder_tool,
                swift_tree_tool,
                swift_ripgrep_tool,
                swift_read_file_tool,
                edit_program_template_file_tool,
                compile_program_template_tool,
                execute_javascript_program_tool,
                list_program_templates_tool,
                web_search_tool,
                list_d8_flags_tool,
            ],
            model_id=WORKER_MODEL,
            api_key=self.api_key,
            step_timeout=9000,
            maxsteps=100,
            logging_level=self.logging_level,
            **checkpoint_kwargs,
        )
        self.agents['compiler']._base_prompt = self.get_prompt("compiler.txt")

        self.agents['reviewer_of_code'] = IkaBaseAgent(
            name="ReviewerOfCode",
            description="L2 Worker responsible for reviewing code from various sources using RAG database",
            prompt=self.get_prompt("reviewer_of_code.txt"),
            system_prompt="You are ReviewerOfCode.",
            tools=[
                fuzzy_finder_tool,
                ripgrep_tool,
                tree_tool,
                web_search_tool,
                search_knowledge_base_tool,
                search_knowledge_base_hybrid_tool,
                get_knowledge_doc_tool,
                search_v8_source_rag_hybrid_tool,
                get_v8_source_rag_doc_tool,
                search_chromium_issues_rag_tool,
                search_chromium_issues_rag_hybrid_tool,
            ],
            model_id=WORKER_MODEL,
            api_key=self.api_key,
            maxsteps=20,
            logging_level=self.logging_level,
            **checkpoint_kwargs,
        )
        self.agents['reviewer_of_code']._base_prompt = self.get_prompt("reviewer_of_code.txt")

        v8_txt = self.get_prompt("v8_search.txt") + "THIS IS THE CURRENT V8 PATH ASSUMING YOU ARE INSIDE THE V8 SOURCE CODE DIRECTORY FOR ALL TOOL CALLS ALREADY: " + get_v8_path()

        self.agents['v8_search'] = IkaBaseAgent(
            name="V8Search",
            description="L2 Worker responsible for searching V8 source code using fuzzy find, regex, and compilation tools",
            prompt=v8_txt,
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
            model_id=WORKER_MODEL,
            api_key=self.api_key,
            maxsteps=50,
            logging_level=self.logging_level,
            **checkpoint_kwargs,
        )
        self.agents['v8_search']._base_prompt = v8_txt

        self.agents['code_analyzer'] = IkaBaseAgent(
            name="CodeAnalyzer",
            description="L1 Manager responsible for analyzing code and coordinating retrieval and V8 search operations",
            prompt=self.get_prompt("code_analyzer.txt"),
            system_prompt="You are CodeAnalyzer.",
            tools=[
                run_python_tool,
                lift_fuzzil_to_js_tool,
                compile_js_to_fuzzil_tool,
                web_search_tool,
                search_knowledge_base_tool,
                search_knowledge_base_hybrid_tool,
                get_knowledge_doc_tool,
                read_agent_memory_tool,
                search_v8_source_rag_tool,
                search_v8_source_rag_hybrid_tool,
                get_v8_source_rag_doc_tool,
                list_agent_memory_ids_tool,
                search_chromium_issues_rag_tool,
                search_chromium_issues_rag_hybrid_tool,
            ],
            model_id=MANAGER_MODEL,
            api_key=self.api_key,
            subagents=[self.agents['reviewer_of_code'], self.agents['v8_search']],
            maxsteps=15,
            logging_level=self.logging_level,
            **checkpoint_kwargs,
        )
        self.agents['code_analyzer']._base_prompt = self.get_prompt("code_analyzer.txt")

        self.agents['program_builder'] = IkaBaseAgent(
            name="ProgramBuilder",
            description="L1 Manager responsible for building program templates using corpus and context",
            prompt=self.get_prompt("program_builder.txt"),
            system_prompt="You are ProgramBuilder.",
            tools=[
                get_all_template_names_from_json_tool,
                get_template_from_json_by_name_tool,
                list_program_templates_tool,
                get_random_template_swift_tool,
                get_random_template_fuzzil_tool,
                search_template_file_json_tool,
                search_regex_template_swift_tool,
                search_regex_template_fuzzil_tool,
                similar_template_swift_tool,
                similar_template_fuzzil_tool,
            ],
            model_id=MANAGER_MODEL,
            api_key=self.api_key,
            subagents=[self.agents['static_verfication'], self.agents['compiler']],
            maxsteps=30,
            step_timeout=9000,
            logging_level=self.logging_level,
            **checkpoint_kwargs,
        )
        self.agents['program_builder']._base_prompt = self.get_prompt("program_builder.txt")

        self.agents['pick_section'] = IkaBaseAgent(
            name="PickSection",
            description="L0 Root Manager responsible for picking a section of the V8 code base that targets the JIT system",
            prompt=self.get_prompt("pick_section.txt"),
            system_prompt="You are PickSection.",
            tools=[
                search_js_file_name_by_pattern_tool,
                get_js_entry_data_by_name_tool,
                get_all_js_file_names_tool,
                get_random_entry_data_tool,
                search_knowledge_base_tool,
                search_knowledge_base_hybrid_tool,
                get_knowledge_doc_tool,
                search_v8_source_rag_hybrid_tool,
                list_program_templates_tool,
                list_d8_flags_tool,
            ],
            model_id=MANAGER_MODEL,
            api_key=self.api_key,
            maxsteps=30,
            logging_level=self.logging_level,
            **checkpoint_kwargs,
        )
        self.agents['pick_section']._base_prompt = self.get_prompt("pick_section.txt")

        self.agents['root_manager'] = IkaBaseAgent(
            name="RootManager",
            description="L0 Manager responsible for orchestrating code analysis and program building operations",
            prompt=self.get_prompt("root_manager.txt"),
            system_prompt="You are RootManager, the top-level orchestration manager.",
            tools=[
                search_knowledge_base_tool,
                search_knowledge_base_hybrid_tool,
                get_knowledge_doc_tool,
                search_v8_source_rag_tool,
                search_v8_source_rag_hybrid_tool,
                get_v8_source_rag_doc_tool,
                web_search_tool,
                search_chromium_issues_rag_tool,
                search_chromium_issues_rag_hybrid_tool,
            ],
            model_id=ROOT_MODEL,
            api_key=self.api_key,
            subagents=[self.agents['code_analyzer'], self.agents['program_builder'], self.agents['pick_section']],
            maxsteps=30,
            logging_level=self.logging_level,
            **checkpoint_kwargs,
        )
        self.agents['root_manager']._base_prompt = self.get_prompt("root_manager.txt")

    def get_prompt(self, prompt_name: str) -> str:
        with open(Path(__file__).parent.parent / "prompts" / "FoG-prompts" / prompt_name, 'r') as f:
            return f.read()

    def start_system(self, checkpoint_uid=None):
        result = self.run_task(
            task_description="Initialize Root Manager orchestration",
            context={
                "PickSection": "Select a promising V8 code region to analyze",
                "RootManager": "Primary orchestrator of the system, coordinates between analysis and program generation",
                "CodeAnalyzer": "Analyze V8 code and knowledge bases to guide the program template building",
                "ProgramBuilder": "Generate Fuzzilli program templates for fuzzing a specific code region"
            },
            checkpoint_uid=checkpoint_uid,
        )
        logger.info("FoG start result:")
        logger.info(f"Completed: {result['completed']}")
        if result['output']:
            logger.info(f"Output: {result['output']}")
        if result['error']:
            logger.error(f"Error: {result['error']}")
        return result


def main():
    log_path = configure_process_logging("fog", "FoG", logger=logger)
    logger.info(f"Writing logs to {log_path}")
    openai_key = get_openai_api_key()
    anthropic_key = get_anthropic_api_key()
    deepseek_key = get_deepseek_api_key()

    if not openai_key:
        raise RuntimeError("FoG now requires OPENAI_API_KEY for gpt-5-mini/gpt-5.4 execution")

    if deepseek_key:
        os.environ["DEEPSEEK_API_KEY"] = deepseek_key
    if openai_key:
        os.environ["OPENAI_API_KEY"] = openai_key

    system = Father(model=None, api_key=openai_key, anthropic_api_key=anthropic_key)

    result = system.run_task(
        task_description="Initialize corpus generation for V8 fuzzing",
        context={
            "CodeAnalyzer": "Analyze V8 source code for patterns. vulnerabilities. specifc components, etc...",
            "ProgramBuilder": "Build JavaScript programs using corpus and context"
        }
    )

    logger.info("Task Result:")
    logger.info(f"Completed: {result['completed']}")
    logger.info(f"Output: {result['output']}")
    if result['error']:
        logger.error(f"Error: {result['error']}")


if __name__ == "__main__":
    main()
