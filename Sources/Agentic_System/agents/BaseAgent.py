#!/usr/bin/env python3

import sys
import threading
import time
import os
import logging
from datetime import datetime
import pytz
from abc import ABC, abstractmethod
from pathlib import Path

_ikacore_src = Path(__file__).resolve().parent.parent / "IkaCore" / "src"
if str(_ikacore_src) not in sys.path:
    sys.path.insert(0, str(_ikacore_src))

logger = logging.getLogger("base_agent")
if not logger.handlers:
    logger.addHandler(logging.NullHandler())
logger.propagate = False
logger.disabled = True
est_timezone = pytz.timezone('US/Eastern')

# Global default logging level for all IkaBaseAgent instances.
# Override by passing logging_level into Agent.__init__.
DEFAULT_AGENT_LOGGING_LEVEL = 1

def enable_base_agent_logging():
    try:
        if os.getenv("FOG_DEBUG") == "1":
            logs_dir = Path(__file__).parent / "fog_logs"
            logs_dir.mkdir(parents=True, exist_ok=True)
            log_path = logs_dir / "base_agent.log"
            logging.basicConfig(
                filename=str(log_path),
                level=logging.INFO,
                format='%(asctime)s - %(levelname)s - %(message)s'
            )
            logger.disabled = False
        elif os.getenv("EBG_DEBUG") == "1":
            logs_dir = Path(__file__).parent / "ebg_logs"
            logs_dir.mkdir(parents=True, exist_ok=True)
            log_path = logs_dir / "base_agent.log"
            logging.basicConfig(
                filename=str(log_path),
                level=logging.INFO,
                format='%(asctime)s - %(levelname)s - %(message)s'
            )
            logger.disabled = False
    except Exception:
        logger.disabled = True

class Agent(ABC):

    def __init__(
        self,
        model=None,
        api_key: str = None,
        anthropic_api_key: str = None,
        model_id: str = None,
        logging_level=None,
    ):
        self.model_id = model_id or (getattr(model, "model_id", None) if model else None) or "deepseek"
        self.api_key = api_key or (getattr(model, "api_key", None) if model else None)
        self.anthropic_api_key = anthropic_api_key
        self.logging_level = DEFAULT_AGENT_LOGGING_LEVEL if logging_level is None else logging_level
        self.agents = {}
        self.setup_agents()

    def get_prompt(self, prompt_name: str) -> str:
        prompt_path = Path(__file__).parent / "prompts" / prompt_name
        if prompt_path.exists():
            with open(prompt_path, 'r') as f:
                return f.read()
        return ""

    @abstractmethod
    def setup_agents(self):
        pass

    def get_manager_agent(self):
        if 'father_of_george' in self.agents:
            return self.agents['father_of_george']
        if 'root_manager' in self.agents:
            return self.agents['root_manager']
        for key, agent in self.agents.items():
            if hasattr(agent, 'description') and 'manager' in agent.description.lower():
                return agent
            if hasattr(agent, 'name') and 'manager' in agent.name.lower():
                return agent
        if self.agents:
            return list(self.agents.values())[0]
        return None

    def get_all_agents(self) -> list:
        return list(self.agents.values())

    def run_task(self, task_description: str, context: dict = None) -> dict:
        results = {
            "task_description": task_description,
            "completed": False,
            "output": None,
            "error": None,
        }

        try:

            manager_agent = self.get_manager_agent()
            if not manager_agent:
                results["error"] = "No manager agent found for this system"
                return results

            prompt = self._create_task_prompt(task_description, context)
            base = getattr(manager_agent, "_base_prompt", None) or getattr(manager_agent, "prompt", None)
            full = (base + "\n\n" + prompt) if base else prompt

            logger.info(f"Using {manager_agent.name} for task: {task_description}")
            logger.info(f"Task prompt: {prompt}")

            manager_agent.message_history["first_input"]["message"] = full
            manager_agent.prompt = full

            exec_result = manager_agent.execution()
            out = exec_result.get("final_message") or exec_result.get("summary") or ""
            results["output"] = str(out) if out else None
            results["completed"] = bool(results["output"])

            logger.info(f"Task '{task_description}' completed successfully by {manager_agent.name} at {datetime.now(est_timezone)}")

        except Exception as e:
            results["error"] = str(e)
            logger.info(f"Error running task: {e}")

        return results

    def _create_task_prompt(self, task_description: str, context: dict = None) -> str:
        prompt = f"Task: {task_description}\n\n"
        if context:
            prompt += "Context:\n"
            for key, value in context.items():
                prompt += f"- {key}: {value}\n"
            prompt += "\n"
        return prompt
