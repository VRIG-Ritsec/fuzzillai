#!/usr/bin/env python3
"""
Extract crash details for a single program hash from Postgres.

Outputs:
  - <outdir>/<hash>.json            full metadata (program + executions + flags)
  - <outdir>/<hash>.flags.txt       execution/engine flags if available
  - <outdir>/<hash>.b64             raw program_base64 from DB (when present)
  - <outdir>/<hash>.fzil            decoded FuzzIL bytes (when program_base64 present)
  - <outdir>/<hash>.js              lifted JavaScript (when --lift-js and FuzzILTool exists)

Connection:
  POSTGRES_URL
  or POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

DB_DOCKER_CONTAINER: Optional[str] = None


def load_dotenv(repo_root: Path) -> None:
    for name in (".env", "env.distributed"):
        p = repo_root / name
        if not p.is_file():
            continue
        for raw in p.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            key = k.strip()
            val = v.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


def is_valid_hash(value: str) -> bool:
    if len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def sql_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def psql_base_cmd() -> Tuple[List[str], Dict[str, str]]:
    url = os.getenv("POSTGRES_URL", "").strip()
    if url:
        return (["psql", url], os.environ.copy())
    env = os.environ.copy()
    env["PGPASSWORD"] = os.getenv("POSTGRES_PASSWORD", "fuzzilli123")
    cmd = [
        "psql",
        "-h",
        os.getenv("POSTGRES_HOST", "localhost"),
        "-p",
        str(os.getenv("POSTGRES_PORT", "5432")),
        "-U",
        os.getenv("POSTGRES_USER", "fuzzilli"),
        "-d",
        os.getenv("POSTGRES_DB", "fuzzilli_master"),
    ]
    return (cmd, env)


def docker_psql_cmd(container: str) -> Tuple[List[str], Dict[str, str]]:
    cmd = [
        "docker",
        "exec",
        "-i",
        container,
        "psql",
        "-U",
        os.getenv("POSTGRES_USER", "fuzzilli"),
        "-d",
        os.getenv("POSTGRES_DB", "fuzzilli_master"),
    ]
    return (cmd, os.environ.copy())


def run_sql_json(sql: str) -> List[Dict[str, Any]]:
    wrapper = (
        "SELECT COALESCE(json_agg(row_to_json(t)), '[]'::json)::text "
        f"FROM ({sql}) t;"
    )
    attempts: List[Tuple[str, List[str], Dict[str, str]]] = []
    base, env = psql_base_cmd()
    attempts.append(("direct", base, env))
    if DB_DOCKER_CONTAINER:
        dcmd, denv = docker_psql_cmd(DB_DOCKER_CONTAINER)
        attempts.append((f"docker:{DB_DOCKER_CONTAINER}", dcmd, denv))

    errors: List[str] = []
    for label, base_cmd, cmd_env in attempts:
        cmd = base_cmd + ["-t", "-A", "-X", "-c", wrapper]
        proc = subprocess.run(cmd, capture_output=True, text=True, env=cmd_env)
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            errors.append(f"{label}: {err}")
            continue

        text = (proc.stdout or "").strip()
        if not text:
            return []
        try:
            loaded = json.loads(text)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Failed to parse psql JSON output ({label}): {e}") from e
        if isinstance(loaded, list):
            return loaded
        return []

    raise RuntimeError("psql query failed; attempts: " + " | ".join(errors))


def table_exists(table_name: str) -> bool:
    q = f"""
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = {sql_quote(table_name)}
        ) AS exists
    """
    rows = run_sql_json(q)
    return bool(rows and rows[0].get("exists"))


def get_columns(table_name: str) -> List[str]:
    q = f"""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = {sql_quote(table_name)}
        ORDER BY ordinal_position
    """
    rows = run_sql_json(q)
    return [str(r["column_name"]) for r in rows if "column_name" in r]


def to_jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, list):
        return [to_jsonable(v) for v in value]
    if isinstance(value, tuple):
        return [to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    return value


def one_row(sql: str) -> Optional[Dict[str, Any]]:
    rows = run_sql_json(sql)
    if not rows:
        return None
    return to_jsonable(rows[0])


def many_rows(sql: str) -> List[Dict[str, Any]]:
    rows = run_sql_json(sql)
    return [to_jsonable(r) for r in rows]


def select_exprs(alias: str, columns: Sequence[str], wanted: Sequence[str]) -> List[str]:
    existing = set(columns)
    return [f"{alias}.{c}" for c in wanted if c in existing]


def find_related_flag_columns(columns: Sequence[str]) -> List[str]:
    picks: List[str] = []
    for c in columns:
        lc = c.lower()
        if "flag" in lc or "arg" in lc or "option" in lc:
            picks.append(c)
    return picks


def write_text(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding="utf-8")


def dump_program_files(outdir: Path, program_hash: str, b64: Optional[str]) -> Dict[str, Optional[str]]:
    written: Dict[str, Optional[str]] = {"b64": None, "fzil": None}
    if not b64:
        return written

    b64_path = outdir / f"{program_hash}.b64"
    write_text(b64_path, b64.strip() + "\n")
    written["b64"] = str(b64_path)

    try:
        raw = base64.b64decode(b64, validate=False)
    except Exception:
        return written

    fzil_path = outdir / f"{program_hash}.fzil"
    fzil_path.write_bytes(raw)
    written["fzil"] = str(fzil_path)
    return written


def maybe_lift_js(
    outdir: Path,
    program_hash: str,
    b64: Optional[str],
    fuzziltool: Path,
    do_lift: bool,
) -> Tuple[Optional[str], Optional[str]]:
    if not do_lift:
        return None, None
    if not b64:
        return None, "program_base64 is missing; cannot lift JS"
    if not fuzziltool.is_file() or not os.access(fuzziltool, os.X_OK):
        return None, f"FuzzILTool not executable: {fuzziltool}"

    try:
        raw = base64.b64decode(b64, validate=False)
    except Exception as e:
        return None, f"base64 decode failed: {e}"

    with tempfile.NamedTemporaryFile(suffix=".fzil", delete=False) as tmp:
        tmp.write(raw)
        src = Path(tmp.name)

    js_path = outdir / f"{program_hash}.js"
    try:
        proc = subprocess.run(
            [str(fuzziltool), "--liftToJS", str(src)],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            err = (proc.stderr or "") + (proc.stdout or "")
            return None, err.strip()[:4000] or "FuzzILTool failed"
        write_text(js_path, proc.stdout or "")
        return str(js_path), None
    finally:
        try:
            src.unlink()
        except OSError:
            pass


def parse_profile_name(engine_args: Sequence[str]) -> Optional[str]:
    for token in engine_args:
        if token.startswith("--profile="):
            return token.split("=", 1)[1].strip() or None
    return None


def parse_d8_path(engine_args: Sequence[str]) -> Optional[str]:
    for token in reversed(engine_args):
        t = token.strip()
        if not t:
            continue
        if t.endswith("/d8") or t == "d8":
            return t
    return None


def parse_profile_default_flags(repo_root: Path, profile_name: str) -> List[str]:
    profile_to_file = {
        "v8": repo_root / "Sources" / "FuzzilliCli" / "Profiles" / "V8Profile.swift",
        "v8holefuzzing": repo_root / "Sources" / "FuzzilliCli" / "Profiles" / "V8HoleFuzzingProfile.swift",
    }
    src = profile_to_file.get(profile_name.lower())
    if not src or not src.is_file():
        return []

    in_list = False
    flags: List[str] = []
    for raw in src.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if "var args = [" in line:
            in_list = True
            continue
        if not in_list:
            continue
        if line.startswith("]"):
            break
        # Accept quoted entries like: "--fuzzing",
        if '"' not in line:
            continue
        parts = line.split('"')
        if len(parts) < 3:
            continue
        val = parts[1].strip()
        if val.startswith("--"):
            flags.append(val)
    return flags


def derive_d8_flags(repo_root: Path, engine_args: Sequence[str]) -> Tuple[Optional[str], Optional[str], List[str]]:
    profile = parse_profile_name(engine_args)
    d8_path = parse_d8_path(engine_args)

    # If explicit args are passed after d8 path in engine_arguments, treat those as highest priority.
    explicit: List[str] = []
    if d8_path and d8_path in engine_args:
        idx = list(engine_args).index(d8_path)
        for token in engine_args[idx + 1 :]:
            if token.startswith("--"):
                explicit.append(token)

    if explicit:
        return profile, d8_path, explicit

    # Otherwise derive defaults from profile source.
    if profile:
        defaults = parse_profile_default_flags(repo_root, profile)
        if defaults:
            return profile, d8_path, defaults

    return profile, d8_path, []


def main() -> int:
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent
    load_dotenv(repo_root)

    parser = argparse.ArgumentParser(description="Extract crash details for one program hash")
    parser.add_argument("program_hash", help="64-hex program/crash hash")
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=Path("/tmp/fuzzillai_hash_extract"),
        help="Directory to write outputs",
    )
    parser.add_argument(
        "--include-non-crash-executions",
        action="store_true",
        help="Include executions for all outcomes, not only Crashed",
    )
    parser.add_argument(
        "--lift-js",
        action="store_true",
        help="Also lift program to JS via FuzzILTool",
    )
    parser.add_argument(
        "--fuzziltool",
        type=Path,
        default=Path(os.getenv("FUZZILTOOL", str(repo_root / ".build" / "debug" / "FuzzILTool"))),
        help="Path to FuzzILTool",
    )
    parser.add_argument(
        "--docker-container",
        default=os.getenv("DB_CONTAINER", "fuzzilli-postgres-master"),
        help="Optional docker container name for psql fallback",
    )
    args = parser.parse_args()
    global DB_DOCKER_CONTAINER
    DB_DOCKER_CONTAINER = args.docker_container.strip() or None

    program_hash = args.program_hash.lower().strip()
    if not is_valid_hash(program_hash):
        print(f"Error: invalid hash: {args.program_hash}", file=sys.stderr)
        return 2

    outdir = args.output_dir.resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    # Smoke-test psql availability and DB connectivity.
    try:
        _ = run_sql_json("SELECT 1 AS ok")
    except Exception as e:
        print(f"Error: failed to query Postgres via psql: {e}", file=sys.stderr)
        return 1

    for required in ("program", "execution", "execution_outcome"):
        if not table_exists(required):
            print(f"Error: required table missing: {required}", file=sys.stderr)
            return 1

    p_cols = get_columns("program")
    e_cols = get_columns("execution")
    m_cols = get_columns("main") if table_exists("main") else []
    eo_cols = get_columns("execution_outcome")

    program_fields = select_exprs(
        "p",
        p_cols,
        (
            "program_hash",
            "fuzzer_id",
            "created_at",
            "inserted_at",
            "program_size",
            "parent_program_hash",
            "source_mutators",
            "source_mutator",
            "program_base64",
        ),
    )
    if not program_fields:
        print("Error: could not find expected columns in program table", file=sys.stderr)
        return 1

    program_sql = f"""
        SELECT {", ".join(program_fields)}
        FROM program p
        WHERE p.program_hash = {sql_quote(program_hash)}
        LIMIT 1
    """
    program = one_row(program_sql)
    if not program:
        print(f"No program row found for hash: {program_hash}", file=sys.stderr)
        return 3

    crash_filter = ""
    if not args.include_non_crash_executions and "outcome" in eo_cols:
        crash_filter = "AND eo.outcome = 'Crashed'"

    execution_fields = select_exprs(
        "e",
        e_cols,
        (
            "execution_id",
            "created_at",
            "execution_outcome_id",
            "execution_time_ms",
            "coverage_total",
            "edges_found",
            "total_edges",
            "is_new_edge",
            "feedback_nexus_count",
            "turbofan_optimization_bits",
            "mutator_type_id",
            "signal",
            "signal_code",
            "stdout",
            "stderr",
            "fuzzout",
        ),
    )
    if "outcome" in eo_cols:
        execution_fields.append("eo.outcome")
    if not execution_fields:
        execution_fields = ["e.program_hash"]

    order_col = "e.created_at" if "created_at" in e_cols else "e.execution_id"
    executions_sql = f"""
        SELECT {", ".join(execution_fields)}
        FROM execution e
        LEFT JOIN execution_outcome eo ON e.execution_outcome_id = eo.id
        WHERE e.program_hash = {sql_quote(program_hash)}
        {crash_filter}
        ORDER BY {order_col} DESC NULLS LAST
    """
    executions = many_rows(executions_sql)

    engine_args_raw: List[str] = []
    fuzzer_meta: Optional[Dict[str, Any]] = None
    if m_cols and "fuzzer_id" in program and program["fuzzer_id"] is not None:
        main_fields = select_exprs(
            "m",
            m_cols,
            ("fuzzer_id", "fuzzer_name", "status", "created_at", "last_activity", "engine_arguments"),
        )
        if main_fields:
            fid = int(program["fuzzer_id"])
            main_sql = f"""
                SELECT {", ".join(main_fields)}
                FROM main m
                WHERE m.fuzzer_id = {fid}
                LIMIT 1
            """
            fuzzer_meta = one_row(main_sql)
            if fuzzer_meta and isinstance(fuzzer_meta.get("engine_arguments"), list):
                engine_args_raw = [str(x) for x in fuzzer_meta["engine_arguments"]]

    profile_name, d8_path, d8_flags = derive_d8_flags(repo_root, engine_args_raw)

    flagged_cols = {
        "program": find_related_flag_columns(p_cols),
        "execution": find_related_flag_columns(e_cols),
        "main": find_related_flag_columns(m_cols),
    }

    payload: Dict[str, Any] = {
        "query_hash": program_hash,
        "extracted_at_utc": datetime.now(timezone.utc).isoformat(),
        "program": program,
        "fuzzer": fuzzer_meta,
        "engine_arguments_raw": engine_args_raw,
        "profile_name": profile_name,
        "d8_path": d8_path,
        "d8_flags": d8_flags,
        "executions": executions,
        "column_hints_for_flags": flagged_cols,
        "execution_count": len(executions),
    }

    json_path = outdir / f"{program_hash}.json"
    write_text(json_path, json.dumps(payload, indent=2, ensure_ascii=True) + "\n")

    flag_lines: List[str] = []
    flag_lines.append(f"# program_hash={program_hash}")
    if "fuzzer_id" in program:
        flag_lines.append(f"# fuzzer_id={program.get('fuzzer_id')}")
    if profile_name:
        flag_lines.append(f"# profile={profile_name}")
    if d8_path:
        flag_lines.append(f"# d8_path={d8_path}")
    flag_lines.append("# d8 runtime flags")
    if d8_flags:
        flag_lines.extend(d8_flags)
    else:
        flag_lines.append("# none found")
    flags_path = outdir / f"{program_hash}.flags.txt"
    write_text(flags_path, "\n".join(flag_lines) + "\n")

    file_info = dump_program_files(outdir, program_hash, program.get("program_base64"))
    js_path, lift_err = maybe_lift_js(
        outdir=outdir,
        program_hash=program_hash,
        b64=program.get("program_base64"),
        fuzziltool=args.fuzziltool,
        do_lift=args.lift_js,
    )
    if lift_err:
        write_text(outdir / f"{program_hash}.lift_error.txt", lift_err + "\n")

    crash_execs = 0
    for row in executions:
        if str(row.get("outcome", "")).lower() == "crashed":
            crash_execs += 1

    print(f"Wrote: {json_path}")
    print(f"Wrote: {flags_path}")
    if file_info.get("b64"):
        print(f"Wrote: {file_info['b64']}")
    if file_info.get("fzil"):
        print(f"Wrote: {file_info['fzil']}")
    if js_path:
        print(f"Wrote: {js_path}")
    elif args.lift_js:
        print(f"JS lift failed; see: {outdir / (program_hash + '.lift_error.txt')}")

    print(f"Summary: executions={len(executions)} crashed={crash_execs} fuzzer_id={program.get('fuzzer_id')}")
    if d8_flags:
        print(f"d8 flags found: {len(d8_flags)}")
    else:
        print("d8 flags found: 0")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
