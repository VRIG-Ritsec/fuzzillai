import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import startup_checks


class TestStartupChecks(unittest.TestCase):
    def test_collect_runtime_preflight_reports_missing_required_paths(self):
        with patch.dict(os.environ, {}, clear=True), patch(
            "startup_checks.get_runtime_paths", return_value={}
        ), patch("startup_checks.apply_runtime_paths", return_value={}):
            errors, warnings = startup_checks.collect_runtime_preflight(check_debugger=False)

        self.assertEqual(warnings, [])
        self.assertIn("V8_PATH is not set", errors)
        self.assertIn("D8_PATH is not set", errors)
        self.assertIn("FUZZILLI_PATH is not set", errors)
        self.assertIn("FUZZILLI_TOOL_BIN is not set", errors)

    def test_collect_runtime_preflight_warns_when_debugger_support_is_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            v8_dir = temp_path / "src"
            v8_dir.mkdir()
            d8_path = temp_path / "d8"
            d8_path.write_text("")
            fuzzilli_dir = temp_path / "fuzzilli"
            fuzzilli_dir.mkdir()
            fuzzilli_tool = temp_path / "FuzzILTool"
            fuzzilli_tool.write_text("")

            env = {
                "V8_PATH": str(v8_dir),
                "D8_PATH": str(d8_path),
                "FUZZILLI_PATH": str(fuzzilli_dir),
                "FUZZILLI_TOOL_BIN": str(fuzzilli_tool),
            }

            with patch.dict(os.environ, env, clear=True), patch(
                "startup_checks.importlib.util.find_spec", return_value=None
            ), patch("startup_checks.shutil.which", return_value=None):
                errors, warnings = startup_checks.collect_runtime_preflight(check_debugger=True)

        self.assertEqual(errors, [])
        self.assertIn(
            "pygdbmi is not installed; MI debugger validation will be unavailable",
            warnings,
        )
        self.assertIn(
            "gdb is not available on PATH; breakpoint-driven validation will be unavailable",
            warnings,
        )

    def test_collect_runtime_preflight_can_downgrade_specific_missing_paths_to_warnings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            v8_dir = temp_path / "src"
            v8_dir.mkdir()
            fuzzilli_dir = temp_path / "fuzzilli"
            fuzzilli_dir.mkdir()
            fuzzilli_tool = temp_path / "FuzzILTool"
            fuzzilli_tool.write_text("")

            env = {
                "V8_PATH": str(v8_dir),
                "D8_PATH": str(temp_path / "missing-d8"),
                "FUZZILLI_PATH": str(fuzzilli_dir),
                "FUZZILLI_TOOL_BIN": str(fuzzilli_tool),
            }

            with patch.dict(os.environ, env, clear=True):
                errors, warnings = startup_checks.collect_runtime_preflight(
                    check_debugger=False,
                    warn_only_vars=("D8_PATH",),
                )

        self.assertEqual(errors, [])
        self.assertIn("D8_PATH does not point to an existing file", warnings[0])


if __name__ == "__main__":
    unittest.main()
