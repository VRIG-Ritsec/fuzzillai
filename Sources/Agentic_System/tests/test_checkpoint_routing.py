"""
Tests for checkpoint resume routing in BaseAgent.

Validates that resuming from any agent's checkpoint:
  - Pre-primes every agent in the chain with its saved state
  - Starts execution from the ROOT agent (not the checkpointed subagent)
  - Each subagent resumes via its _resume_checkpoint rather than starting fresh

Run:
    python3 tests/test_checkpoint_routing.py
"""

import copy
import json
import os
import sys
import tempfile
import shutil
import unittest
from pathlib import Path

_here = Path(__file__).resolve().parent
_agentic_dir = _here.parent
_ikacore_src = _agentic_dir / "IkaCore" / "src"
for p in [str(_agentic_dir), str(_ikacore_src)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from IkaCore.checkpoint import CheckpointStore
from agents.BaseAgent import Agent


# ---------------------------------------------------------------------------
# Minimal IkaBaseAgent stand-in
# ---------------------------------------------------------------------------

class _DummyIkaAgent:
    """Minimal stand-in for IkaBaseAgent."""
    def __init__(self, name, subagents=None):
        self.name = name
        self.description = f"{name} description"
        self.subagents = subagents or []
        self.message_history = {
            "system": {"message": ""},
            "first_input": {"message": ""},
            "summary": {"message": ""},
            "messages": {},
        }
        self.prompt = ""
        self._base_prompt = ""
        self._resume_checkpoint = None
        self._parent_agent = None
        self.checkpoint_store = None
        self.maxsteps = 30
        # Track what execution() was called with
        self._execution_calls = []

    def execution(self, checkpoint_uid=None):
        self._execution_calls.append({
            "checkpoint_uid": checkpoint_uid,
            "resume_checkpoint": copy.deepcopy(self._resume_checkpoint),
            "message_history_snapshot": copy.deepcopy(self.message_history),
        })
        return {"final_message": f"{self.name} result", "summary": f"{self.name} summary"}


class _ConcreteAgent(Agent):
    """3-level hierarchy: RootManager → ProgramBuilder → Compiler."""
    def setup_agents(self):
        self.agents["compiler"] = _DummyIkaAgent("Compiler")
        self.agents["program_builder"] = _DummyIkaAgent(
            "ProgramBuilder", subagents=[self.agents["compiler"]]
        )
        self.agents["root_manager"] = _DummyIkaAgent(
            "RootManager",
            subagents=[self.agents["program_builder"], self.agents["compiler"]],
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mh(tag: str) -> dict:
    """Create a distinct message_history for each agent."""
    return {
        "system": {"message": f"{tag}-system"},
        "first_input": {"message": f"{tag}-first"},
        "summary": {"message": ""},
        "messages": {f"{tag}-msg1": "hello"},
    }


# ---------------------------------------------------------------------------
# Test suite
# ---------------------------------------------------------------------------

class TestCheckpointRouting(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db_path = str(self.tmp / "test.db")
        self.store = CheckpointStore(self.db_path)
        self.system = _ConcreteAgent(model=None, api_key="test-key")
        # Give every agent the same store (mirrors production setup)
        for agent in self.system.agents.values():
            agent.checkpoint_store = self.store
            for sub in (agent.subagents or []):
                sub.checkpoint_store = self.store

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def _agent(self, key: str) -> _DummyIkaAgent:
        return self.system.agents[key]

    # ── _find_agent_by_name ────────────────────────────────────────────────

    def test_find_root_agent(self):
        self.assertEqual(self.system._find_agent_by_name("RootManager").name, "RootManager")

    def test_find_l1_subagent(self):
        self.assertEqual(self.system._find_agent_by_name("ProgramBuilder").name, "ProgramBuilder")

    def test_find_l2_subagent(self):
        self.assertEqual(self.system._find_agent_by_name("Compiler").name, "Compiler")

    def test_find_missing_returns_none(self):
        self.assertIsNone(self.system._find_agent_by_name("Ghost"))

    # ── _find_agent_for_checkpoint — chain restore ─────────────────────────

    def _save_compiler_checkpoint(self, fog_mh=None, pb_mh=None) -> str:
        """Save a Compiler checkpoint with realistic parent_contexts."""
        payload = {
            "scope": "agent",
            "agent_name": "Compiler",
            "agent_chain": ["RootManager", "ProgramBuilder", "Compiler"],
            "remaining_steps": 5,
            "last_content": "mid-compile",
            "message_history": _make_mh("Compiler"),
            "maxsteps": 100,
            "memory_access": {},
            "timestamp": 0.0,
            "parent_contexts": [
                {
                    "agent_name": "ProgramBuilder",
                    "message_history": pb_mh or _make_mh("ProgramBuilder"),
                    "maxsteps": 30,
                    "remaining_steps": 15,
                },
                {
                    "agent_name": "RootManager",
                    "message_history": fog_mh or _make_mh("RootManager"),
                    "maxsteps": 30,
                    "remaining_steps": 20,
                },
            ],
        }
        return self.store.save_checkpoint(scope="agent", payload=payload)

    def test_compiler_checkpoint_primes_compiler(self):
        uid = self._save_compiler_checkpoint()
        fog = self._agent("root_manager")
        result = self.system._find_agent_for_checkpoint(uid, fog)

        compiler = self._agent("compiler")
        self.assertIsNotNone(compiler._resume_checkpoint)
        self.assertEqual(compiler._resume_checkpoint["agent_name"], "Compiler")

    def test_compiler_checkpoint_primes_program_builder(self):
        uid = self._save_compiler_checkpoint()
        fog = self._agent("root_manager")
        self.system._find_agent_for_checkpoint(uid, fog)

        pb = self._agent("program_builder")
        self.assertIsNotNone(pb._resume_checkpoint)
        self.assertEqual(pb._resume_checkpoint["agent_name"], "ProgramBuilder")

    def test_compiler_checkpoint_primes_root_manager(self):
        uid = self._save_compiler_checkpoint()
        fog = self._agent("root_manager")
        self.system._find_agent_for_checkpoint(uid, fog)

        self.assertIsNotNone(fog._resume_checkpoint)
        self.assertEqual(fog._resume_checkpoint["agent_name"], "RootManager")

    def test_compiler_checkpoint_restores_parent_message_histories(self):
        fog_mh = _make_mh("FOG-specific")
        pb_mh = _make_mh("PB-specific")
        uid = self._save_compiler_checkpoint(fog_mh=fog_mh, pb_mh=pb_mh)
        fog = self._agent("root_manager")
        self.system._find_agent_for_checkpoint(uid, fog)

        # Parent message_histories in the synthetic _resume_checkpoints
        fog_cp = self._agent("root_manager")._resume_checkpoint
        self.assertEqual(fog_cp["message_history"]["first_input"]["message"], "FOG-specific-first")

        pb_cp = self._agent("program_builder")._resume_checkpoint
        self.assertEqual(pb_cp["message_history"]["first_input"]["message"], "PB-specific-first")

    def test_compiler_checkpoint_returns_root_agent(self):
        uid = self._save_compiler_checkpoint()
        fog = self._agent("root_manager")
        result = self.system._find_agent_for_checkpoint(uid, fog)
        self.assertEqual(result.name, "RootManager")

    def test_root_checkpoint_returns_root(self):
        payload = {
            "scope": "agent",
            "agent_name": "RootManager",
            "agent_chain": ["RootManager"],
            "remaining_steps": 20,
            "last_content": "",
            "message_history": _make_mh("RootManager"),
            "maxsteps": 30,
            "memory_access": {},
            "timestamp": 0.0,
            "parent_contexts": [],
        }
        uid = self.store.save_checkpoint(scope="agent", payload=payload)
        fog = self._agent("root_manager")
        result = self.system._find_agent_for_checkpoint(uid, fog)
        self.assertEqual(result.name, "RootManager")

    def test_missing_uid_falls_back_to_default(self):
        fog = self._agent("root_manager")
        result = self.system._find_agent_for_checkpoint("bad-uid", fog)
        self.assertEqual(result.name, "RootManager")

    def test_unknown_agent_name_falls_back_to_default(self):
        payload = {
            "scope": "agent",
            "agent_name": "Ghost",
            "agent_chain": ["RootManager", "Ghost"],
            "remaining_steps": 5,
            "message_history": {},
            "maxsteps": 10,
            "memory_access": {},
            "timestamp": 0.0,
            "parent_contexts": [],
        }
        uid = self.store.save_checkpoint(scope="agent", payload=payload)
        fog = self._agent("root_manager")
        result = self.system._find_agent_for_checkpoint(uid, fog)
        self.assertEqual(result.name, "RootManager")

    # ── run_task: execution is always called on root, never on the subagent ─

    def test_run_task_with_compiler_checkpoint_calls_root_not_compiler(self):
        uid = self._save_compiler_checkpoint()
        self.system.run_task("test", checkpoint_uid=uid)

        fog = self._agent("root_manager")
        compiler = self._agent("compiler")

        # Root must have been called
        self.assertEqual(len(fog._execution_calls), 1)
        # Compiler must NOT have been directly called by run_task
        self.assertEqual(len(compiler._execution_calls), 0,
                         "run_task must NOT call Compiler.execution() directly — "
                         "the parent (RootManager) drives the chain")

    def test_run_task_with_compiler_checkpoint_passes_no_uid_to_execution(self):
        """execution() is called with checkpoint_uid=None; pre-priming handles it."""
        uid = self._save_compiler_checkpoint()
        self.system.run_task("test", checkpoint_uid=uid)
        fog = self._agent("root_manager")
        self.assertIsNone(fog._execution_calls[0]["checkpoint_uid"])

    def test_run_task_with_compiler_checkpoint_fog_has_resume_cp(self):
        """RootManager must have _resume_checkpoint set when execution() is called."""
        uid = self._save_compiler_checkpoint()
        self.system.run_task("test", checkpoint_uid=uid)
        fog = self._agent("root_manager")
        # The snapshot taken at call time must contain the restored checkpoint
        self.assertIsNotNone(fog._execution_calls[0]["resume_checkpoint"])

    def test_run_task_compiler_has_resume_cp_before_root_executes(self):
        """Compiler._resume_checkpoint must be set (not None) when root starts."""
        uid = self._save_compiler_checkpoint()
        self.system.run_task("test", checkpoint_uid=uid)
        fog = self._agent("root_manager")
        # Compiler's _resume_checkpoint is set BEFORE fog.execution() is called
        compiler_cp = self._agent("compiler")._resume_checkpoint
        # After run_task, _resume_checkpoint still holds the payload (our dummy
        # execution() doesn't clear it, unlike the real subagent_executor).
        self.assertIsNotNone(compiler_cp)

    def test_run_task_root_checkpoint_also_works(self):
        payload = {
            "scope": "agent",
            "agent_name": "RootManager",
            "agent_chain": ["RootManager"],
            "remaining_steps": 20,
            "last_content": "",
            "message_history": _make_mh("RootManager"),
            "maxsteps": 30,
            "memory_access": {},
            "timestamp": 0.0,
            "parent_contexts": [],
        }
        uid = self.store.save_checkpoint(scope="agent", payload=payload)
        self.system.run_task("test", checkpoint_uid=uid)
        fog = self._agent("root_manager")
        self.assertEqual(len(fog._execution_calls), 1)

    def test_run_task_without_checkpoint_is_unchanged(self):
        self.system.run_task("fresh task", context={"key": "val"})
        fog = self._agent("root_manager")
        compiler = self._agent("compiler")
        self.assertEqual(len(fog._execution_calls), 1)
        self.assertIsNone(fog._execution_calls[0]["checkpoint_uid"])
        self.assertEqual(len(compiler._execution_calls), 0)

    # ── _save_agent_checkpoint includes parent_contexts ────────────────────

    def test_save_checkpoint_includes_parent_contexts_when_parent_set(self):
        """If _parent_agent is set, parent_contexts must be populated."""
        from IkaCore.checkpoint import CheckpointStore as CS
        db = str(self.tmp / "chain.db")
        store = CS(db)

        class _FakeLogger:
            def write_line(self, *a): pass
            def log_action(self, *a): pass
            @property
            def level(self): return 1

        # Build a tiny agent with a parent
        class _MinAgent:
            name = "Child"
            _parent_hierarchy = ["Parent", "Child"]
            message_history = _make_mh("Child")
            maxsteps = 10
            memory_access = {}
            checkpoint_store = store
            logger = _FakeLogger()

        class _ParentAgent:
            name = "Parent"
            message_history = _make_mh("Parent")
            maxsteps = 20
            _parent_agent = None  # root has no parent

        child = _MinAgent()
        parent = _ParentAgent()
        child._parent_agent = parent  # type: ignore

        from IkaCore.agent_helpers import AgentHelpersMixin
        # Call the mixin method directly on our fake agent
        uid = AgentHelpersMixin._save_agent_checkpoint(child, remaining_steps=5, last_content="hi")
        payload = store.load_checkpoint(uid)
        self.assertIsNotNone(payload)
        pcs = payload.get("parent_contexts", [])
        self.assertEqual(len(pcs), 1, "Should have exactly one parent context (Parent)")
        self.assertEqual(pcs[0]["agent_name"], "Parent")
        self.assertEqual(pcs[0]["message_history"]["first_input"]["message"], "Parent-first")

    def test_save_checkpoint_no_parent_gives_empty_contexts(self):
        from IkaCore.checkpoint import CheckpointStore as CS
        db = str(self.tmp / "solo.db")
        store = CS(db)

        class _FakeLogger:
            def write_line(self, *a): pass
            def log_action(self, *a): pass
            @property
            def level(self): return 1

        class _SoloAgent:
            name = "Solo"
            _parent_hierarchy = ["Solo"]
            message_history = _make_mh("Solo")
            maxsteps = 10
            memory_access = {}
            checkpoint_store = store
            logger = _FakeLogger()
            _parent_agent = None

        from IkaCore.agent_helpers import AgentHelpersMixin
        uid = AgentHelpersMixin._save_agent_checkpoint(_SoloAgent(), remaining_steps=3, last_content="x")
        payload = store.load_checkpoint(uid)
        self.assertEqual(payload.get("parent_contexts", []), [])


# ---------------------------------------------------------------------------
# Helpers for new-scope tests
# ---------------------------------------------------------------------------

def _make_fake_logger():
    class _FL:
        def write_line(self, *a): pass
        def log_action(self, *a): pass
        @property
        def level(self): return 1
    return _FL()


def _make_mixin_agent(name, store, parent=None, hierarchy=None, mh=None, maxsteps=10, has_stages=False):
    """Build a minimal object that satisfies AgentHelpersMixin's interface."""
    class _A:
        pass
    a = _A()
    a.name = name
    a._parent_hierarchy = hierarchy or [name]
    a.message_history = mh or _make_mh(name)
    a.maxsteps = maxsteps
    a.memory_access = {}
    a.checkpoint_store = store
    a.logger = _make_fake_logger()
    a._parent_agent = parent
    a.Stages = [] if not has_stages else [type("S", (), {"name": "S0", "checkpoint": False})()]
    return a


# ---------------------------------------------------------------------------
# agent_entry checkpoint tests
# ---------------------------------------------------------------------------

class TestAgentEntryCheckpoint(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = CheckpointStore(str(self.tmp / "test.db"))

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def test_agent_entry_scope(self):
        from IkaCore.agent_helpers import AgentHelpersMixin
        agent = _make_mixin_agent("Child", self.store)
        uid = AgentHelpersMixin._save_agent_entry_checkpoint(agent)
        payload = self.store.load_checkpoint(uid)
        self.assertEqual(payload["scope"], "agent_entry")
        self.assertEqual(payload["agent_name"], "Child")

    def test_agent_entry_remaining_steps_equals_maxsteps(self):
        from IkaCore.agent_helpers import AgentHelpersMixin
        agent = _make_mixin_agent("Child", self.store, maxsteps=42)
        uid = AgentHelpersMixin._save_agent_entry_checkpoint(agent)
        payload = self.store.load_checkpoint(uid)
        self.assertEqual(payload["remaining_steps"], 42)
        self.assertEqual(payload["maxsteps"], 42)

    def test_agent_entry_captures_parent_contexts(self):
        from IkaCore.agent_helpers import AgentHelpersMixin
        parent = _make_mixin_agent("Parent", self.store, mh=_make_mh("P"))
        child = _make_mixin_agent("Child", self.store, parent=parent, hierarchy=["Parent", "Child"])
        uid = AgentHelpersMixin._save_agent_entry_checkpoint(child)
        payload = self.store.load_checkpoint(uid)
        pcs = payload.get("parent_contexts", [])
        self.assertEqual(len(pcs), 1)
        self.assertEqual(pcs[0]["agent_name"], "Parent")

    def test_agent_entry_no_parent_empty_contexts(self):
        from IkaCore.agent_helpers import AgentHelpersMixin
        agent = _make_mixin_agent("Solo", self.store)
        uid = AgentHelpersMixin._save_agent_entry_checkpoint(agent)
        payload = self.store.load_checkpoint(uid)
        self.assertEqual(payload.get("parent_contexts", []), [])

    def test_agent_entry_message_history_stored(self):
        from IkaCore.agent_helpers import AgentHelpersMixin
        mh = _make_mh("MyAgent")
        agent = _make_mixin_agent("MyAgent", self.store, mh=mh)
        uid = AgentHelpersMixin._save_agent_entry_checkpoint(agent)
        payload = self.store.load_checkpoint(uid)
        self.assertEqual(
            payload["message_history"]["first_input"]["message"], "MyAgent-first"
        )

    def test_agent_entry_no_store_returns_none(self):
        from IkaCore.agent_helpers import AgentHelpersMixin
        agent = _make_mixin_agent("X", store=None)
        result = AgentHelpersMixin._save_agent_entry_checkpoint(agent)
        self.assertIsNone(result)

    # ── routing: agent_entry resumes via same pre-prime logic ────────────────

    def test_routing_agent_entry_primes_target(self):
        """_find_agent_for_checkpoint handles agent_entry scope identically to agent scope."""
        system = _ConcreteAgent(model=None, api_key="key")
        fog = system.agents["root_manager"]
        for ag in system.agents.values():
            ag.checkpoint_store = self.store

        payload = {
            "scope": "agent_entry",
            "agent_name": "Compiler",
            "agent_chain": ["RootManager", "ProgramBuilder", "Compiler"],
            "message_history": _make_mh("Compiler"),
            "maxsteps": 100,
            "remaining_steps": 100,
            "last_content": "",
            "memory_access": {},
            "timestamp": 0.0,
            "parent_contexts": [
                {
                    "agent_name": "ProgramBuilder",
                    "message_history": _make_mh("ProgramBuilder"),
                    "maxsteps": 30,
                    "remaining_steps": 28,
                },
                {
                    "agent_name": "RootManager",
                    "message_history": _make_mh("RootManager"),
                    "maxsteps": 30,
                    "remaining_steps": 25,
                },
            ],
        }
        uid = self.store.save_checkpoint(scope="agent_entry", payload=payload)
        root = system._find_agent_for_checkpoint(uid, fog)
        self.assertEqual(root.name, "RootManager")
        self.assertIsNotNone(system.agents["compiler"]._resume_checkpoint)
        self.assertEqual(system.agents["compiler"]._resume_checkpoint["scope"], "agent_entry")
        self.assertIsNotNone(system.agents["program_builder"]._resume_checkpoint)
        self.assertIsNotNone(fog._resume_checkpoint)

    def test_agent_entry_parent_contexts_include_chat_messages(self):
        """Parent contexts in agent_entry checkpoint must include chat_messages."""
        from IkaCore.agent_helpers import AgentHelpersMixin
        parent = _make_mixin_agent("Parent", self.store, mh=_make_mh("P"))
        # Simulate parent being mid-run_simple() — has _current_chat_messages set
        parent._current_chat_messages = [
            {"role": "user", "content": "do task"},
            {"role": "assistant", "content": "calling subagent"},
        ]
        child = _make_mixin_agent("Child", self.store, parent=parent, hierarchy=["Parent", "Child"])
        uid = AgentHelpersMixin._save_agent_entry_checkpoint(child)
        payload = self.store.load_checkpoint(uid)
        pcs = payload.get("parent_contexts", [])
        self.assertEqual(len(pcs), 1)
        self.assertEqual(len(pcs[0]["chat_messages"]), 2)
        self.assertEqual(pcs[0]["chat_messages"][0]["role"], "user")

    def test_agent_entry_live_messages_preferred_over_current(self):
        """_live_messages (live reference) is preferred over _current_chat_messages in parent contexts."""
        from IkaCore.agent_helpers import AgentHelpersMixin
        parent = _make_mixin_agent("Parent", self.store, mh=_make_mh("P"))
        # _current_chat_messages is stale (only 1 message)
        parent._current_chat_messages = [{"role": "user", "content": "stale"}]
        # _live_messages is the up-to-date list (2 messages, as chat() would have appended)
        live = [
            {"role": "user", "content": "do task"},
            {"role": "assistant", "content": "round1 result"},
            {"role": "tool", "content": "tool result"},
        ]
        parent._live_messages = live  # reference to the live messages list
        child = _make_mixin_agent("Child", self.store, parent=parent, hierarchy=["Parent", "Child"])
        uid = AgentHelpersMixin._save_agent_entry_checkpoint(child)
        payload = self.store.load_checkpoint(uid)
        pcs = payload.get("parent_contexts", [])
        self.assertEqual(len(pcs), 1)
        # Should have 3 messages from _live_messages, not 1 from _current_chat_messages
        self.assertEqual(len(pcs[0]["chat_messages"]), 3)
        self.assertEqual(pcs[0]["chat_messages"][2]["role"], "tool")

    def test_synthetic_parent_checkpoint_includes_chat_messages(self):
        """_find_agent_for_checkpoint propagates chat_messages into synthetic checkpoints."""
        system = _ConcreteAgent(model=None, api_key="key")
        fog = system.agents["root_manager"]
        for ag in system.agents.values():
            ag.checkpoint_store = self.store

        saved_chat = [{"role": "user", "content": "step1"}, {"role": "assistant", "content": "ok"}]
        payload = {
            "scope": "agent_entry",
            "agent_name": "Compiler",
            "agent_chain": ["RootManager", "ProgramBuilder", "Compiler"],
            "message_history": _make_mh("Compiler"),
            "maxsteps": 100,
            "remaining_steps": 100,
            "last_content": "",
            "memory_access": {},
            "timestamp": 0.0,
            "parent_contexts": [
                {
                    "agent_name": "ProgramBuilder",
                    "message_history": _make_mh("ProgramBuilder"),
                    "maxsteps": 30,
                    "remaining_steps": 28,
                    "chat_messages": saved_chat,
                },
                {
                    "agent_name": "RootManager",
                    "message_history": _make_mh("RootManager"),
                    "maxsteps": 30,
                    "remaining_steps": 25,
                    "chat_messages": [{"role": "user", "content": "fog step"}],
                },
            ],
        }
        uid = self.store.save_checkpoint(scope="agent_entry", payload=payload)
        system._find_agent_for_checkpoint(uid, fog)
        pb_cp = system.agents["program_builder"]._resume_checkpoint
        self.assertIsNotNone(pb_cp)
        self.assertEqual(pb_cp.get("chat_messages"), saved_chat)
        fog_cp = fog._resume_checkpoint
        self.assertEqual(fog_cp.get("chat_messages"), [{"role": "user", "content": "fog step"}])

    def test_routing_agent_entry_returns_root(self):
        system = _ConcreteAgent(model=None, api_key="key")
        fog = system.agents["root_manager"]
        for ag in system.agents.values():
            ag.checkpoint_store = self.store

        payload = {
            "scope": "agent_entry",
            "agent_name": "ProgramBuilder",
            "agent_chain": ["RootManager", "ProgramBuilder"],
            "message_history": _make_mh("ProgramBuilder"),
            "maxsteps": 30,
            "remaining_steps": 30,
            "last_content": "",
            "memory_access": {},
            "timestamp": 0.0,
            "parent_contexts": [
                {
                    "agent_name": "RootManager",
                    "message_history": _make_mh("RootManager"),
                    "maxsteps": 30,
                    "remaining_steps": 28,
                }
            ],
        }
        uid = self.store.save_checkpoint(scope="agent_entry", payload=payload)
        root = system._find_agent_for_checkpoint(uid, fog)
        self.assertEqual(root.name, "RootManager")


# ---------------------------------------------------------------------------
# stage_entry checkpoint tests
# ---------------------------------------------------------------------------

class TestStageEntryCheckpoint(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = CheckpointStore(str(self.tmp / "test.db"))

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def _make_staged_agent(self, name="StageAgent", num_stages=3, parent=None):
        stage_cls = type("S", (), {"name": f"S{{i}}", "checkpoint": False})
        stages = [type("S", (), {"name": f"Stage{i}"})() for i in range(num_stages)]
        agent = _make_mixin_agent(name, self.store, parent=parent, has_stages=False)
        agent.Stages = stages
        return agent

    def test_stage_entry_scope(self):
        from IkaCore.agent_helpers import AgentHelpersMixin
        agent = self._make_staged_agent()
        uid = AgentHelpersMixin._save_stage_entry_checkpoint(agent, stage_index=1, remaining_steps=20)
        payload = self.store.load_checkpoint(uid)
        self.assertEqual(payload["scope"], "stage_entry")

    def test_stage_entry_stage_index(self):
        from IkaCore.agent_helpers import AgentHelpersMixin
        agent = self._make_staged_agent()
        uid = AgentHelpersMixin._save_stage_entry_checkpoint(agent, stage_index=2, remaining_steps=15)
        payload = self.store.load_checkpoint(uid)
        self.assertEqual(payload["stage_index"], 2)

    def test_stage_entry_last_content_empty(self):
        from IkaCore.agent_helpers import AgentHelpersMixin
        agent = self._make_staged_agent()
        uid = AgentHelpersMixin._save_stage_entry_checkpoint(agent, stage_index=0, remaining_steps=30)
        payload = self.store.load_checkpoint(uid)
        self.assertEqual(payload["last_content"], "")

    def test_stage_entry_captures_parent_contexts(self):
        from IkaCore.agent_helpers import AgentHelpersMixin
        parent = _make_mixin_agent("Parent", self.store, mh=_make_mh("Par"))
        agent = self._make_staged_agent(parent=parent)
        agent._parent_hierarchy = ["Parent", "StageAgent"]
        uid = AgentHelpersMixin._save_stage_entry_checkpoint(agent, stage_index=1, remaining_steps=20)
        payload = self.store.load_checkpoint(uid)
        pcs = payload.get("parent_contexts", [])
        self.assertEqual(len(pcs), 1)
        self.assertEqual(pcs[0]["agent_name"], "Parent")

    def test_stage_entry_out_of_range_returns_none(self):
        from IkaCore.agent_helpers import AgentHelpersMixin
        agent = self._make_staged_agent(num_stages=2)
        result = AgentHelpersMixin._save_stage_entry_checkpoint(agent, stage_index=5, remaining_steps=10)
        self.assertIsNone(result)

    def test_stage_entry_no_store_returns_none(self):
        from IkaCore.agent_helpers import AgentHelpersMixin
        agent = self._make_staged_agent()
        agent.checkpoint_store = None
        result = AgentHelpersMixin._save_stage_entry_checkpoint(agent, stage_index=0, remaining_steps=10)
        self.assertIsNone(result)

    # ── routing: stage_entry primes target and parents ───────────────────────

    def test_routing_stage_entry_primes_target_and_parents(self):
        """stage_entry checkpoint: target + parents all pre-primed, root returned."""
        system = _ConcreteAgent(model=None, api_key="key")
        fog = system.agents["root_manager"]
        for ag in system.agents.values():
            ag.checkpoint_store = self.store

        payload = {
            "scope": "stage_entry",
            "agent_name": "Compiler",
            "agent_chain": ["RootManager", "ProgramBuilder", "Compiler"],
            "stage_index": 2,
            "stage_name": "Stage2",
            "message_history": _make_mh("Compiler"),
            "maxsteps": 100,
            "remaining_steps": 80,
            "last_content": "",
            "memory_access": {},
            "timestamp": 0.0,
            "parent_contexts": [
                {
                    "agent_name": "ProgramBuilder",
                    "message_history": _make_mh("ProgramBuilder"),
                    "maxsteps": 30,
                    "remaining_steps": 20,
                },
                {
                    "agent_name": "RootManager",
                    "message_history": _make_mh("RootManager"),
                    "maxsteps": 30,
                    "remaining_steps": 18,
                },
            ],
        }
        uid = self.store.save_checkpoint(scope="stage_entry", payload=payload)
        root = system._find_agent_for_checkpoint(uid, fog)
        self.assertEqual(root.name, "RootManager")
        compiler = system.agents["compiler"]
        self.assertIsNotNone(compiler._resume_checkpoint)
        self.assertEqual(compiler._resume_checkpoint["scope"], "stage_entry")
        self.assertEqual(compiler._resume_checkpoint["stage_index"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
