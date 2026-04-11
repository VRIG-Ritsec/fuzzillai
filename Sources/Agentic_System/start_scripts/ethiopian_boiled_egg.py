#!/usr/bin/env python3
import os
import sys
import subprocess
from pathlib import Path

_agentic_root = Path(__file__).resolve().parents[1]
if str(_agentic_root) not in sys.path:
    sys.path.insert(0, str(_agentic_root))

from venv_site_packages import add_fuzzillai_repo_venv_site_packages

add_fuzzillai_repo_venv_site_packages()

import argparse
import functools
import json
import logging
import site
from datetime import datetime
import pytz

import config_loader as config_loader
config_loader.apply_runtime_paths()
from startup_checks import collect_runtime_preflight, format_preflight_report
from agents.EBG_crash import EBG_Crash
from agents.EBG_plateau import EBG_Plateau
from agent_logging import configure_process_logging
from config_loader import get_openai_api_key, get_anthropic_api_key, get_deepseek_api_key, get_openrouter_api_key

def _model(model_id, api_key):
    return type('_Model', (), {'model_id': model_id, 'api_key': api_key})()

logger = logging.getLogger("boiled_eggs")
if not logger.handlers:
    logger.addHandler(logging.NullHandler())
logger.propagate = False
logger.disabled = True
est_timezone = pytz.timezone("America/New_York")

DEEPSEEK_MODEL_ID = "deepseek"
OPENAI_MODEL_ID = "gpt-5.4"
OPENROUTER_MODEL_ID = "openai/gpt-5-mini"


def _regressions_json_candidates(agentic_root: Path) -> list[Path]:
    candidates = []
    raw = os.getenv("REGRESSIONS_JSON")
    if raw:
        candidates.append(Path(raw).expanduser())
    candidates.extend(
        [
            agentic_root / "regressions" / "regressions.json",
            agentic_root / "regressions.json",
        ]
    )
    return [path.resolve() for path in candidates if path.exists()]


def _regressions_zst_candidates(agentic_root: Path) -> list[Path]:
    candidates = []
    raw = os.getenv("REGRESSIONS_JSON_ZST")
    if raw:
        candidates.append(Path(raw).expanduser())
    candidates.extend(
        [
            agentic_root / "regressions" / "regressions.json.zst",
            agentic_root / "regressions.json.zst",
        ]
    )
    return [path.resolve() for path in candidates if path.exists()]


def _ensure_regressions_index(agentic_root: Path) -> Path | None:
    json_candidates = _regressions_json_candidates(agentic_root)
    if json_candidates:
        return max(json_candidates, key=lambda path: path.stat().st_mtime)

    zst_candidates = _regressions_zst_candidates(agentic_root)
    if not zst_candidates:
        return None

    zst_path = max(zst_candidates, key=lambda path: path.stat().st_mtime)
    json_path = zst_path.with_suffix("")
    try:
        subprocess.run(
            ["zstd", "-d", "-f", str(zst_path), "-o", str(json_path)],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        logger.warning(f"Could not decompress regressions index {zst_path}: {e}")
        return None
    return json_path


class EthiopianBoiledEgg:
    def __init__(self, mode: str = "Crash", fuzzer_id: str = "fuzzer-1", crash_program_hash: str = None):
        logger.info("Initializing EthiopianBoiledEgg")
        self.openai_api_key = get_openai_api_key()
        self.anthropic_api_key = get_anthropic_api_key()
        self.deepseek_api_key = get_deepseek_api_key()
        self.openrouter_api_key = get_openrouter_api_key()

        if not self.openai_api_key:
            raise RuntimeError("EBG now requires OPENAI_API_KEY for gpt-5-mini/gpt-5.4 execution")

        os.environ["OPENAI_API_KEY"] = self.openai_api_key
        if self.deepseek_api_key:
            os.environ["DEEPSEEK_API_KEY"] = self.deepseek_api_key
        if self.openrouter_api_key:
            os.environ["OPENROUTER_API_KEY"] = self.openrouter_api_key
            os.environ["OPENROUTER_API_URL"] = "https://openrouter.ai/api/v1/chat/completions"

        key, model_id = self._select_model_and_key()
        self.model = _model(model_id, key)
        logger.info("System is running in %s mode", mode)
        if mode == "Crash":
            self.system = EBG_Crash(self.model, api_key=key, anthropic_api_key=self.anthropic_api_key, crash_program_hash=crash_program_hash)
        elif mode == "Plateau":
            self.system = EBG_Plateau(self.model, api_key=key, anthropic_api_key=self.anthropic_api_key, fuzzer_id=fuzzer_id)

    def _select_model_and_key(self):
        if self.openai_api_key:
            return self.openai_api_key, OPENAI_MODEL_ID
        if self.deepseek_api_key:
            return self.deepseek_api_key, DEEPSEEK_MODEL_ID
        if self.openrouter_api_key:
            return self.openrouter_api_key, OPENROUTER_MODEL_ID
        return None, OPENAI_MODEL_ID


def run(force_logging: bool = True):
    # Add the previous parent directory (Sources) to site dirs, preserving old behavior
    site.addsitedir(Path(__file__).resolve().parents[2])
    # smolagent-fork

    parser = argparse.ArgumentParser(description="Ethiopian Boiled Eggs agentic system")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging to fog logs")
    parser.add_argument(
        "--mode",
        choices=["Crash", "Plateau"],
        default="Crash",
        help="Agent mode: 'Crash' (default) or 'Plateau'",
    )
    parser.add_argument(
        "--fuzzer-id",
        default="fuzzer-1",
        help="Fuzzer ID to pass to the Plateau agent (default: fuzzer-1). "
             "Only used when --mode=Plateau.",
    )
    parser.add_argument(
        "--crash-hash",
        default=None,
        help="Program hash of the crash to analyse. Only used when --mode=Crash.",
    )
    args = parser.parse_args()
    # force logging
    args.debug = force_logging

    if args.debug:
        system_name = "ebg_crash" if args.mode == "Crash" else "ebg_plateau"
        log_path = configure_process_logging(system_name, "ethiopian_boiled_egg", logger=logger)
        logger.info(f"Writing logs to {log_path}")

    logger.info("something funny")
    logger.info(f"time: {datetime.now(est_timezone)}")
    logger.info(f"mode: {args.mode}  fuzzer_id: {args.fuzzer_id}  crash_hash: {args.crash_hash}")

    errors, warnings = collect_runtime_preflight(check_debugger=(args.mode == "Plateau"))
    for warning in warnings:
        logger.warning(f"Runtime preflight warning: {warning}")
    if errors:
        raise RuntimeError(f"Runtime preflight failed before agent startup:\n{format_preflight_report(errors, warnings)}")

    a = EthiopianBoiledEgg(
        mode=args.mode,
        fuzzer_id=args.fuzzer_id,
        crash_program_hash=args.crash_hash,
    )

    if args.mode == "Crash":
        agentic_root = Path(__file__).resolve().parents[1]
        regressions_index = _ensure_regressions_index(agentic_root)
        if regressions_index is not None:
            logger.info(f"Using regressions index at {regressions_index}")
        elif (agentic_root / "regressions").exists():
            logger.info("No regressions json index found; tools will fall back to raw regression files")
        else:
            logger.warning("No regression corpus found under the repository root")

    a.system.start_system()


if __name__ == "__main__":
    sys.exit(run())
