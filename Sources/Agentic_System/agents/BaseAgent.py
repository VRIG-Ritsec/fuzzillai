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
from agent_logging import enable_named_logger_from_env

_ikacore_src = Path(__file__).resolve().parent.parent / "IkaCore" / "src"
if str(_ikacore_src) not in sys.path:
    sys.path.insert(0, str(_ikacore_src))

logger = logging.getLogger("base_agent")
if not logger.handlers:
    logger.addHandler(logging.NullHandler())
logger.propagate = False
logger.disabled = True


def _resolve_est_timezone():
    for timezone_name in ("US/Eastern", "America/New_York"):
        try:
            return pytz.timezone(timezone_name)
        except Exception:
            continue
    return pytz.UTC


est_timezone = _resolve_est_timezone()

# Global default logging level for all IkaBaseAgent instances.
# Override by passing logging_level into Agent.__init__.
DEFAULT_AGENT_LOGGING_LEVEL = 1

def enable_base_agent_logging():
    try:
        enable_named_logger_from_env("base_agent")
        logger.disabled = not bool(os.getenv("IKA_AGENT_LOG_FILE", "").strip())
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
        enable_base_agent_logging()
        self.model_id = model_id or (getattr(model, "model_id", None) if model else None) or "deepseek"
        self.api_key = api_key or (getattr(model, "api_key", None) if model else None)
        self.anthropic_api_key = anthropic_api_key
        self.logging_level = DEFAULT_AGENT_LOGGING_LEVEL if logging_level is None else logging_level
        self.agents = {}
        self.setup_agents()

    def get_checkpoint_db_path(self, namespace: str | None = None) -> str:
        override_path = os.getenv("IKA_CHECKPOINT_DB_PATH", "").strip()
        if override_path:
            return override_path

        checkpoint_root_raw = os.getenv("IKA_CHECKPOINT_DIR", "").strip()
        if checkpoint_root_raw:
            checkpoint_root = Path(checkpoint_root_raw).expanduser()
        else:
            checkpoint_root = Path(__file__).resolve().parent.parent / "runtime_data" / "ika_snapshots"
        checkpoint_root.mkdir(parents=True, exist_ok=True)

        checkpoint_name = (namespace or self.__class__.__name__).strip().lower().replace(" ", "_")
        return str(checkpoint_root / f"{checkpoint_name}.db")

    def get_checkpoint_kwargs(self, namespace: str | None = None) -> dict:
        return {
            "checkpoint": True,
            "checkpoint_db_path": self.get_checkpoint_db_path(namespace),
        }

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

    def _find_agent_by_name(self, name: str):
        """
        Search self.agents and their subagents recursively for an agent with the given name.
        Returns the agent instance, or None if not found.
        """
        def _search(agent, target_name: str):
            if getattr(agent, "name", None) == target_name:
                return agent
            for sub in (getattr(agent, "subagents", None) or []):
                found = _search(sub, target_name)
                if found:
                    return found
            return None

        for agent in self.agents.values():
            found = _search(agent, name)
            if found:
                return found
        return None

    def _find_agent_for_checkpoint(self, checkpoint_uid: str, default_agent):
        """
        Load the checkpoint, restore the entire parent chain, pre-prime every agent
        with its saved state, then return the ROOT agent to execute from.

        On resume the root re-runs its step, the LLM (using the restored
        message_history) calls the same subagent, and each subagent in the chain
        finds its _resume_checkpoint pre-loaded so it resumes rather than starting
        fresh.  The deepest agent (the one that actually saved the checkpoint)
        continues from exactly where it stopped.

        Falls back to default_agent if anything cannot be resolved.
        """
        store = getattr(default_agent, "checkpoint_store", None)
        if store is None:
            return default_agent
        try:
            payload = store.load_checkpoint(checkpoint_uid)
            if not payload:
                logger.info(f"Checkpoint {checkpoint_uid} not found in store")
                return default_agent

            saved_by = payload.get("agent_name")
            agent_chain = payload.get("agent_chain", [])
            parent_contexts = payload.get("parent_contexts", [])

            if not saved_by:
                return default_agent

            # ── 1. Pre-prime the target (deepest) agent ──────────────────────
            target = self._find_agent_by_name(saved_by)
            if target is None:
                logger.info(
                    f"Agent '{saved_by}' not found in hierarchy; "
                    f"falling back to {default_agent.name}"
                )
                return default_agent
            target._resume_checkpoint = payload

            # ── 2. Restore every parent in the chain ─────────────────────────
            # parent_contexts[0] = direct parent, parent_contexts[-1] = root.
            # We create a synthetic _resume_checkpoint for each so that
            # subagent_executor skips overwriting their message_history.
            for ctx in parent_contexts:
                parent_agent = self._find_agent_by_name(ctx["agent_name"])
                if parent_agent is None:
                    continue
                synthetic_cp = {
                    "scope": "agent",
                    "agent_name": ctx["agent_name"],
                    "message_history": ctx["message_history"],
                    "remaining_steps": ctx.get("remaining_steps", ctx.get("maxsteps", parent_agent.maxsteps)),
                    "maxsteps": ctx.get("maxsteps", parent_agent.maxsteps),
                    "memory_access": getattr(parent_agent, "memory_access", {}),
                    # Restore the raw LLM conversation so the parent resumes
                    # exactly where it left off rather than restarting fresh.
                    "chat_messages": ctx.get("chat_messages", []),
                }
                parent_agent._resume_checkpoint = synthetic_cp

            # ── 3. Return the root of the chain so execution starts there ─────
            if len(agent_chain) > 1:
                root_name = agent_chain[0]
                root_agent = self._find_agent_by_name(root_name)
                if root_agent is not None:
                    logger.info(
                        f"Resume: restoring chain {' -> '.join(agent_chain)}, "
                        f"starting from root '{root_name}'"
                    )
                    return root_agent

            # Target IS the root (chain length 1)
            logger.info(f"Resume: target '{saved_by}' is the root agent")
            return target

        except Exception as e:
            logger.info(f"Could not resolve checkpoint hierarchy, using default: {e}")
            return default_agent

    def run_task(self, task_description: str, context: dict = None, checkpoint_uid: str = None) -> dict:
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

            if checkpoint_uid:
                # _find_agent_for_checkpoint pre-primes every agent in the chain
                # with its saved state (_resume_checkpoint).  We then call
                # execution() on the ROOT with no checkpoint_uid — each agent
                # picks up its own _resume_checkpoint internally.
                target_agent = self._find_agent_for_checkpoint(checkpoint_uid, manager_agent)
                logger.info(f"Resume: executing from '{target_agent.name}'")
                exec_result = target_agent.execution(checkpoint_uid=None)
            else:
                target_agent = manager_agent
                prompt = self._create_task_prompt(task_description, context)
                base = getattr(manager_agent, "_base_prompt", None) or getattr(manager_agent, "prompt", None)
                full = (base + "\n\n" + prompt) if base else prompt
                logger.info(f"Using {manager_agent.name} for task: {task_description}")
                logger.info(f"Task prompt: {prompt}")
                manager_agent.message_history["first_input"]["message"] = full
                manager_agent.prompt = full
                exec_result = target_agent.execution(checkpoint_uid=None)

            out = exec_result.get("final_message") or exec_result.get("summary") or ""
            results["output"] = str(out) if out else None
            results["completed"] = bool(results["output"])
            logger.info(
                f"Task '{task_description}' completed by '{target_agent.name}' "
                f"at {datetime.now(est_timezone)}"
            )

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
