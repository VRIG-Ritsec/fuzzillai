import unittest
from pathlib import Path


class TestEBGPlateauPrompts(unittest.TestCase):
    def test_db_analyzer_prompt_references_real_tools(self):
        prompt_path = Path(__file__).resolve().parent.parent / "prompts" / "EBG-plateau-prompts" / "db_analyzer.txt"
        prompt = prompt_path.read_text(encoding="utf-8")

        self.assertNotIn("db_get_program_coverage_mapping", prompt)
        self.assertIn("db_list_fuzzers", prompt)
        self.assertIn("db_resolve_fuzzer_id", prompt)
        self.assertIn("db_query", prompt)
        self.assertIn("numeric DB fuzzer id", prompt)

    def test_runtime_and_manager_prompts_use_queue_and_numeric_id_guidance(self):
        prompts_dir = Path(__file__).resolve().parent.parent / "prompts" / "EBG-plateau-prompts"
        runtime_prompt = (prompts_dir / "runtime_analyzer.txt").read_text(encoding="utf-8")
        manager_prompt = (prompts_dir / "plateau_manager.txt").read_text(encoding="utf-8")

        self.assertIn('"task":', runtime_prompt)
        self.assertNotIn("\n    TASK:", runtime_prompt)
        self.assertIn("numeric DB fuzzer id", runtime_prompt)
        self.assertIn("db_resolve_fuzzer_id", runtime_prompt)
        self.assertIn("generated corpus inbox", manager_prompt)
        self.assertIn("numeric DB fuzzer id", manager_prompt)

    def test_plateau_agent_source_does_not_use_crash_descriptions(self):
        agent_path = Path(__file__).resolve().parent.parent / "agents" / "EBG_plateau.py"
        source = agent_path.read_text(encoding="utf-8")

        self.assertNotIn('description="L2 Worker responsible for debugging a crash"', source)
        self.assertNotIn('description="L1 Manager responsible for generating JavaScript program seeds from a crash PoC"', source)
        self.assertIn("generated corpus inbox", (Path(__file__).resolve().parent.parent / "prompts" / "EBG-plateau-prompts" / "JS_generator.txt").read_text(encoding="utf-8"))
        self.assertNotIn("latest programs from the database", source)
        self.assertIn("current PostgreSQL schema/init SQL", source)


if __name__ == "__main__":
    unittest.main()
