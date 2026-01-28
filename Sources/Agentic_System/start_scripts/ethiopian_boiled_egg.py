#!/usr/bin/env python3
import argparse
import functools
import json
import os
import sys
import subprocess
from pathlib import Path
import logging
from datetime import datetime
import pytz

import site

# Ensure the Agentic_System package root is on sys.path so sibling modules import correctly
_agentic_root = Path(__file__).resolve().parents[1]
if str(_agentic_root) not in sys.path:
    sys.path.insert(0, str(_agentic_root))

import config_loader as config_loader
from agents.EBG_crash import EBG_Crash
from agents.EBG_plateau import EBG_Plateau
from config_loader import get_openai_api_key, get_anthropic_api_key, get_deepseek_api_key

def _model(model_id, api_key):
    return type('_Model', (), {'model_id': model_id, 'api_key': api_key})()

logger = logging.getLogger("boiled_eggs")
if not logger.handlers:
    logger.addHandler(logging.NullHandler())
logger.propagate = False
logger.disabled = True
est_timezone = pytz.timezone('US/Eastern')

BASE_MODEL_ID = "deepseek"

# Prefer the project's virtualenv site-packages if present, so tools like chromadb are importable
try:
    # Repo root (unchanged despite this file moving one level deeper)
    _root = Path(__file__).resolve().parents[3]
    _venv_site = _root / ".venv" / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    if _venv_site.exists():
        site.addsitedir(str(_venv_site))
except Exception:
    pass


class EthiopianBoiledEgg:
    def __init__(self, mode: str = "Crash", fuzzer_id: str = "fuzzer-1", crash_program_hash: str = None):
        logger.info("Initializing EthiopianBoiledEgg")
        self.openai_api_key = get_openai_api_key()
        self.anthropic_api_key = get_anthropic_api_key()
        self.deepseek_api_key = get_deepseek_api_key()

        if self.deepseek_api_key:
            os.environ["DEEPSEEK_API_KEY"] = self.deepseek_api_key

        key = self.deepseek_api_key or self.openai_api_key
        self.model = _model(BASE_MODEL_ID, key)
        print("System is running in " + mode + " mode")
        if mode == "Crash":
            self.system = EBG_Crash(self.model, api_key=key, anthropic_api_key=self.anthropic_api_key, crash_program_hash=crash_program_hash)
        elif mode == "Plateau":
            self.system = EBG_Plateau(self.model, api_key=key, anthropic_api_key=self.anthropic_api_key, fuzzer_id=fuzzer_id)


def run(force_logging: bool = True):
    # Add the previous parent directory (Sources) to site dirs, preserving old behavior
    site.addsitedir(Path(__file__).resolve().parents[2])
    # smolagent-fork

    parser = argparse.ArgumentParser(description="Ethiopian Boiled Eggs agentic system")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging to fog logs")
    args = parser.parse_args()
    # force logging
    args.debug = force_logging

    if args.debug:
        # Logs live under Agentic_System/agents/ebg_logs even though this file moved into start_scripts
        agentic_root = Path(__file__).resolve().parents[1]
        log_dir = agentic_root / 'agents' / 'ebg_logs'
        log_dir.mkdir(parents=True, exist_ok=True)
        latest_num = 0
        if os.path.exists(log_dir / 'ethiopian_boiled_egg.log'):
            for root, dirs, files in os.walk(log_dir, topdown=False):
                for name in files:
                    if name.endswith('.log'):
                        if "ethiopian_boiled_egg.log" not in name:
                            num = int(name[len('ethiopian_boiled_egg'):-len('.log')])
                            if num > latest_num:
                                latest_num = num
            log_path = str(log_dir / f'ethiopian_boiled_egg{latest_num + 1}.log')
        else:
            log_path = str(log_dir / f'ethiopian_boiled_egg.log')

        if os.path.exists(log_path):
            print(f"Log file already exists: {log_path}")

        # Configure logger to write messages as-is (no prefixes) for 1:1 capture
        logger.handlers.clear()
        file_handler = logging.FileHandler(log_path, mode='a', encoding='utf-8')
        file_handler.setFormatter(logging.Formatter('%(message)s'))
        logger.addHandler(file_handler)
        logger.setLevel(logging.INFO)
        logger.disabled = False

        class _StreamToLogger:
            def __init__(self, log_fn):
                self.log_fn = log_fn
                self._buffer = ''

            def write(self, message):
                if not isinstance(message, str):
                    message = message.decode('utf-8', errors='ignore')
                self._buffer += message
                while '\n' in self._buffer:
                    line, self._buffer = self._buffer.split('\n', 1)
                    self.log_fn(line)

            def flush(self):
                if self._buffer:
                    self.log_fn(self._buffer)
                    self._buffer = ''

            def isatty(self):
                return False

        sys.stdout = _StreamToLogger(logger.info)
        sys.stderr = _StreamToLogger(logger.error)

        # Signal BaseAgent to enable its own logging lazily and ensure directory exists
        os.environ["EBG_DEBUG"] = "1"

    logger.info("something funny")
    logger.info(f"time: {datetime.now(est_timezone)}")
    a = EthiopianBoiledEgg(mode="Plateau")
    path = os.path.join(os.getenv('FUZZILLI_PATH', ''), "Sources", "Agentic_System")
    regressions_dir = os.path.join(path, "regressions")
    if not os.path.exists(os.path.join(regressions_dir, "regressions.json")):
        try:
            subprocess.run(["unzstd", os.path.join(regressions_dir, "regressions.json.zst")], check=True)
            # unzstd regressions.json.zst
        except subprocess.CalledProcessError as e:
            logger.error(f"Error decompressing regressions.json.zst: {e}")
            exit(1)
        else:
            logger.info("Regressions.json decompressed successfully")
    a.system.start_system()


if __name__ == "__main__":
    sys.exit(run())

