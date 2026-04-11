#!/usr/bin/env python3

import logging
import os
import sys
from pathlib import Path

_AGENTIC_ROOT = Path(__file__).resolve().parent
_AGENT_LOG_ROOT = _AGENTIC_ROOT / "runtime_data" / "agent-logs"

SYSTEM_LOG_DIRS = {
    "ebg_crash": "ebg_crash_logs",
    "ebg_plateau": "ebg_plateau_logs",
    "fog": "fog_logs",
    "services": "service_logs",
}


class StreamToLogger:
    def __init__(self, log_fn):
        self.log_fn = log_fn
        self._buffer = ""

    def write(self, message):
        if not isinstance(message, str):
            message = message.decode("utf-8", errors="ignore")
        self._buffer += message
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self.log_fn(line)

    def flush(self):
        if self._buffer:
            self.log_fn(self._buffer)
            self._buffer = ""

    def isatty(self):
        return False


def get_agent_log_root() -> Path:
    _AGENT_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    return _AGENT_LOG_ROOT


def get_system_log_dir(system_name: str) -> Path:
    dirname = SYSTEM_LOG_DIRS.get(system_name, system_name)
    path = get_agent_log_root() / dirname
    path.mkdir(parents=True, exist_ok=True)
    return path


def next_log_path(system_name: str, stem: str, suffix: str = ".log") -> Path:
    log_dir = get_system_log_dir(system_name)
    plain_log = log_dir / f"{stem}{suffix}"
    if not plain_log.exists():
        return plain_log

    latest_num = 0
    for existing in log_dir.glob(f"{stem}*{suffix}"):
        if existing.name == plain_log.name:
            continue
        extra = existing.stem[len(stem):]
        if extra.isdigit():
            latest_num = max(latest_num, int(extra))
    return log_dir / f"{stem}{latest_num + 1}{suffix}"


def configure_plain_file_logger(logger: logging.Logger, log_path: Path, level: int = logging.INFO) -> logging.Logger:
    logger.handlers.clear()
    file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(file_handler)
    logger.setLevel(level)
    logger.propagate = False
    logger.disabled = False
    return logger


def enable_named_logger_from_env(logger_name: str, level: int = logging.INFO) -> logging.Logger:
    log_file = os.getenv("IKA_AGENT_LOG_FILE", "").strip()
    logger = logging.getLogger(logger_name)
    if not log_file:
        logger.disabled = True
        return logger

    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    return configure_plain_file_logger(logger, log_path, level=level)


def configure_process_logging(
    system_name: str,
    stem: str,
    logger: logging.Logger | None = None,
    redirect_stdio: bool = True,
    level: int = logging.INFO,
) -> Path:
    log_path = next_log_path(system_name, stem)
    active_logger = logger or logging.getLogger(f"{system_name}.{stem}")
    configure_plain_file_logger(active_logger, log_path, level=level)

    os.environ["IKA_AGENT_SYSTEM"] = system_name
    os.environ["IKA_AGENT_LOG_DIR"] = str(log_path.parent)
    os.environ["IKA_AGENT_LOG_FILE"] = str(log_path)

    if redirect_stdio:
        sys.stdout = StreamToLogger(active_logger.info)
        sys.stderr = StreamToLogger(active_logger.error)

    return log_path
