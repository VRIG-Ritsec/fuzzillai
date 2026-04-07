#!/usr/bin/env python3
"""
Forward Server - Monitors PostgreSQL for crashes and coverage stagnation,
then dispatches EBG agents accordingly.

API endpoints:
  GET  /health           - liveness check
  GET  /status           - monitor state, recent detections, active agents
  GET  /fuzzers          - active fuzzer list from DB
  POST /trigger/crash    - {"fuzzer_id": <int>, "program_hash": "<hash>"}
  POST /trigger/plateau  - {"fuzzer_id": <int>}
"""

import sys
import os
import json
import time
import threading
import logging
import argparse
from datetime import datetime
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

# ── path setup ─────────────────────────────────────────────────────────────────
_agentic_root = Path(__file__).resolve().parents[1]
if str(_agentic_root) not in sys.path:
    sys.path.insert(0, str(_agentic_root))

import site
_root = Path(__file__).resolve().parents[3]
_venv_site = _root / ".venv" / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
if _venv_site.exists():
    site.addsitedir(str(_venv_site))

import psycopg2
import psycopg2.extras

from tools.EBG_tools._shared import (
    POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD,
)
from config_loader import get_openai_api_key, get_anthropic_api_key, get_deepseek_api_key, get_openrouter_api_key

# ── constants ──────────────────────────────────────────────────────────────────
STAGNATION_WINDOW_MINUTES = 30          # coverage unchanged for this long → plateau
POLL_INTERVAL_SECONDS     = 60          # how often to poll the DB
CRASH_LOOKBACK_MINUTES    = 10          # window to scan for new crashes
PLATEAU_COOLDOWN_SECONDS  = 7_200       # 2 h minimum between plateau triggers per fuzzer
COVERAGE_STAGNATION_DELTA = 0.01        # % change threshold considered "no progress"

logger = logging.getLogger("forward_server")

# ── DB helpers ─────────────────────────────────────────────────────────────────

def _connect() -> psycopg2.extensions.connection:
    return psycopg2.connect(
        host=POSTGRES_HOST, port=POSTGRES_PORT,
        dbname=POSTGRES_DB, user=POSTGRES_USER, password=POSTGRES_PASSWORD,
        connect_timeout=10,
    )


def _query(conn, sql: str, params=()) -> list[dict]:
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


# ── Monitor ────────────────────────────────────────────────────────────────────

class FuzzerMonitor:
    """
    Background thread that polls the DB and fires agents for crashes / plateau.
    """

    def __init__(self):
        self._lock = threading.Lock()

        # tracking state
        self._triggered_crash_hashes: set[str]        = set()
        self._plateau_last_trigger:   dict[int, float] = {}   # fuzzer_id → epoch

        # public status (safe to read without lock for display; written under lock)
        self.status: dict = {
            "running":          False,
            "last_poll":        None,
            "polls_total":      0,
            "crashes_detected": [],   # list of {fuzzer_id, program_hash, detected_at}
            "plateaus_detected":[],   # list of {fuzzer_id, detected_at}
            "active_agents":    [],   # list of {type, fuzzer_id, started_at, thread_id}
        }

    # ── DB checks ──────────────────────────────────────────────────────────────

    def _check_crashes(self, conn) -> list[dict]:
        """Return new crashes not yet dispatched."""
        rows = _query(conn, """
            SELECT DISTINCT p.fuzzer_id, e.program_hash
            FROM   execution e
            JOIN   program   p ON e.program_hash = p.program_hash
            WHERE  e.execution_outcome_id = 1
            AND    e.created_at > NOW() - INTERVAL '%s minutes'
        """ % CRASH_LOOKBACK_MINUTES)

        new_crashes = []
        for row in rows:
            if row["program_hash"] not in self._triggered_crash_hashes:
                new_crashes.append(row)
                self._triggered_crash_hashes.add(row["program_hash"])
        return new_crashes

    def _check_stagnation(self, conn) -> list[int]:
        """
        Return fuzzer_ids where max coverage over last STAGNATION_WINDOW_MINUTES
        is ≤ COVERAGE_STAGNATION_DELTA more than the previous equal-length window.
        """
        recent_rows = _query(conn, """
            SELECT p.fuzzer_id, MAX(e.coverage_total) AS max_cov
            FROM   execution e
            JOIN   program   p ON e.program_hash = p.program_hash
            WHERE  e.created_at > NOW() - INTERVAL '%s minutes'
            AND    e.coverage_total IS NOT NULL
            GROUP  BY p.fuzzer_id
        """ % STAGNATION_WINDOW_MINUTES)

        older_rows = _query(conn, """
            SELECT p.fuzzer_id, MAX(e.coverage_total) AS max_cov
            FROM   execution e
            JOIN   program   p ON e.program_hash = p.program_hash
            WHERE  e.created_at >  NOW() - INTERVAL '%s minutes'
            AND    e.created_at <= NOW() - INTERVAL '%s minutes'
            AND    e.coverage_total IS NOT NULL
            GROUP  BY p.fuzzer_id
        """ % (STAGNATION_WINDOW_MINUTES * 2, STAGNATION_WINDOW_MINUTES))

        recent = {r["fuzzer_id"]: float(r["max_cov"]) for r in recent_rows}
        older  = {r["fuzzer_id"]: float(r["max_cov"]) for r in older_rows}

        stagnating = []
        for fid, rcov in recent.items():
            if fid not in older:
                continue                          # not enough history yet
            delta = rcov - older[fid]
            if delta <= COVERAGE_STAGNATION_DELTA:
                now = time.time()
                last = self._plateau_last_trigger.get(fid, 0)
                if (now - last) >= PLATEAU_COOLDOWN_SECONDS:
                    stagnating.append(fid)
        return stagnating

    def _active_fuzzers(self, conn) -> list[dict]:
        return _query(conn, "SELECT fuzzer_id, status, last_activity FROM main WHERE status = 'active'")

    # ── agent dispatch ─────────────────────────────────────────────────────────

    def _dispatch_crash_agent(self, fuzzer_id: int, program_hash: str):
        """Spin up EBG_Crash in a daemon thread."""
        def _run():
            try:
                from agents.EBG_crash import EBG_Crash
                openai_key    = get_openai_api_key()
                anthropic_key = get_anthropic_api_key()
                os.environ["OPENAI_API_KEY"] = openai_key or ""

                label = f"crash-{program_hash[:12]}"
                logger.info("[CRASH] Launching EBG_Crash for fuzzer=%d hash=%s", fuzzer_id, program_hash)

                model = _make_model(openai_key)
                sys_obj = EBG_Crash(
                    model, api_key=openai_key, anthropic_api_key=anthropic_key,
                    crash_program_hash=program_hash,
                )
                sys_obj.start_system()
            except Exception as e:
                logger.error("[CRASH] Agent error: %s", e)
            finally:
                self._remove_active_agent(label)

        label = f"crash-{program_hash[:12]}"
        t = threading.Thread(target=_run, name=label, daemon=True)
        t.start()
        entry = {
            "type":      "crash",
            "fuzzer_id": fuzzer_id,
            "hash":      program_hash,
            "started_at": datetime.utcnow().isoformat(),
            "label":     label,
        }
        with self._lock:
            self.status["crashes_detected"].append({
                "fuzzer_id": fuzzer_id, "program_hash": program_hash,
                "detected_at": datetime.utcnow().isoformat(),
            })
            self.status["active_agents"].append(entry)
        logger.info("[CRASH] Thread started: %s", label)

    def _dispatch_plateau_agent(self, fuzzer_id: int):
        """Spin up EBG_Plateau in a daemon thread."""
        fuzzer_label = f"fuzzer-{fuzzer_id}"

        def _run():
            try:
                from agents.EBG_plateau import EBG_Plateau
                openai_key    = get_openai_api_key()
                anthropic_key = get_anthropic_api_key()
                os.environ["OPENAI_API_KEY"] = openai_key or ""

                logger.info("[PLATEAU] Launching EBG_Plateau for %s", fuzzer_label)
                model = _make_model(openai_key)
                sys_obj = EBG_Plateau(
                    model, api_key=openai_key, anthropic_api_key=anthropic_key,
                    fuzzer_id=fuzzer_label,
                )
                sys_obj.start_system()
            except Exception as e:
                logger.error("[PLATEAU] Agent error: %s", e)
            finally:
                self._remove_active_agent(fuzzer_label)

        t = threading.Thread(target=_run, name=fuzzer_label, daemon=True)
        t.start()
        entry = {
            "type":       "plateau",
            "fuzzer_id":  fuzzer_id,
            "started_at": datetime.utcnow().isoformat(),
            "label":      fuzzer_label,
        }
        with self._lock:
            self._plateau_last_trigger[fuzzer_id] = time.time()
            self.status["plateaus_detected"].append({
                "fuzzer_id": fuzzer_id,
                "detected_at": datetime.utcnow().isoformat(),
            })
            self.status["active_agents"].append(entry)
        logger.info("[PLATEAU] Thread started: %s", fuzzer_label)

    def _remove_active_agent(self, label: str):
        with self._lock:
            self.status["active_agents"] = [
                a for a in self.status["active_agents"] if a.get("label") != label
            ]

    # ── public API ─────────────────────────────────────────────────────────────

    def trigger_crash(self, fuzzer_id: int, program_hash: str):
        """Manually trigger crash agent (via HTTP POST)."""
        self._triggered_crash_hashes.add(program_hash)   # prevent auto re-trigger
        self._dispatch_crash_agent(fuzzer_id, program_hash)

    def trigger_plateau(self, fuzzer_id: int):
        """Manually trigger plateau agent (via HTTP POST)."""
        self._plateau_last_trigger[fuzzer_id] = time.time()
        self._dispatch_plateau_agent(fuzzer_id)

    def get_fuzzers(self) -> list[dict]:
        try:
            conn = _connect()
            try:
                return self._active_fuzzers(conn)
            finally:
                conn.close()
        except Exception as e:
            logger.error("get_fuzzers error: %s", e)
            return []

    # ── polling loop ───────────────────────────────────────────────────────────

    def _poll_once(self):
        try:
            conn = _connect()
            try:
                crashes    = self._check_crashes(conn)
                stagnating = self._check_stagnation(conn)
            finally:
                conn.close()

            for crash in crashes:
                self._dispatch_crash_agent(crash["fuzzer_id"], crash["program_hash"])

            for fid in stagnating:
                self._dispatch_plateau_agent(fid)

            with self._lock:
                self.status["last_poll"]   = datetime.utcnow().isoformat()
                self.status["polls_total"] += 1

            if crashes or stagnating:
                logger.info("Poll: %d new crashes, %d stagnating fuzzers", len(crashes), len(stagnating))

        except Exception as e:
            logger.error("Poll error: %s", e)

    def run_forever(self):
        with self._lock:
            self.status["running"] = True
        logger.info("Monitor started (poll_interval=%ds stagnation_window=%dmin)",
                    POLL_INTERVAL_SECONDS, STAGNATION_WINDOW_MINUTES)
        while self.status["running"]:
            self._poll_once()
            time.sleep(POLL_INTERVAL_SECONDS)

    def stop(self):
        with self._lock:
            self.status["running"] = False


# ── HTTP handler ───────────────────────────────────────────────────────────────

def _json_response(handler, code: int, data):
    body = json.dumps(data, default=str).encode()
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def make_handler(monitor: FuzzerMonitor):

    class Handler(BaseHTTPRequestHandler):
        log_message = lambda self, fmt, *args: None   # silence access log

        def do_GET(self):
            parsed = urlparse(self.path)
            path   = parsed.path.rstrip("/")

            if path == "/health":
                _json_response(self, 200, {"ok": True})

            elif path == "/status":
                with monitor._lock:
                    snap = dict(monitor.status)
                _json_response(self, 200, snap)

            elif path == "/fuzzers":
                fuzzers = monitor.get_fuzzers()
                _json_response(self, 200, {"fuzzers": fuzzers})

            else:
                _json_response(self, 404, {"error": "not found"})

        def do_POST(self):
            parsed = urlparse(self.path)
            path   = parsed.path.rstrip("/")

            length = int(self.headers.get("Content-Length", 0))
            body   = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                _json_response(self, 400, {"error": "invalid JSON"})
                return

            if path == "/trigger/crash":
                fid  = payload.get("fuzzer_id")
                phsh = payload.get("program_hash")
                if fid is None or not phsh:
                    _json_response(self, 400, {"error": "fuzzer_id and program_hash required"})
                    return
                monitor.trigger_crash(int(fid), str(phsh))
                _json_response(self, 202, {"status": "crash agent dispatched", "fuzzer_id": fid, "program_hash": phsh})

            elif path == "/trigger/plateau":
                fid = payload.get("fuzzer_id")
                if fid is None:
                    _json_response(self, 400, {"error": "fuzzer_id required"})
                    return
                monitor.trigger_plateau(int(fid))
                _json_response(self, 202, {"status": "plateau agent dispatched", "fuzzer_id": fid})

            else:
                _json_response(self, 404, {"error": "not found"})

    return Handler


# ── helpers ────────────────────────────────────────────────────────────────────

def _make_model(openai_key: str):
    model_id = os.environ.get("EBG_MANAGER_MODEL", "gpt-5.4")
    return type("_Model", (), {"model_id": model_id, "api_key": openai_key})()


def _setup_logging(level: str):
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=getattr(logging, level.upper(), logging.INFO),
    )


# ── entry point ────────────────────────────────────────────────────────────────

def main():
    global POLL_INTERVAL_SECONDS, STAGNATION_WINDOW_MINUTES  # noqa: PLW0603
    parser = argparse.ArgumentParser(description="Forward Server - fuzzer monitoring + agent dispatch")
    parser.add_argument("--host",          default="0.0.0.0",  help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--port",          type=int, default=8765, help="HTTP port (default: 8765)")
    parser.add_argument("--poll-interval", type=int, default=POLL_INTERVAL_SECONDS,
                        help=f"DB poll interval in seconds (default: {POLL_INTERVAL_SECONDS})")
    parser.add_argument("--stagnation-window", type=int, default=STAGNATION_WINDOW_MINUTES,
                        help=f"Stagnation detection window in minutes (default: {STAGNATION_WINDOW_MINUTES})")
    parser.add_argument("--log-level",     default="INFO", help="Logging level (default: INFO)")
    parser.add_argument("--no-monitor",    action="store_true",
                        help="Start only the API server without the background DB monitor")
    args = parser.parse_args()

    _setup_logging(args.log_level)

    POLL_INTERVAL_SECONDS     = args.poll_interval
    STAGNATION_WINDOW_MINUTES = args.stagnation_window

    monitor = FuzzerMonitor()

    if not args.no_monitor:
        t = threading.Thread(target=monitor.run_forever, name="db-monitor", daemon=True)
        t.start()

    server = HTTPServer((args.host, args.port), make_handler(monitor))
    logger.info("Forward server listening on %s:%d", args.host, args.port)
    logger.info("Endpoints: GET /health /status /fuzzers  |  POST /trigger/crash /trigger/plateau")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down")
        monitor.stop()
        server.server_close()


if __name__ == "__main__":
    main()
