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

_ikacore_src = _agentic_root / "IkaCore" / "src"
if _ikacore_src.exists() and str(_ikacore_src) not in sys.path:
    sys.path.insert(0, str(_ikacore_src))

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
from agents.FoG import Father
from agent_logging import configure_process_logging
from config_loader import get_openai_api_key, get_anthropic_api_key, get_deepseek_api_key

def _model(model_id, api_key):
    return type('_Model', (), {'model_id': model_id, 'api_key': api_key})()

logger = logging.getLogger("rises_the_fog")
if not logger.handlers:
    logger.addHandler(logging.NullHandler())
logger.propagate = False
logger.disabled = True
est_timezone = pytz.timezone("America/New_York")

BASE_MODEL_ID = "deepseek-chat"
MANAGER_MODEL_ID = os.getenv("FOG_MANAGER_MODEL", "gpt-5.4")


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


class FatherOfGod:
    def __init__(self):
        logger.info("Initializing FatherOfGod")
        self.openai_api_key = get_openai_api_key()
        self.anthropic_api_key = get_anthropic_api_key()
        self.deepseek_api_key = get_deepseek_api_key()

        if not self.openai_api_key:
            raise RuntimeError("FoG now requires OPENAI_API_KEY for gpt-5-mini/gpt-5.4 execution")

        if self.openai_api_key:
            os.environ["OPENAI_API_KEY"] = self.openai_api_key
        if self.deepseek_api_key:
            os.environ["DEEPSEEK_API_KEY"] = self.deepseek_api_key

        key = self.openai_api_key
        self.model = _model(MANAGER_MODEL_ID, key)
        self.system = Father(self.model, api_key=key, anthropic_api_key=self.anthropic_api_key)
        # self.ebg = EBG(self.model, api_key=self.openai_api_key, anthropic_api_key=self.anthropic_api_key)


def run(force_logging: bool = True):
    # Add the previous parent directory (Sources) to site dirs, preserving old behavior
    site.addsitedir(Path(__file__).resolve().parents[2])
    # smolagent-fork

    parser = argparse.ArgumentParser(description="Rise the FoG agentic system")
    parser.add_argument("checkpoint_uid", nargs="?", default=None, help="Checkpoint UID to resume from")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging to fog logs")
    args = parser.parse_args()
    # force logging
    args.debug = force_logging

    if args.debug:
        log_path = configure_process_logging("fog", "rises_the_fog", logger=logger)
        logger.info(f"Writing logs to {log_path}")

    logger.info("I must go in; the fog is rising")
    logger.info(f"time: {datetime.now(est_timezone)}")
    errors, warnings = collect_runtime_preflight(check_debugger=False)
    for warning in warnings:
        logger.warning(f"Runtime preflight warning: {warning}")
    if errors:
        raise RuntimeError(f"Runtime preflight failed before agent startup:\n{format_preflight_report(errors, warnings)}")
    a = FatherOfGod()
    agentic_root = Path(__file__).resolve().parents[1]
    regressions_index = _ensure_regressions_index(agentic_root)
    if regressions_index is not None:
        logger.info(f"Using regressions index at {regressions_index}")
    elif (agentic_root / "regressions").exists():
        logger.info("No regressions json index found; FoG tools will fall back to raw regression files")
    else:
        logger.warning("No regression corpus found under the repository root")
    checkpoint_uid = getattr(args, "checkpoint_uid", None)
    if checkpoint_uid:
        logger.info(f"Resuming from checkpoint: {checkpoint_uid}")
    a.system.start_system(checkpoint_uid=checkpoint_uid)


if __name__ == "__main__":
    sys.exit(run())
