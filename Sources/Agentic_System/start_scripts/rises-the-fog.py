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

# Ensure the Agentic_System package root and IkaCore src are on sys.path
_agentic_root = Path(__file__).resolve().parents[1]
if str(_agentic_root) not in sys.path:
    sys.path.insert(0, str(_agentic_root))
_ikacore_src = _agentic_root / "IkaCore" / "src"
if _ikacore_src.exists() and str(_ikacore_src) not in sys.path:
    sys.path.insert(0, str(_ikacore_src))

import config_loader as config_loader
from agents.FoG import Father
from config_loader import get_openai_api_key, get_anthropic_api_key, get_deepseek_api_key

def _model(model_id, api_key):
    return type('_Model', (), {'model_id': model_id, 'api_key': api_key})()

logger = logging.getLogger("rises_the_fog")
if not logger.handlers:
    logger.addHandler(logging.NullHandler())
logger.propagate = False
logger.disabled = True
est_timezone = pytz.timezone('US/Eastern')

BASE_MODEL_ID = "deepseek-chat"

# Prefer the project's virtualenv site-packages if present, so RAG tools are importable
try:
    # Repo root (unchanged despite this file moving one level deeper)
    _root = Path(__file__).resolve().parents[3]
    _venv_site = _root / ".venv" / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    if _venv_site.exists():
        site.addsitedir(str(_venv_site))
except Exception:
    pass


class FatherOfGod:
    def __init__(self):
        logger.info("Initializing FatherOfGod")
        self.openai_api_key = get_openai_api_key()
        self.anthropic_api_key = get_anthropic_api_key()
        self.deepseek_api_key = get_deepseek_api_key()

        if self.deepseek_api_key:
            os.environ["DEEPSEEK_API_KEY"] = self.deepseek_api_key

        key = self.deepseek_api_key or self.openai_api_key
        self.model = _model(BASE_MODEL_ID, key)
        self.system = Father(self.model, api_key=key, anthropic_api_key=self.anthropic_api_key)
        # self.ebg = EBG(self.model, api_key=self.openai_api_key, anthropic_api_key=self.anthropic_api_key)


def run(force_logging: bool = True):
    # Add the previous parent directory (Sources) to site dirs, preserving old behavior
    site.addsitedir(Path(__file__).resolve().parents[2])
    # smolagent-fork

    parser = argparse.ArgumentParser(description="Rise the FoG agentic system")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging to fog logs")
    args = parser.parse_args()
    # force logging
    args.debug = force_logging

    if args.debug:
        # Logs live under Agentic_System/agents/fog_logs even though this file moved into start_scripts
        agentic_root = Path(__file__).resolve().parents[1]
        log_dir = agentic_root / 'agents' / 'fog_logs'
        log_dir.mkdir(parents=True, exist_ok=True)
        latest_num = 0
        if os.path.exists(log_dir / 'rises_the_fog.log'):
            for root, dirs, files in os.walk(log_dir, topdown=False):
                for name in files:
                    if name.endswith('.log'):
                        if "rises_the_fog.log" not in name:
                            num = int(name[len('rises_the_fog'):-len('.log')])
                            if num > latest_num:
                                latest_num = num
            log_path = str(log_dir / f'rises_the_fog{latest_num + 1}.log')
        else:
            log_path = str(log_dir / f'rises_the_fog.log')

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
        os.environ["FOG_DEBUG"] = "1"

    logger.info("I must go in; the fog is rising")
    logger.info(f"time: {datetime.now(est_timezone)}")
    a = FatherOfGod()
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

