#!/usr/bin/env python3

import os
from pathlib import Path
from typing import Dict


def _agentic_root() -> Path:
    return Path(__file__).resolve().parent


def _default_fuzzilli_root() -> Path:
    agentic_root = _agentic_root()
    if agentic_root.name == "Agentic_System" and agentic_root.parent.name == "Sources":
        return agentic_root.parent.parent
    return agentic_root.parent


def _default_d8_path() -> Path:
    return Path("/mnt/vdc/v8_vrig/v8/out/fuzzbuild/d8")


def _default_v8_src_from_d8(d8_path: str | Path | None) -> Path | None:
    if not d8_path:
        return None
    try:
        path = Path(d8_path).expanduser().resolve()
    except OSError:
        return None
    if path.name != "d8":
        return None
    for parent in path.parents:
        if parent.name == "out":
            candidate = parent.parent / "src"
            if candidate.is_dir():
                return candidate
            break
    return None

def load_keys_from_config(config_path: Path = None) -> Dict[str, str]:
    keys = {}
    
    if config_path is None:
        possible_paths = [
            Path(__file__).parent / "keys.cfg",
            Path(__file__).parent.parent / "keys.cfg",
            Path.cwd() / "keys.cfg",
            Path.cwd() / "Sources" / "Agentic_System" / "keys.cfg",
        ]
        
        for path in possible_paths:
            if path.exists():
                config_path = path
                break
        else:
            return keys
    
    if not config_path or not config_path.exists():
        return keys
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                
                if '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip()
                    if key and value:
                        keys[key] = value
    except Exception as e:
        import sys
        print(f"Error loading keys from {config_path}: {e}", file=sys.stderr)
        return keys
    
    return keys


def _config_or_env(name: str, default: str = "") -> str:
    keys = load_keys_from_config()
    value = keys.get(name, "")
    if value:
        return value
    env_value = os.getenv(name, "").strip()
    if env_value:
        return env_value
    return default


def get_d8_path() -> str:
    value = _config_or_env("D8_PATH", str(_default_d8_path()))
    return value.strip()


def get_v8_path() -> str:
    value = _config_or_env("V8_PATH", "")
    if value:
        return value.strip()
    derived = _default_v8_src_from_d8(get_d8_path())
    return str(derived) if derived else ""


def get_fuzzilli_path() -> str:
    value = _config_or_env("FUZZILLI_PATH", "")
    if value:
        return value.strip()
    return str(_default_fuzzilli_root())


def get_fuzzilli_tool_bin() -> str:
    value = _config_or_env("FUZZILLI_TOOL_BIN", "")
    if value:
        return value.strip()
    candidate = Path(get_fuzzilli_path()) / ".build" / "x86_64-unknown-linux-gnu" / "debug" / "FuzzILTool"
    return str(candidate) if candidate.exists() else ""


def get_runtime_paths() -> Dict[str, str]:
    paths = {
        "V8_PATH": get_v8_path(),
        "D8_PATH": get_d8_path(),
        "FUZZILLI_PATH": get_fuzzilli_path(),
        "FUZZILLI_TOOL_BIN": get_fuzzilli_tool_bin(),
    }
    return {key: value for key, value in paths.items() if value}


def apply_runtime_paths(target_env: Dict[str, str] | None = None, overwrite: bool = False) -> Dict[str, str]:
    env = target_env if target_env is not None else os.environ
    for key, value in get_runtime_paths().items():
        if overwrite or not str(env.get(key, "")).strip():
            env[key] = value
    return env

def get_openai_api_key() -> str:
    keys = load_keys_from_config()
    return keys.get('OPENAI_API_KEY', '')
    
def get_anthropic_api_key() -> str:
    keys = load_keys_from_config()
    return keys.get('ANTHROPIC_API_KEY', '')
    
def get_deepseek_api_key() -> str:
    keys = load_keys_from_config()
    key = keys.get('DEEPSEEK_API_KEY', '')
    if not key:
        key = os.getenv('DEEPSEEK_API_KEY', '')
    return key
    
def get_openrouter_api_key() -> str:
    keys = load_keys_from_config()
    key = keys.get('OPENROUTER_API_KEY', '')
    if not key:
        key = os.getenv('OPENROUTER_API_KEY', '')
    return key
