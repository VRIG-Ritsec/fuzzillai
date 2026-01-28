#!/usr/bin/env python3

from agents.BaseAgent import Agent
from IkaCore.agents import IkaBaseAgent
from pathlib import Path
from tools.ika_tools_registry import (
    fog_george_foreman_tools,
    fog_compiler_tools,
    fog_reviewer_of_code_tools,
    fog_v8_search_tools,
    fog_code_analyzer_tools,
    fog_program_builder_tools,
    fog_pick_section_tools,
    fog_father_of_george_tools,
)
from tools.FoG_tools import get_v8_path
from config_loader import get_openai_api_key, get_anthropic_api_key, get_deepseek_api_key

import sys
import os

sys.path.append(str(Path(__file__).parent.parent))

class Father(Agent):

    def setup_agents(self):
        self.agents['george_foreman'] = IkaBaseAgent(
            name="GeorgeForeman",
            description="L2 Worker responsible for validating program templates built by the program builder",
            prompt="Complete the delegated task.",
            system_prompt="You are GeorgeForeman.",
            tools=fog_george_foreman_tools(),
            model_id="deepseek-chat",
            api_key=self.api_key,
            maxsteps=20,
        )
        self.agents['george_foreman']._base_prompt = self.get_prompt("george_foreman.txt")

        self.agents['compiler'] = IkaBaseAgent(
            name="Compiler",
            description="L2 Worker responsible for compiling program templates built by the program builder",
            prompt="Complete the delegated task.",
            system_prompt="You are Compiler.",
            tools=fog_compiler_tools(),
            model_id="deepseek-chat",
            api_key=self.api_key,
            maxsteps=100,
        )
        self.agents['compiler']._base_prompt = self.get_prompt("compiler.txt")

        self.agents['reviewer_of_code'] = IkaBaseAgent(
            name="ReviewerOfCode",
            description="L2 Worker responsible for reviewing code from various sources using RAG database",
            prompt="Complete the delegated task.",
            system_prompt="You are ReviewerOfCode.",
            tools=fog_reviewer_of_code_tools(),
            model_id="deepseek-chat",
            api_key=self.api_key,
            maxsteps=20,
        )
        self.agents['reviewer_of_code']._base_prompt = self.get_prompt("reviewer_of_code.txt")

        v8_txt = self.get_prompt("v8_search.txt") + "THIS IS THE CURRENT V8 PATH ASSUMING YOU ARE INSIDE THE V8 SOURCE CODE DIRECTORY FOR ALL TOOL CALLS ALREADY: " + get_v8_path()

        self.agents['v8_search'] = IkaBaseAgent(
            name="V8Search",
            description="L2 Worker responsible for searching V8 source code using fuzzy find, regex, and compilation tools",
            prompt="Complete the delegated task.",
            system_prompt="You are V8Search.",
            tools=fog_v8_search_tools(),
            model_id="deepseek-chat",
            api_key=self.api_key,
            maxsteps=50,
        )
        self.agents['v8_search']._base_prompt = v8_txt

        self.agents['code_analyzer'] = IkaBaseAgent(
            name="CodeAnalyzer",
            description="L1 Manager responsible for analyzing code and coordinating retrieval and V8 search operations",
            prompt="Complete the delegated task.",
            system_prompt="You are CodeAnalyzer.",
            tools=fog_code_analyzer_tools(),
            model_id="deepseek-chat",
            api_key=self.api_key,
            subagents=[self.agents['reviewer_of_code'], self.agents['v8_search']],
            maxsteps=15,
        )
        self.agents['code_analyzer']._base_prompt = self.get_prompt("code_analyzer.txt")

        self.agents['program_builder'] = IkaBaseAgent(
            name="ProgramBuilder",
            description="L1 Manager responsible for building program templates using corpus and context",
            prompt="Complete the delegated task.",
            system_prompt="You are ProgramBuilder.",
            tools=fog_program_builder_tools(),
            model_id="deepseek-chat",
            api_key=self.api_key,
            subagents=[self.agents['george_foreman'], self.agents['compiler']],
            maxsteps=30,
        )
        self.agents['program_builder']._base_prompt = self.get_prompt("program_builder.txt")

        self.agents['pick_section'] = IkaBaseAgent(
            name="PickSection",
            description="L0 Root Manager responsible for picking a section of the V8 code base that targets the JIT system",
            prompt="Complete the delegated task.",
            system_prompt="You are PickSection.",
            tools=fog_pick_section_tools(),
            model_id="deepseek-chat",
            api_key=self.api_key,
            maxsteps=30,
        )
        self.agents['pick_section']._base_prompt = self.get_prompt("pick_section.txt")

        self.agents['father_of_george'] = IkaBaseAgent(
            name="FatherOfGeorge",
            description="L0 Manager responsible for orchestrating code analysis and program building operations",
            prompt="The task will be provided at runtime.",
            system_prompt="You are FatherOfGeorge, the root manager.",
            tools=fog_father_of_george_tools(),
            model_id="deepseek-chat",
            api_key=self.api_key,
            subagents=[self.agents['code_analyzer'], self.agents['program_builder'], self.agents['pick_section']],
            maxsteps=30,
        )
        self.agents['father_of_george']._base_prompt = self.get_prompt("root_manager.txt")

    def get_prompt(self, prompt_name: str) -> str:
        with open(Path(__file__).parent.parent / "prompts" / "FoG-prompts" / prompt_name, 'r') as f:
            return f.read()

    def start_system(self):
        result = self.run_task(
            task_description="Initialize Root Manager orchestration",
            context={
                "PickSection": "Select a promising V8 code region to analyze",
                "FatherOfGeorge": "Primary orchestrator of the system, coordinates between analysis and program generation",
                "CodeAnalyzer": "Analyze V8 code and knowledge bases to guide the program template building",
                "ProgramBuilder": "Generate Fuzzilli program templates for fuzzing a specific code region"
            }
        )
        print("FoG start result:")
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

    system = Father(model=None, api_key=deepseek_key, anthropic_api_key=anthropic_key)

    result = system.run_task(
        task_description="Initialize corpus generation for V8 fuzzing",
        context={
            "CodeAnalyzer": "Analyze V8 source code for patterns. vulnerabilities. specifc components, etc...",
            "ProgramBuilder": "Build JavaScript programs using corpus and context"
        }
    )

    print("Task Result:")
    print(f"Completed: {result['completed']}")
    print(f"Output: {result['output']}")
    if result['error']:
        print(f"Error: {result['error']}")


if __name__ == "__main__":
    main()
