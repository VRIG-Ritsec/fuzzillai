"""
Regression tests for EBG/FoG tooling failure modes.
"""

import base64
import json
import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_here = Path(__file__).resolve().parent
_agentic_dir = _here.parent
_ikacore_src = _agentic_dir / "IkaCore" / "src"
for candidate in [str(_agentic_dir), str(_ikacore_src)]:
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

if "IkaCore" not in sys.modules:
    import types

    ika_pkg = types.ModuleType("IkaCore")
    ika_tools_mod = types.ModuleType("IkaCore.tools")

    class _IkaTools:
        def __init__(self, name, description, parameters, execute_function, **kwargs):
            self.name = name
            self.description = description
            self.parameters = parameters
            self.execute_function = execute_function

    ika_tools_mod.IkaTools = _IkaTools
    ika_pkg.tools = ika_tools_mod
    sys.modules["IkaCore"] = ika_pkg
    sys.modules["IkaCore.tools"] = ika_tools_mod

import tools._shared as shared_tools
import tools.FoG_tools._shared as fog_shared
import tools.FoG_tools.fuzzil as fog_fuzzil
import tools.FoG_tools.program_template as program_template
import tools.EBG_tools.execution as execution
import tools.EBG_tools.db as db
import tools.EBG_tools.generate_folder as generate_folder


class _FakeCursor:
    def __init__(self, row=None, rows=None, fetchone_rows=None, fetchall_rows=None):
        self.row = row
        self.rows = rows or []
        self.fetchone_rows = list(fetchone_rows or [])
        self.fetchall_rows = list(fetchall_rows or [])
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def fetchone(self):
        if self.fetchone_rows:
            return self.fetchone_rows.pop(0)
        return self.row

    def fetchall(self):
        if self.fetchall_rows:
            return self.fetchall_rows.pop(0)
        return self.rows


class _FakeConn:
    def __init__(self, row=None, rows=None, fetchone_rows=None, fetchall_rows=None):
        self.cursor_obj = _FakeCursor(
            row=row,
            rows=rows,
            fetchone_rows=fetchone_rows,
            fetchall_rows=fetchall_rows,
        )
        self.committed = False

    def cursor(self, *args, **kwargs):
        return self.cursor_obj

    def commit(self):
        self.committed = True

    def close(self):
        pass


class _FakeGdbController:
    def __init__(self, command=None):
        self.command = command
        self.commands = []

    def write(self, command, read_response=False, timeout_sec=None):
        self.commands.append(command)
        return [{"command": command}]

    def exit(self):
        return None


class TestEBGTooling(unittest.TestCase):
    def test_write_and_execute_js_reports_missing_d8_path(self):
        with patch.object(shared_tools, "D8_PATH", ""):
            result = json.loads(execution.write_and_execute_js("print(1);", file_name="sample.js"))

        self.assertEqual(result["error"], "Error: D8_PATH is not set")
        self.assertIn("Error: D8_PATH is not set", result["execution_result"])

    def test_program_template_executor_reports_missing_d8_path(self):
        with patch.object(fog_shared, "D8_PATH", ""):
            result = program_template._execute_javascript_program_executor(
                {"template_js_path": "/tmp/example.js", "d8_flags": "--trace-opt"}
            )

        self.assertIn("Error: D8_PATH is not set", result)

    def test_program_template_executor_resolves_relative_generate_folder_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            js_path = Path(temp_dir) / "validate_program.js"
            js_path.write_text("print(1);\n")

            with patch(
                "tools.EBG_tools._shared._get_varianal_folder", return_value=temp_dir
            ), patch.object(
                program_template,
                "run_d8_command",
                return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr=""),
            ) as mock_run:
                result = program_template._execute_javascript_program_executor(
                    {"template_js_path": "validate_program.js", "d8_flags": "--trace-opt"}
                )

        called_args = mock_run.call_args.args[0]
        called_cwd = mock_run.call_args.kwargs["cwd"]
        self.assertEqual(called_args[-1], str(js_path.resolve()))
        self.assertEqual(called_cwd, fog_shared._runtime_artifact_dir(str(js_path.resolve())))
        self.assertIn("ok", result)

    def test_program_template_executor_drops_unsupported_flags(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            js_path = Path(temp_dir) / "validate_program.js"
            js_path.write_text("print(1);\n")

            with patch(
                "tools.EBG_tools._shared._get_varianal_folder", return_value=temp_dir
            ), patch.object(
                program_template,
                "_get_supported_d8_flags",
                return_value={
                    "--allow-natives-syntax",
                    "--print-bytecode",
                    "--trace-opt",
                    "--trace-deopt",
                    "--trace-osr",
                    "--trace-turbo",
                    "--trace-maglev-graph-building",
                },
            ), patch.object(
                program_template,
                "run_d8_command",
                return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr=""),
            ) as mock_run:
                result = program_template._execute_javascript_program_executor(
                    {
                        "template_js_path": "validate_program.js",
                        "d8_flags": "--trace-turbofan --print-code --trace-opt",
                    }
                )

        called_args = mock_run.call_args.args[0]
        called_cwd = mock_run.call_args.kwargs["cwd"]
        self.assertNotIn("--trace-turbofan", called_args)
        self.assertNotIn("--print-code", called_args)
        self.assertIn("--trace-opt", called_args)
        self.assertEqual(called_cwd, fog_shared._runtime_artifact_dir(str(js_path.resolve())))
        self.assertIn("[dropped unsupported/problematic flags] --trace-turbofan --print-code", result)

    def test_fog_fuzzil_tools_report_missing_tool_binary(self):
        with patch.object(fog_shared, "FUZZILLI_TOOL_BIN", ""), patch.object(
            fog_fuzzil, "run_fuzzilli_tool", side_effect=fog_shared.run_fuzzilli_tool
        ):
            result = fog_fuzzil._compile_js_to_fuzzil_executor({"target": "/tmp/example.js"})

        self.assertIn("Error: FUZZILLI_TOOL_BIN is not set", result)

    def test_db_store_generated_program_compiles_and_enqueues_into_generated_queue(self):
        compiled_bytes = b"compiled-fuzzil"
        fake_conn = _FakeConn(row={"program_hash": "unused"})

        def fake_run_fuzzilli_tool(args, timeout=90, cwd=None, env=None):
            self.assertEqual(args[0], "--compile")
            js_path = Path(args[1])
            self.assertTrue(str(js_path.parent).startswith(str(Path(db.TEMP_FUZZIL_PATH).parent)))
            js_path.with_suffix(".fzil").write_bytes(compiled_bytes)
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

        with patch.object(db.psycopg2, "connect", return_value=fake_conn), patch.object(
            shared_tools, "run_fuzzilli_tool", side_effect=fake_run_fuzzilli_tool
        ):
            raw = json.loads(db.db_store_generated_program("print(1);", 3))

        query, params = fake_conn.cursor_obj.executed[-1]
        expected_b64 = base64.b64encode(compiled_bytes).decode("utf-8")
        expected_hash = hashlib.sha256(expected_b64.encode("utf-8")).hexdigest()

        self.assertIn("INSERT INTO generated_program_queue", query)
        self.assertEqual(params[0], 3)
        self.assertEqual(params[1], expected_hash)
        self.assertEqual(params[2], expected_b64)
        self.assertEqual(params[3], "agentic")
        self.assertIsInstance(params[4], db.psycopg2.extras.Json)
        self.assertEqual(
            params[4].adapted,
            {
                "source_mutators": ["AgenticJSSeed"],
                "contributors": ["EBGGeneratedJS"],
            },
        )
        self.assertEqual(raw["program_id"], "unused")
        self.assertEqual(raw["target_fuzzer_id"], 3)
        self.assertTrue(fake_conn.committed)

    def test_db_store_generated_program_accepts_fuzzer_label(self):
        compiled_bytes = b"compiled-fuzzil"
        fake_conn = _FakeConn(row={"program_hash": "queued"})

        def fake_run_fuzzilli_tool(args, timeout=90, cwd=None, env=None):
            js_path = Path(args[1])
            js_path.with_suffix(".fzil").write_bytes(compiled_bytes)
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

        with patch.object(db.psycopg2, "connect", return_value=fake_conn), patch.object(
            shared_tools, "run_fuzzilli_tool", side_effect=fake_run_fuzzilli_tool
        ):
            raw = json.loads(db.db_store_generated_program("print(1);", "fuzzer-7"))

        _, params = fake_conn.cursor_obj.executed[-1]
        self.assertEqual(params[0], 7)
        self.assertEqual(raw["target_fuzzer_id"], 7)

    def test_db_resolve_fuzzer_id_accepts_fuzzer_label(self):
        fake_conn = _FakeConn(
            fetchone_rows=[
                {
                    "fuzzer_id": 7,
                    "status": "active",
                    "created_at": None,
                    "last_activity": None,
                    "engine_arguments": ["--foo"],
                }
            ]
        )

        with patch.object(db.psycopg2, "connect", return_value=fake_conn):
            raw = json.loads(db.db_resolve_fuzzer_id("fuzzer-7"))

        self.assertEqual(raw["resolved_fuzzer_id"], 7)
        self.assertEqual(raw["fuzzer"]["status"], "active")

    def test_db_get_fuzzer_performance_summary_tool_accepts_fuzzer_label(self):
        fake_conn = _FakeConn(
            fetchone_rows=[
                {
                    "fuzzer_id": 7,
                    "status": "active",
                    "created_at": None,
                    "last_activity": None,
                    "engine_arguments": [],
                }
            ],
            fetchall_rows=[
                [{"fuzzer_id": 7, "status": "active", "max_coverage": 1.23}],
            ],
        )

        with patch.object(db.psycopg2, "connect", return_value=fake_conn):
            raw = json.loads(
                db.db_get_fuzzer_performance_summary_tool.execute_function({"fuzzer_id": "fuzzer-7"})
            )

        self.assertEqual(raw[0]["fuzzer_id"], 7)
        self.assertEqual(fake_conn.cursor_obj.executed[1][1], (7,))

    def test_db_list_programs_accepts_fuzzer_label(self):
        fake_conn = _FakeConn(
            fetchone_rows=[
                {
                    "fuzzer_id": 7,
                    "status": "active",
                    "created_at": None,
                    "last_activity": None,
                    "engine_arguments": [],
                }
            ],
            fetchall_rows=[
                [{"program_hash": "abc", "fuzzer_id": 7, "inserted_at": None}],
            ],
        )

        with patch.object(db.psycopg2, "connect", return_value=fake_conn):
            raw = json.loads(db.db_list_programs(limit=5, offset=2, fuzzer_id="fuzzer-7"))

        self.assertEqual(raw[0]["program_hash"], "abc")
        self.assertEqual(fake_conn.cursor_obj.executed[1][1], (7, 5, 2))

    def test_get_program_js_from_hash_lifts_fuzzil_from_database(self):
        program_b64 = base64.b64encode(b"serialized-fuzzil").decode("utf-8")
        fake_conn = _FakeConn(row={"program_base64": program_b64})

        with patch.object(db.psycopg2, "connect", return_value=fake_conn), patch.object(
            shared_tools,
            "run_fuzzilli_tool",
            return_value=subprocess.CompletedProcess(
                args=["FuzzILTool", "--liftToJS"],
                returncode=0,
                stdout="print(42);",
                stderr="",
            ),
        ):
            raw = json.loads(db.get_program_js_from_hash("hash"))

        self.assertEqual(raw["program_hash"], "hash")
        self.assertEqual(raw["javascript_code"], "print(42);")

    def test_get_program_js_from_hash_falls_back_to_raw_js_for_legacy_rows(self):
        js_code = "function f() { return 1; }\nprint(f());\n"
        program_b64 = base64.b64encode(js_code.encode("utf-8")).decode("utf-8")
        fake_conn = _FakeConn(row={"program_base64": program_b64})

        with patch.object(db.psycopg2, "connect", return_value=fake_conn), patch.object(
            shared_tools,
            "run_fuzzilli_tool",
            return_value=subprocess.CompletedProcess(
                args=["FuzzILTool", "--liftToJS"],
                returncode=1,
                stdout="",
                stderr="Failed to load program",
            ),
        ):
            raw = json.loads(db.get_program_js_from_hash("legacy"))

        self.assertEqual(raw["program_hash"], "legacy")
        self.assertEqual(raw["javascript_code"], js_code)

    def test_write_to_generate_folder_returns_file_path(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            generate_folder, "_get_varianal_folder", return_value=temp_dir
        ):
            raw = json.loads(generate_folder.write_to_generate_folder("validate_program.js", "print(1);\n"))

        self.assertEqual(raw["file_name"], "validate_program.js")
        self.assertEqual(raw["folder"], temp_dir)
        self.assertEqual(raw["file_path"], str(Path(temp_dir) / "validate_program.js"))

    def test_start_mi_debug_session_resolves_relative_generate_folder_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            js_path = Path(temp_dir) / "validate_program.js"
            js_path.write_text("print(1);\n")
            d8_path = Path(temp_dir) / "d8"
            d8_path.write_text("")

            with patch(
                "tools.EBG_tools._shared._get_varianal_folder", return_value=temp_dir
            ), patch.object(shared_tools, "D8_PATH", str(d8_path)), patch.object(
                shared_tools, "PygdbmiController", _FakeGdbController
            ):
                shared_tools.MI_CONTROLLER = None
                shared_tools.DEBUG_SESSION["js_path"] = ""
                result = shared_tools.start_mi_debug_session("validate_program.js", "--trace-opt")

            self.assertIn("-file-exec-and-symbols", result)
            self.assertIn(f"-environment-cd {shared_tools._runtime_artifact_dir(str(js_path.resolve()))}", result)
            self.assertEqual(shared_tools.DEBUG_SESSION["js_path"], str(js_path.resolve()))
            shared_tools.MI_CONTROLLER = None

    def test_trace_v8_analysis_writes_js_and_turbo_output_under_runtime_data(self):
        trace_dir = Path(db.TEMP_FUZZIL_PATH).parent / "trace_v8_test"
        trace_dir.mkdir(parents=True, exist_ok=True)

        def fake_run_d8(js_path, flags=None, timeout=90):
            self.assertTrue(str(Path(js_path).resolve()).startswith(str(trace_dir.resolve())))
            turbo_flag = next(flag for flag in flags if flag.startswith("--trace-turbo-path="))
            turbo_dir = Path(turbo_flag.split("=", 1)[1])
            self.assertTrue(str(turbo_dir.resolve()).startswith(str(trace_dir.resolve())))
            turbo_dir.mkdir(parents=True, exist_ok=True)
            (turbo_dir / "turbo-test.json").write_text("{\"ok\":true}")
            return subprocess.CompletedProcess(args=[js_path], returncode=0, stdout="trace-ok", stderr="")

        with patch.object(execution, "fetch_program_js_from_db", return_value="print(1);\n"), patch.object(
            execution, "_get_varianal_folder", return_value=str(trace_dir)
        ), patch.object(execution, "run_d8", side_effect=fake_run_d8):
            raw = json.loads(execution.trace_v8_analysis("deadbeef"))

        self.assertEqual(raw["js_file"], str((trace_dir / "deadbeef.js").resolve()))
        self.assertIn("turbo-test.json", raw["turbo_ir_files"])
        self.assertEqual(raw["stdout"], "trace-ok")


if __name__ == "__main__":
    unittest.main()
