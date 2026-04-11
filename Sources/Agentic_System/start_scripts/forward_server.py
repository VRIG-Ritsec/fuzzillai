#!/usr/bin/env python3
"""
Forward Server - monitors PostgreSQL for crashes and coverage stagnation,
then dispatches EBG agents accordingly.

This module no longer serves HTTP. The dashboard and control API live in
start_scripts/web-ui/server.py.
"""

import sys
import os
import time
import threading
import logging
import argparse
import subprocess
import re
from datetime import datetime, timezone
from pathlib import Path

_agentic_root = Path(__file__).resolve().parents[1]
if str(_agentic_root) not in sys.path:
    sys.path.insert(0, str(_agentic_root))

from venv_site_packages import add_fuzzillai_repo_venv_site_packages, preferred_python_executable

add_fuzzillai_repo_venv_site_packages()

from agent_logging import get_system_log_dir

import psycopg2
import psycopg2.extras

from tools.EBG_tools._shared import (
    POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD,
)
from config_loader import apply_runtime_paths, get_openai_api_key, get_anthropic_api_key

apply_runtime_paths()

# -- constants -----------------------------------------------------------------
STAGNATION_WINDOW_MINUTES = 30
POLL_INTERVAL_SECONDS = 60
CRASH_LOOKBACK_MINUTES = 10
PLATEAU_COOLDOWN_SECONDS = 7_200
COVERAGE_STAGNATION_DELTA = 0.01

logger = logging.getLogger("forward_server")
_service_log_dir = get_system_log_dir("services")
_agent_spawn_log_dir = _service_log_dir / "agent_spawns"
_agent_spawn_log_dir.mkdir(parents=True, exist_ok=True)
_AGENT_PYTHON = preferred_python_executable()


def _safe_agent_log_stem(label: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", label).strip("_")
    return cleaned[:120] if cleaned else "agent"


# -- DB helpers ----------------------------------------------------------------

def _connect() -> psycopg2.extensions.connection:
    return psycopg2.connect(
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        dbname=POSTGRES_DB,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
        connect_timeout=10,
    )


def _query(conn, sql: str, params=()) -> list[dict]:
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


def _query_one(conn, sql: str, params=()) -> dict:
    rows = _query(conn, sql, params)
    return rows[0] if rows else {}


# -- monitor -------------------------------------------------------------------

class FuzzerMonitor:
    """
    Background thread that polls the DB and fires agents for crashes / plateau.
    """

    def __init__(self, auto_dispatch_agents: bool = True):
        logger.info("Agent subprocess interpreter: %s", _AGENT_PYTHON)
        self._auto_dispatch_agents = auto_dispatch_agents
        self._lock = threading.Lock()
        self._triggered_crash_hashes: set[str] = set()
        self._plateau_last_trigger: dict[int, float] = {}
        self._active_processes: dict[str, subprocess.Popen] = {}
        self.status: dict = {
            "running": False,
            "auto_dispatch_agents": auto_dispatch_agents,
            "last_poll": None,
            "polls_total": 0,
            "crashes_detected": [],
            "plateaus_detected": [],
            "active_agents": [],
        }

    def _check_crashes(self, conn) -> list[dict]:
        rows = _query(
            conn,
            """
            SELECT DISTINCT p.fuzzer_id, e.program_hash
            FROM execution e
            JOIN program p ON e.program_hash = p.program_hash
            WHERE e.execution_outcome_id = 1
            AND e.created_at > NOW() - INTERVAL '%s minutes'
            """ % CRASH_LOOKBACK_MINUTES,
        )

        new_crashes = []
        for row in rows:
            if row["program_hash"] not in self._triggered_crash_hashes:
                new_crashes.append(row)
                self._triggered_crash_hashes.add(row["program_hash"])
        return new_crashes

    def _check_stagnation(self, conn) -> list[int]:
        recent_rows = _query(
            conn,
            """
            SELECT p.fuzzer_id, MAX(e.coverage_total) AS max_cov
            FROM execution e
            JOIN program p ON e.program_hash = p.program_hash
            WHERE e.created_at > NOW() - INTERVAL '%s minutes'
            AND e.coverage_total IS NOT NULL
            GROUP BY p.fuzzer_id
            """ % STAGNATION_WINDOW_MINUTES,
        )

        older_rows = _query(
            conn,
            """
            SELECT p.fuzzer_id, MAX(e.coverage_total) AS max_cov
            FROM execution e
            JOIN program p ON e.program_hash = p.program_hash
            WHERE e.created_at > NOW() - INTERVAL '%s minutes'
            AND e.created_at <= NOW() - INTERVAL '%s minutes'
            AND e.coverage_total IS NOT NULL
            GROUP BY p.fuzzer_id
            """ % (STAGNATION_WINDOW_MINUTES * 2, STAGNATION_WINDOW_MINUTES),
        )

        recent = {r["fuzzer_id"]: float(r["max_cov"]) for r in recent_rows}
        older = {r["fuzzer_id"]: float(r["max_cov"]) for r in older_rows}

        stagnating = []
        for fuzzer_id, recent_cov in recent.items():
            if fuzzer_id not in older:
                continue
            delta = recent_cov - older[fuzzer_id]
            if delta <= COVERAGE_STAGNATION_DELTA:
                now = time.time()
                last = self._plateau_last_trigger.get(fuzzer_id, 0)
                if (now - last) >= PLATEAU_COOLDOWN_SECONDS:
                    stagnating.append(fuzzer_id)
        return stagnating

    def _active_fuzzers(self, conn) -> list[dict]:
        return _query(conn, "SELECT fuzzer_id, status, last_activity FROM main WHERE status = 'active'")

    def _spawn_agent_process(self, label: str, command: list[str]) -> tuple[subprocess.Popen, Path]:
        env = os.environ.copy()
        apply_runtime_paths(env)
        env["OPENAI_API_KEY"] = get_openai_api_key() or env.get("OPENAI_API_KEY", "")
        anthropic_key = get_anthropic_api_key()
        if anthropic_key:
            env["ANTHROPIC_API_KEY"] = anthropic_key

        log_path = _agent_spawn_log_dir / f"{_safe_agent_log_stem(label)}_{int(time.time())}.log"
        log_file = open(log_path, "a", encoding="utf-8", buffering=1)
        try:
            header = f"# agent={label} started={datetime.now(timezone.utc).isoformat()}\n# cmd={' '.join(command)}\n"
            log_file.write(header)
            log_file.flush()
            process = subprocess.Popen(
                command,
                cwd=str(_agentic_root),
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        finally:
            log_file.close()
        logger.info("Agent subprocess log: %s", log_path)
        with self._lock:
            self._active_processes[label] = process
        return process, log_path

    def _dispatch_crash_agent(self, fuzzer_id: int, program_hash: str):
        label = f"crash-{program_hash[:12]}"
        command = [
            _AGENT_PYTHON,
            str(_agentic_root / "start_scripts" / "ethiopian_boiled_egg.py"),
            "--mode",
            "Crash",
            "--crash-hash",
            program_hash,
        ]
        process, spawn_log = self._spawn_agent_process(label, command)
        entry = {
            "type": "crash",
            "fuzzer_id": fuzzer_id,
            "hash": program_hash,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "label": label,
            "pid": process.pid,
            "spawn_log": str(spawn_log),
        }
        with self._lock:
            self.status["crashes_detected"].append(
                {
                    "fuzzer_id": fuzzer_id,
                    "program_hash": program_hash,
                    "detected_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            self.status["active_agents"].append(entry)
        logger.info("[CRASH] Subprocess started: %s pid=%s", label, process.pid)

    def _dispatch_plateau_agent(self, fuzzer_id: int):
        fuzzer_label = f"fuzzer-{fuzzer_id}"
        command = [
            _AGENT_PYTHON,
            str(_agentic_root / "start_scripts" / "ethiopian_boiled_egg.py"),
            "--mode",
            "Plateau",
            "--fuzzer-id",
            fuzzer_label,
        ]
        process, spawn_log = self._spawn_agent_process(fuzzer_label, command)
        entry = {
            "type": "plateau",
            "fuzzer_id": fuzzer_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "label": fuzzer_label,
            "pid": process.pid,
            "spawn_log": str(spawn_log),
        }
        with self._lock:
            self._plateau_last_trigger[fuzzer_id] = time.time()
            self.status["plateaus_detected"].append(
                {
                    "fuzzer_id": fuzzer_id,
                    "detected_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            self.status["active_agents"].append(entry)
        logger.info("[PLATEAU] Subprocess started: %s pid=%s", fuzzer_label, process.pid)

    def _remove_active_agent(self, label: str):
        with self._lock:
            self._active_processes.pop(label, None)
            self.status["active_agents"] = [
                agent for agent in self.status["active_agents"] if agent.get("label") != label
            ]

    def _reap_finished_agents(self):
        finished: list[tuple[str, int]] = []
        with self._lock:
            items = list(self._active_processes.items())
        for label, process in items:
            returncode = process.poll()
            if returncode is not None:
                finished.append((label, returncode))
        for label, returncode in finished:
            logger.info("Agent process finished: %s exit=%s", label, returncode)
            self._remove_active_agent(label)

    def trigger_crash(self, fuzzer_id: int, program_hash: str):
        self._triggered_crash_hashes.add(program_hash)
        self._dispatch_crash_agent(fuzzer_id, program_hash)

    def trigger_plateau(self, fuzzer_id: int):
        self._plateau_last_trigger[fuzzer_id] = time.time()
        self._dispatch_plateau_agent(fuzzer_id)

    def get_fuzzers(self) -> list[dict]:
        try:
            conn = _connect()
            try:
                return self._active_fuzzers(conn)
            finally:
                conn.close()
        except Exception as exc:
            logger.error("get_fuzzers error: %s", exc)
            return []

    def _poll_once(self):
        try:
            self._reap_finished_agents()
            if self._auto_dispatch_agents:
                conn = _connect()
                try:
                    crashes = self._check_crashes(conn)
                    stagnating = self._check_stagnation(conn)
                finally:
                    conn.close()

                for crash in crashes:
                    self._dispatch_crash_agent(crash["fuzzer_id"], crash["program_hash"])

                for fuzzer_id in stagnating:
                    self._dispatch_plateau_agent(fuzzer_id)

                if crashes or stagnating:
                    logger.info("Poll: %d new crashes, %d stagnating fuzzers", len(crashes), len(stagnating))

            with self._lock:
                self.status["last_poll"] = datetime.now(timezone.utc).isoformat()
                self.status["polls_total"] += 1

        except Exception as exc:
            logger.error("Poll error: %s", exc)

    def run_forever(self):
        with self._lock:
            self.status["running"] = True
        logger.info(
            "Monitor started (poll_interval=%ds stagnation_window=%dmin auto_dispatch=%s)",
            POLL_INTERVAL_SECONDS,
            STAGNATION_WINDOW_MINUTES,
            self._auto_dispatch_agents,
        )
        while self.status["running"]:
            self._poll_once()
            time.sleep(POLL_INTERVAL_SECONDS)

    def stop(self):
        with self._lock:
            self.status["running"] = False


# -- helpers -------------------------------------------------------------------

def _make_model(openai_key: str):
    model_id = os.environ.get("EBG_MANAGER_MODEL", "gpt-5.4")
    return type("_Model", (), {"model_id": model_id, "api_key": openai_key})()


def _setup_logging(level: str, stem: str = "forward_server"):
    log_path = _service_log_dir / f"{stem}.log"
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    root_logger.addHandler(file_handler)
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.info("Writing service logs to %s", log_path)


# -- entry point ----------------------------------------------------------------

def main():
    global POLL_INTERVAL_SECONDS, STAGNATION_WINDOW_MINUTES  # noqa: PLW0603

    parser = argparse.ArgumentParser(description="Forward Server - fuzzer monitoring + agent dispatch")
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=POLL_INTERVAL_SECONDS,
        help=f"DB poll interval in seconds (default: {POLL_INTERVAL_SECONDS})",
    )
    parser.add_argument(
        "--stagnation-window",
        type=int,
        default=STAGNATION_WINDOW_MINUTES,
        help=f"Stagnation detection window in minutes (default: {STAGNATION_WINDOW_MINUTES})",
    )
    parser.add_argument("--log-level", default="INFO", help="Logging level (default: INFO)")
    parser.add_argument(
        "--no-auto-agents",
        action="store_true",
        help="Poll the database but never automatically spawn crash or plateau agents",
    )
    args = parser.parse_args()

    _setup_logging(args.log_level)
    POLL_INTERVAL_SECONDS = args.poll_interval
    STAGNATION_WINDOW_MINUTES = args.stagnation_window

    monitor = FuzzerMonitor(auto_dispatch_agents=not args.no_auto_agents)
    try:
        monitor.run_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down")
        monitor.stop()


if __name__ == "__main__":
    main()
