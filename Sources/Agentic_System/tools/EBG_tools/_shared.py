"""
EBG tools shared helpers and constants.
"""

import os
import re
import json
import random
import datetime
from pathlib import Path
from decimal import Decimal
from collections import OrderedDict

POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")
POSTGRES_DB = os.getenv("POSTGRES_DB", "fuzzilli_master")
POSTGRES_USER = os.getenv("POSTGRES_USER", "fuzzilli")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "fuzzilli123")

TEMP_FUZZIL_PATH = "/tmp/temp-base64-fuzzil.fzil"
_RUNTIME_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "runtime_data"

_GENERATE_FOLDER_HASHS = None
_DB_QUERY_CACHE = OrderedDict()
_DB_QUERY_CACHE_MAX = 128


def _get_varianal_folder():
    global _GENERATE_FOLDER_HASHS
    if _GENERATE_FOLDER_HASHS is None:
        import hashlib
        folder_name = (
            "varianal_"
            + hashlib.sha256(datetime.datetime.now().isoformat().encode("utf-8")).hexdigest()
            + "_"
            + str(random.randint(1, 1000000))
        )
        _GENERATE_FOLDER_HASHS = str(_RUNTIME_DATA_DIR / folder_name)
    return _GENERATE_FOLDER_HASHS


def json_serial(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")


def _normalize_sql_whitespace(query: str) -> str:
    return " ".join((query or "").strip().split())


def _is_read_only_sql(query: str) -> bool:
    normalized = _normalize_sql_whitespace(query).lower()
    return (
        normalized.startswith("select ")
        or normalized.startswith("with ")
        or normalized.startswith("explain ")
    )


def _build_cache_key(query: str, exec_params: tuple) -> str:
    return f"{query}||{json.dumps(exec_params, default=str, ensure_ascii=True)}"


def _cache_get(cache_key: str):
    if cache_key not in _DB_QUERY_CACHE:
        return None
    _DB_QUERY_CACHE.move_to_end(cache_key)
    return _DB_QUERY_CACHE[cache_key]


def _cache_set(cache_key: str, value: str) -> None:
    _DB_QUERY_CACHE[cache_key] = value
    _DB_QUERY_CACHE.move_to_end(cache_key)
    while len(_DB_QUERY_CACHE) > _DB_QUERY_CACHE_MAX:
        _DB_QUERY_CACHE.popitem(last=False)


def _validate_and_prepare_sql(query: str, params: list) -> tuple:
    query = (query or "").strip()
    params = [] if params is None else params
    pg_matches = re.findall(r"\$(\d+)", query)
    percent_placeholder_count = len(re.findall(r"(?<!%)%s", query))

    if pg_matches and percent_placeholder_count:
        return None, None, "Database error: mixed placeholder styles are not allowed (use either $n or %s)."

    if pg_matches:
        positions = [int(match) for match in pg_matches]
        max_pos = max(positions)
        if len(params) != max_pos:
            return (
                None,
                None,
                f"Database error: positional placeholder mismatch (expected exactly {max_pos} params for $ placeholders, got {len(params)}).",
            )
        normalized_query = re.sub(r"\$\d+", "%s", query)
        exec_params = tuple(params[pos - 1] for pos in positions)
        return normalized_query, exec_params, None

    if percent_placeholder_count:
        if len(params) != percent_placeholder_count:
            return (
                None,
                None,
                f"Database error: placeholder mismatch (expected exactly {percent_placeholder_count} params for %s placeholders, got {len(params)}).",
            )
        return query, tuple(params), None

    if len(params) > 0:
        return None, None, "Database error: query has no placeholders but params were provided."
    return query, tuple(), None
