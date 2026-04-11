#!/usr/bin/env python3
"""
Web UI server for monitoring fuzzers, browsing AI logs, and manually triggering
plateau/crash agents.
"""

import sys
import json
import re
import base64
import zipfile
import threading
import logging
import argparse
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

_web_ui_dir = Path(__file__).resolve().parent
_start_scripts_dir = _web_ui_dir.parent
_agentic_root = _start_scripts_dir.parent

for path in (_agentic_root, _start_scripts_dir):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from venv_site_packages import add_fuzzillai_repo_venv_site_packages

add_fuzzillai_repo_venv_site_packages()

import forward_server as forward_core

_runtime_data_dir = _agentic_root / "runtime_data"
_log_archive_dir = _runtime_data_dir / "log_archives"
_log_archive_dir.mkdir(parents=True, exist_ok=True)
_dashboard_template = (_web_ui_dir / "dashboard.html").read_text(encoding="utf-8")
_logs_template = (_web_ui_dir / "logs.html").read_text(encoding="utf-8")
_corpus_template = (_web_ui_dir / "corpus.html").read_text(encoding="utf-8")
_assets_dir = _web_ui_dir / "assets"

# Tiny template partial — shared nav rendered once per page via str.replace.
# Order here is the order links appear in the header of every page.
_NAV_ITEMS = (
    ("dashboard", "/", "Dashboard"),
    ("logs", "/logs", "AI logs"),
    ("corpus", "/corpus", "Corpus"),
)


def _render_nav(active: str) -> str:
    parts = []
    for key, href, label in _NAV_ITEMS:
        current = ' aria-current="page"' if key == active else ""
        parts.append(f'<a class="nav-link" href="{href}"{current}>{label}</a>')
    return "".join(parts)


def _render_template(template: str, active_nav: str) -> str:
    return template.replace("{{NAV}}", _render_nav(active_nav))

logger = logging.getLogger("web_ui_server")


def _tail_text(text: str, lines: int) -> str:
    if lines <= 0:
        return text
    dq: deque[str] = deque(maxlen=max(1, lines))
    for line in text.splitlines():
        dq.append(line)
    return "\n".join(dq)


def _is_log_like(path: Path) -> bool:
    return path.suffix.lower() in {".txt", ".log", ".json", ".cfg"}


def _encode_log_ref(kind: str, container: str, entry: str = "") -> str:
    payload = {"kind": kind, "container": container, "entry": entry}
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_log_ref(ref: str) -> dict:
    raw = base64.urlsafe_b64decode(ref.encode("ascii"))
    return json.loads(raw.decode("utf-8"))


def _log_path_sort_key(path: Path) -> tuple[str, int, str]:
    stem = path.stem
    m = re.search(r"(\d+)$", stem)
    idx = int(m.group(1)) if m else 0
    return (path.parent.name, -idx, stem.lower())


def _live_log_files() -> list[Path]:
    files: list[Path] = []
    agent_log_root = _runtime_data_dir / "agent-logs"
    if agent_log_root.exists():
        allowed_dirs = {
            agent_log_root / "ebg_crash_logs",
            agent_log_root / "ebg_plateau_logs",
            agent_log_root / "fog_logs",
        }
        for path in sorted(agent_log_root.rglob("*")):
            if not path.is_file() or not _is_log_like(path):
                continue
            if path.parent not in allowed_dirs:
                continue
            if path.suffix.lower() != ".log":
                continue
            files.append(path)
    deduped: list[Path] = []
    seen: set[str] = set()
    for path in files:
        resolved = str(path.resolve())
        if resolved in seen:
            continue
        seen.add(resolved)
        deduped.append(path)
    deduped.sort(key=_log_path_sort_key)
    return deduped


def _list_log_sources() -> list[dict]:
    entries: list[dict] = []

    for path in _live_log_files():
        stat = path.stat()
        entries.append(
            {
                "id": _encode_log_ref("file", str(path.resolve())),
                "kind": "file",
                "label": path.name,
                "container": str(path.resolve()),
                "entry": "",
                "size": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            }
        )

    return entries


def _read_log_source(ref: str, lines: int = 300) -> dict:
    payload = _decode_log_ref(ref)
    kind = payload.get("kind")
    container = payload.get("container", "")
    entry = payload.get("entry", "")

    if kind == "file":
        path = Path(container)
        text = path.read_text(encoding="utf-8", errors="ignore")
        label = path.name
        return {"label": label, "kind": "file", "content": _tail_text(text, lines)}

    if kind == "zip":
        archive = Path(container)
        with zipfile.ZipFile(archive) as zf:
            data = zf.read(entry).decode("utf-8", errors="ignore")
        return {"label": f"{archive.name}: {entry}", "kind": "zip", "content": _tail_text(data, lines)}

    raise ValueError(f"Unknown log source kind: {kind}")


_PROGRAM_HASH_RE = re.compile(r"^[a-fA-F0-9]{64}$")


def _corpus_program_list(fuzzer_id: int | None, limit: int, offset: int) -> list[dict]:
    limit = max(1, min(int(limit), 200))
    offset = max(0, min(int(offset), 50_000))
    conn = forward_core._connect()
    try:
        if fuzzer_id is not None:
            return forward_core._query(
                conn,
                """
                SELECT
                    program_hash,
                    fuzzer_id,
                    inserted_at,
                    created_at,
                    source_mutators,
                    contributors,
                    parent_program_hash
                FROM program
                WHERE fuzzer_id = %s
                ORDER BY inserted_at DESC
                LIMIT %s OFFSET %s
                """,
                (fuzzer_id, limit, offset),
            )
        return forward_core._query(
            conn,
            """
            SELECT
                program_hash,
                fuzzer_id,
                inserted_at,
                created_at,
                source_mutators,
                contributors,
                parent_program_hash
            FROM program
            ORDER BY inserted_at DESC
            LIMIT %s OFFSET %s
            """,
            (limit, offset),
        )
    finally:
        conn.close()


def _corpus_program_detail(program_hash: str) -> dict:
    from tools.EBG_tools.db import _lift_program_bytes_to_js

    if not _PROGRAM_HASH_RE.match(program_hash):
        return {"error": "Invalid program_hash"}

    conn = forward_core._connect()
    try:
        row = forward_core._query_one(
            conn,
            """
            SELECT
                program_hash,
                fuzzer_id,
                inserted_at,
                created_at,
                source_mutators,
                contributors,
                parent_program_hash,
                program_base64
            FROM program
            WHERE program_hash = %s
            """,
            (program_hash,),
        )
    finally:
        conn.close()

    if not row or "program_base64" not in row:
        return {"error": "Program not found"}

    try:
        decoded = base64.b64decode(row["program_base64"])
    except Exception as exc:
        return {"error": f"Invalid base64 data: {exc}"}

    js = _lift_program_bytes_to_js(decoded)
    if isinstance(js, str) and js.startswith("Error"):
        return {"error": js}

    out: dict = {}
    for key, value in row.items():
        if key == "program_base64":
            continue
        out[key] = value
    out["javascript_code"] = js
    return out


def _fuzzer_rows(conn) -> list[dict]:
    # Live aggregates from base tables (not fuzzer_dashboard MV) so the UI stays correct
    # when materialized views are stale or refresh_all_stats has not run yet.
    return forward_core._query(
        conn,
        """
        SELECT
            m.fuzzer_id,
            m.status,
            m.created_at AS fuzzer_started,
            m.last_activity,
            stats.total_programs,
            stats.total_executions,
            stats.executions_last_hour,
            stats.execs_per_second,
            stats.total_crashes,
            stats.crashes_last_hour,
            stats.new_edges_found,
            stats.max_coverage,
            stats.avg_coverage,
            stats.max_edges_found,
            COALESCE(gq.queued_programs, 0) AS queued_programs,
            gq.oldest_queued_at,
            gq.newest_queued_at
        FROM main m
        LEFT JOIN (
            SELECT
                target_fuzzer_id,
                COUNT(*) AS queued_programs,
                MIN(created_at) AS oldest_queued_at,
                MAX(created_at) AS newest_queued_at
            FROM generated_program_queue
            GROUP BY target_fuzzer_id
        ) gq ON gq.target_fuzzer_id = m.fuzzer_id
        LEFT JOIN LATERAL (
            SELECT
                COALESCE(COUNT(DISTINCT p.program_hash), 0)::bigint AS total_programs,
                COALESCE(COUNT(e.execution_id), 0)::bigint AS total_executions,
                COALESCE(
                    COUNT(e.execution_id) FILTER (WHERE e.created_at > NOW() - INTERVAL '1 hour'),
                    0
                )::bigint AS executions_last_hour,
                ROUND(
                    COALESCE(
                        COUNT(e.execution_id) FILTER (WHERE e.created_at > NOW() - INTERVAL '1 hour'),
                        0
                    )::numeric / 3600.0,
                    2
                ) AS execs_per_second,
                COALESCE(COUNT(e.execution_id) FILTER (WHERE e.execution_outcome_id = 1), 0)::bigint
                    AS total_crashes,
                COALESCE(
                    COUNT(e.execution_id) FILTER (
                        WHERE e.execution_outcome_id = 1
                        AND e.created_at > NOW() - INTERVAL '1 hour'
                    ),
                    0
                )::bigint AS crashes_last_hour,
                COALESCE(COUNT(e.execution_id) FILTER (WHERE e.is_new_edge = TRUE), 0)::bigint
                    AS new_edges_found,
                COALESCE(MAX(e.coverage_total), 0::numeric) AS max_coverage,
                COALESCE(
                    AVG(e.coverage_total) FILTER (WHERE e.coverage_total IS NOT NULL),
                    0::numeric
                ) AS avg_coverage,
                COALESCE(MAX(e.edges_found), 0)::bigint AS max_edges_found
            FROM program p
            LEFT JOIN execution e ON e.program_hash = p.program_hash
            WHERE p.fuzzer_id = m.fuzzer_id
        ) stats ON true
        ORDER BY m.fuzzer_id ASC
        """,
    )


def _overview_payload(monitor: forward_core.FuzzerMonitor) -> dict:
    with monitor._lock:
        monitor_status = dict(monitor.status)

    try:
        conn = forward_core._connect()
        try:
            fuzzers = _fuzzer_rows(conn)
            summary = forward_core._query_one(
                conn,
                """
                SELECT
                    (SELECT COUNT(*) FROM main) AS total_fuzzers,
                    (SELECT COUNT(*) FROM main WHERE status = 'active') AS active_fuzzers,
                    (SELECT COUNT(*) FROM generated_program_queue) AS queued_programs,
                    (SELECT COUNT(DISTINCT target_fuzzer_id) FROM generated_program_queue) AS queued_fuzzers,
                    (SELECT COUNT(*) FROM execution WHERE execution_outcome_id = 1 AND created_at > NOW() - INTERVAL '24 hours') AS crashes_last_24h,
                    (SELECT COUNT(*) FROM execution WHERE is_new_edge = TRUE AND created_at > NOW() - INTERVAL '24 hours') AS new_edges_last_24h,
                    (SELECT MAX(created_at) FROM execution) AS latest_execution_at
                """,
            )
            recent_crashes = forward_core._query(
                conn,
                """
                SELECT
                    p.fuzzer_id,
                    e.program_hash,
                    e.created_at,
                    e.coverage_total,
                    e.edges_found
                FROM execution e
                JOIN program p ON p.program_hash = e.program_hash
                WHERE e.execution_outcome_id = 1
                ORDER BY e.created_at DESC
                LIMIT 20
                """,
            )
            generated_queue = forward_core._query(
                conn,
                """
                SELECT target_fuzzer_id, program_hash, created_at, source, metadata
                FROM generated_program_queue
                ORDER BY created_at DESC
                LIMIT 20
                """,
            )
        finally:
            conn.close()
    except Exception as exc:
        return {
            "error": str(exc),
            "monitor_status": monitor_status,
            "summary": {},
            "fuzzers": [],
            "recent_crashes": [],
            "generated_queue": [],
        }

    return {
        "monitor_status": monitor_status,
        "summary": summary,
        "fuzzers": fuzzers,
        "recent_crashes": recent_crashes,
        "generated_queue": generated_queue,
    }


def _fuzzer_detail_payload(monitor: forward_core.FuzzerMonitor, fuzzer_id: int) -> dict:
    with monitor._lock:
        status_snapshot = dict(monitor.status)

    try:
        conn = forward_core._connect()
        try:
            details = forward_core._query_one(
                conn,
                """
                SELECT
                    m.fuzzer_id,
                    m.status,
                    m.created_at AS fuzzer_started,
                    m.last_activity,
                    stats.total_programs,
                    stats.total_executions,
                    stats.executions_last_hour,
                    stats.execs_per_second,
                    stats.total_crashes,
                    stats.crashes_last_hour,
                    stats.new_edges_found,
                    stats.max_coverage,
                    stats.avg_coverage,
                    stats.max_edges_found,
                    COALESCE(gq.queued_programs, 0) AS queued_programs
                FROM main m
                LEFT JOIN (
                    SELECT target_fuzzer_id, COUNT(*) AS queued_programs
                    FROM generated_program_queue
                    GROUP BY target_fuzzer_id
                ) gq ON gq.target_fuzzer_id = m.fuzzer_id
                LEFT JOIN LATERAL (
                    SELECT
                        COALESCE(COUNT(DISTINCT p.program_hash), 0)::bigint AS total_programs,
                        COALESCE(COUNT(e.execution_id), 0)::bigint AS total_executions,
                        COALESCE(
                            COUNT(e.execution_id) FILTER (WHERE e.created_at > NOW() - INTERVAL '1 hour'),
                            0
                        )::bigint AS executions_last_hour,
                        ROUND(
                            COALESCE(
                                COUNT(e.execution_id) FILTER (WHERE e.created_at > NOW() - INTERVAL '1 hour'),
                                0
                            )::numeric / 3600.0,
                            2
                        ) AS execs_per_second,
                        COALESCE(COUNT(e.execution_id) FILTER (WHERE e.execution_outcome_id = 1), 0)::bigint
                            AS total_crashes,
                        COALESCE(
                            COUNT(e.execution_id) FILTER (
                                WHERE e.execution_outcome_id = 1
                                AND e.created_at > NOW() - INTERVAL '1 hour'
                            ),
                            0
                        )::bigint AS crashes_last_hour,
                        COALESCE(COUNT(e.execution_id) FILTER (WHERE e.is_new_edge = TRUE), 0)::bigint
                            AS new_edges_found,
                        COALESCE(MAX(e.coverage_total), 0::numeric) AS max_coverage,
                        COALESCE(
                            AVG(e.coverage_total) FILTER (WHERE e.coverage_total IS NOT NULL),
                            0::numeric
                        ) AS avg_coverage,
                        COALESCE(MAX(e.edges_found), 0)::bigint AS max_edges_found
                    FROM program p
                    LEFT JOIN execution e ON e.program_hash = p.program_hash
                    WHERE p.fuzzer_id = m.fuzzer_id
                ) stats ON true
                WHERE m.fuzzer_id = %s
                LIMIT 1
                """,
                (fuzzer_id,),
            )
            coverage = forward_core._query(
                conn,
                """
                SELECT
                    DATE_TRUNC('hour', e.created_at) AS time_bucket,
                    MAX(e.coverage_total) AS max_coverage,
                    AVG(e.coverage_total) AS avg_coverage,
                    MAX(e.edges_found) AS max_edges_found,
                    COUNT(e.execution_id) FILTER (WHERE e.is_new_edge = TRUE) AS new_edges_count,
                    COUNT(e.execution_id) AS execution_count
                FROM execution e
                JOIN program p ON e.program_hash = p.program_hash
                WHERE p.fuzzer_id = %s
                  AND e.coverage_total IS NOT NULL
                GROUP BY DATE_TRUNC('hour', e.created_at)
                ORDER BY time_bucket DESC
                LIMIT 48
                """,
                (fuzzer_id,),
            )
            recent_executions = forward_core._query(
                conn,
                """
                SELECT
                    e.execution_id,
                    e.created_at,
                    eo.outcome,
                    e.program_hash,
                    e.coverage_total,
                    e.edges_found,
                    e.total_edges,
                    e.is_new_edge
                FROM execution e
                JOIN program p ON p.program_hash = e.program_hash
                JOIN execution_outcome eo ON eo.id = e.execution_outcome_id
                WHERE p.fuzzer_id = %s
                ORDER BY e.created_at DESC
                LIMIT 30
                """,
                (fuzzer_id,),
            )
            top_programs = forward_core._query(
                conn,
                """
                SELECT
                    p.program_hash,
                    p.created_at,
                    p.source_mutators,
                    p.contributors,
                    COUNT(e.execution_id) AS execution_count,
                    MAX(e.coverage_total) AS max_coverage,
                    MAX(e.edges_found) AS max_edges_found,
                    COUNT(e.execution_id) FILTER (WHERE e.is_new_edge = TRUE) AS new_edges_discovered,
                    COUNT(e.execution_id) FILTER (WHERE e.execution_outcome_id = 1) AS crash_count,
                    COUNT(e.execution_id) FILTER (WHERE e.execution_outcome_id = 3) AS success_count,
                    COUNT(e.execution_id) FILTER (WHERE e.execution_outcome_id = 4) AS timeout_count,
                    LENGTH(p.program_base64) AS program_size,
                    MAX(e.created_at) AS last_execution
                FROM program p
                LEFT JOIN execution e ON e.program_hash = p.program_hash
                WHERE p.fuzzer_id = %s
                GROUP BY
                    p.fuzzer_id,
                    p.program_hash,
                    p.created_at,
                    p.source_mutators,
                    p.contributors,
                    p.program_base64
                ORDER BY new_edges_discovered DESC, max_coverage DESC NULLS LAST, last_execution DESC NULLS LAST
                LIMIT 20
                """,
                (fuzzer_id,),
            )
            queued_programs = forward_core._query(
                conn,
                """
                SELECT program_hash, created_at, source, metadata
                FROM generated_program_queue
                WHERE target_fuzzer_id = %s
                ORDER BY created_at DESC
                LIMIT 20
                """,
                (fuzzer_id,),
            )
        finally:
            conn.close()
    except Exception as exc:
        return {"error": str(exc), "status": status_snapshot}

    return {
        "details": details,
        "coverage": list(reversed(coverage)),
        "recent_executions": recent_executions,
        "top_programs": top_programs,
        "queued_programs": queued_programs,
        "status": status_snapshot,
    }


def _json_response(handler, code: int, data):
    body = json.dumps(data, default=str).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _html_response(handler, code: int, body: str):
    payload = body.encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


def _static_response(handler, path: Path):
    if not path.is_file():
        _json_response(handler, 404, {"error": "not found"})
        return

    suffix = path.suffix.lower()
    content_type = {
        ".js": "application/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".html": "text/html; charset=utf-8",
        ".json": "application/json; charset=utf-8",
    }.get(suffix, "application/octet-stream")

    payload = path.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


def make_handler(monitor: forward_core.FuzzerMonitor):
    class Handler(BaseHTTPRequestHandler):
        log_message = lambda self, fmt, *args: None

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/")
            query = parse_qs(parsed.query)

            if path in ("", "/dashboard"):
                _html_response(self, 200, _render_template(_dashboard_template, "dashboard"))
                return

            if path == "/logs":
                _html_response(self, 200, _render_template(_logs_template, "logs"))
                return

            if path == "/generated":
                self.send_response(302)
                self.send_header("Location", "/corpus")
                self.end_headers()
                return

            if path == "/corpus":
                _html_response(self, 200, _render_template(_corpus_template, "corpus"))
                return

            if path.startswith("/assets/"):
                asset_name = path.removeprefix("/assets/")
                asset_path = (_assets_dir / asset_name).resolve()
                try:
                    asset_path.relative_to(_assets_dir.resolve())
                except ValueError:
                    _json_response(self, 403, {"error": "forbidden"})
                    return
                _static_response(self, asset_path)
                return

            if path == "/health":
                _json_response(self, 200, {"ok": True})
                return

            if path == "/status":
                with monitor._lock:
                    snap = dict(monitor.status)
                _json_response(self, 200, snap)
                return

            if path == "/fuzzers":
                _json_response(self, 200, {"fuzzers": monitor.get_fuzzers()})
                return

            if path == "/api/overview":
                _json_response(self, 200, _overview_payload(monitor))
                return

            if path.startswith("/api/fuzzer/"):
                try:
                    fuzzer_id = int(path.split("/")[-1])
                except ValueError:
                    _json_response(self, 400, {"error": "invalid fuzzer id"})
                    return
                _json_response(self, 200, _fuzzer_detail_payload(monitor, fuzzer_id))
                return

            if path == "/api/logs":
                _json_response(self, 200, {"logs": _list_log_sources()})
                return

            if path.startswith("/api/corpus-programs/"):
                program_hash = path.removeprefix("/api/corpus-programs/")
                if not _PROGRAM_HASH_RE.match(program_hash):
                    _json_response(self, 400, {"error": "invalid program_hash"})
                    return
                try:
                    payload = _corpus_program_detail(program_hash)
                except Exception as exc:
                    _json_response(self, 400, {"error": str(exc)})
                    return
                if payload.get("error"):
                    code = 404 if "not found" in payload["error"].lower() else 400
                    _json_response(self, code, payload)
                    return
                _json_response(self, 200, payload)
                return

            if path == "/api/corpus-programs":
                raw_f = (query.get("fuzzer_id") or [None])[0]
                fuzzer_id = None
                if raw_f not in (None, ""):
                    try:
                        fuzzer_id = int(raw_f)
                    except ValueError:
                        _json_response(self, 400, {"error": "invalid fuzzer_id"})
                        return
                try:
                    lim = int((query.get("limit") or ["50"])[0])
                except ValueError:
                    lim = 50
                try:
                    off = int((query.get("offset") or ["0"])[0])
                except ValueError:
                    off = 0
                try:
                    rows = _corpus_program_list(fuzzer_id, lim, off)
                except Exception as exc:
                    _json_response(self, 400, {"error": str(exc)})
                    return
                _json_response(self, 200, {"programs": rows})
                return

            if path == "/api/logs/content":
                ref = (query.get("id") or [""])[0]
                try:
                    lines = int((query.get("lines") or ["500"])[0])
                except ValueError:
                    lines = 500
                if lines < 0:
                    lines = 500
                if lines > 500_000:
                    lines = 500_000
                if not ref:
                    _json_response(self, 400, {"error": "log id required"})
                    return
                try:
                    _json_response(self, 200, _read_log_source(ref, lines=lines))
                except Exception as exc:
                    _json_response(self, 400, {"error": str(exc)})
                return

            _json_response(self, 404, {"error": "not found"})

        def do_POST(self):
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/")

            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                _json_response(self, 400, {"error": "invalid JSON"})
                return

            if path == "/trigger/crash":
                fuzzer_id = payload.get("fuzzer_id")
                program_hash = payload.get("program_hash")
                if fuzzer_id is None or not program_hash:
                    _json_response(self, 400, {"error": "fuzzer_id and program_hash required"})
                    return
                monitor.trigger_crash(int(fuzzer_id), str(program_hash))
                _json_response(
                    self,
                    202,
                    {
                        "status": "crash agent dispatched",
                        "fuzzer_id": fuzzer_id,
                        "program_hash": program_hash,
                    },
                )
                return

            if path == "/trigger/plateau":
                fuzzer_id = payload.get("fuzzer_id")
                if fuzzer_id is None:
                    _json_response(self, 400, {"error": "fuzzer_id required"})
                    return
                monitor.trigger_plateau(int(fuzzer_id))
                _json_response(self, 202, {"status": "plateau agent dispatched", "fuzzer_id": fuzzer_id})
                return

            _json_response(self, 404, {"error": "not found"})

    return Handler


def main():
    parser = argparse.ArgumentParser(description="Web UI server for the fuzzer control panel")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8765, help="HTTP port (default: 8765)")
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=forward_core.POLL_INTERVAL_SECONDS,
        help=f"DB poll interval in seconds (default: {forward_core.POLL_INTERVAL_SECONDS})",
    )
    parser.add_argument(
        "--stagnation-window",
        type=int,
        default=forward_core.STAGNATION_WINDOW_MINUTES,
        help=f"Stagnation detection window in minutes (default: {forward_core.STAGNATION_WINDOW_MINUTES})",
    )
    parser.add_argument("--log-level", default="INFO", help="Logging level (default: INFO)")
    parser.add_argument(
        "--no-monitor",
        action="store_true",
        help="Start only the web UI and API without the background DB monitor",
    )
    parser.add_argument(
        "--no-auto-agents",
        action="store_true",
        help="Run the DB monitor but never automatically spawn crash or plateau agents; use POST /trigger/crash and /trigger/plateau only",
    )
    args = parser.parse_args()

    forward_core._setup_logging(args.log_level, stem="web_ui_server")
    forward_core.POLL_INTERVAL_SECONDS = args.poll_interval
    forward_core.STAGNATION_WINDOW_MINUTES = args.stagnation_window

    if args.no_monitor and args.no_auto_agents:
        logger.warning("--no-auto-agents has no effect while --no-monitor is set")

    monitor = forward_core.FuzzerMonitor(auto_dispatch_agents=not args.no_auto_agents)
    if not args.no_monitor:
        thread = threading.Thread(target=monitor.run_forever, name="db-monitor", daemon=True)
        thread.start()

    server = ThreadingHTTPServer((args.host, args.port), make_handler(monitor))
    logger.info("Web UI listening on %s:%d", args.host, args.port)
    logger.info(
        "Endpoints: GET /, /logs, /corpus, /generated (redirect), /health, /status, /fuzzers, /api/overview, /api/fuzzer/<id>, /api/logs, /api/logs/content, /api/corpus-programs, /api/corpus-programs/<program_hash> | POST /trigger/crash /trigger/plateau"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down")
        monitor.stop()
        server.server_close()


if __name__ == "__main__":
    main()
