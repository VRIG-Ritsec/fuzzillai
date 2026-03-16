from __future__ import annotations

import os
from pathlib import Path


def rag_root() -> Path:
    return Path(__file__).resolve().parent


def metadata_root() -> Path:
    base = os.getenv("RAG_BASE_DIR") or os.getenv("RAG_DB_DIR")
    if base:
        return Path(base).expanduser()
    return rag_root() / "meta_data"


def data_sources_root() -> Path:
    base = os.getenv("RAG_DATA_SOURCES_DIR")
    if base:
        return Path(base).expanduser()
    return rag_root() / "data_sources"


def knowledge_docs_root() -> Path:
    base = os.getenv("KNOWLEDGE_DOCS_DIR")
    if base:
        return Path(base).expanduser()
    return data_sources_root() / "knowlage_docs"


def chromium_issues_json() -> Path:
    raw = os.getenv("CHROMIUM_ISSUES_JSON")
    if raw:
        return Path(raw).expanduser()
    return knowledge_docs_root() / "chromium_issues" / "chromium_issues_from_tracker.json"


def util_scripts_root() -> Path:
    return rag_root() / "util-scripts"

