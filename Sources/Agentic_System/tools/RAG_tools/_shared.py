"""
RAG tools shared helpers: tokenization, BM25, RRF, rerank, formatting.
"""

import os
import re
import sqlite3
import inspect
from pathlib import Path
from typing import List, Dict, Tuple

_tools_dir = Path(__file__).resolve().parent.parent
_agentic_dir = _tools_dir.parent

import sys
if str(_agentic_dir) not in sys.path:
    sys.path.insert(0, str(_agentic_dir))

from RAG.paths import metadata_root

try:
    import numpy as np
    import faiss
    import pickle
    os.environ.setdefault("ACCELERATE_USE_CPU", "1")
    os.environ.setdefault("ACCELERATE_USE_META_DEVICE", "0")
    from sentence_transformers import SentenceTransformer
    FAISS_AVAILABLE = True
except ImportError:
    FAISS_AVAILABLE = False


_BM25_CONTENT_CACHE: Dict[Tuple[str, str], str] = {}


def _rag_base_dir() -> Path:
    base = metadata_root()
    if base.exists():
        return base
    return _agentic_dir / "RAG" / "meta_data"


def _tokenize(text: str) -> List[str]:
    return re.findall(r"[A-Za-z0-9_]{2,}", text.lower())


def _sanitize_bm25_query(query: str) -> str:
    tokens = _tokenize(query)
    if not tokens:
        return ""
    return " OR ".join(tokens[:16])


def _bm25_search(db_path: Path, query: str, top_k: int) -> List[Tuple[str, float]]:
    if not db_path.exists():
        return []
    query = query.strip()
    if not query:
        return []
    try:
        conn = sqlite3.connect(str(db_path))
    except sqlite3.Error:
        return []
    try:
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.execute(
                "SELECT doc_id, bm25(docs) AS score FROM docs WHERE docs MATCH ? ORDER BY score LIMIT ?",
                (query, int(top_k)),
            )
        except sqlite3.OperationalError:
            safe_query = _sanitize_bm25_query(query)
            if not safe_query:
                return []
            try:
                cur = conn.execute(
                    "SELECT doc_id, bm25(docs) AS score FROM docs WHERE docs MATCH ? ORDER BY score LIMIT ?",
                    (safe_query, int(top_k)),
                )
            except sqlite3.OperationalError:
                return []
        return [(row["doc_id"], float(row["score"])) for row in cur.fetchall()]
    finally:
        conn.close()


def _lookup_bm25_content(db_path: Path, doc_id: str) -> str:
    if not db_path.exists() or not doc_id:
        return ""
    cache_key = (str(db_path), doc_id)
    cached = _BM25_CONTENT_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        conn = sqlite3.connect(str(db_path))
    except sqlite3.Error:
        return ""
    try:
        cur = conn.execute("SELECT content FROM docs WHERE doc_id = ?", (doc_id,))
        row = cur.fetchone()
        content = str(row[0]) if row and row[0] is not None else ""
    except sqlite3.Error:
        content = ""
    finally:
        conn.close()
    _BM25_CONTENT_CACHE[cache_key] = content
    return content


def _score_to_similarity(score: float) -> float:
    return 1.0 / (1.0 + abs(float(score)))


def _load_sentence_transformer(model_name: str):
    if not FAISS_AVAILABLE:
        return None
    kwargs = {"device": "cpu"}
    try:
        if "local_files_only" in inspect.signature(SentenceTransformer.__init__).parameters:
            kwargs["local_files_only"] = True
    except Exception:
        pass
    try:
        model = SentenceTransformer(model_name, **kwargs)
    except Exception:
        return None
    try:
        model = model.to("cpu")
    except Exception:
        pass
    if hasattr(model, "eval"):
        model.eval()
    return model


def _strip_embedded_context(doc: Dict[str, object], content: str) -> str:
    if not content:
        return ""
    context = str(doc.get("context") or "").strip()
    if context and content.startswith(context):
        stripped = content[len(context):].lstrip("\n")
        if stripped:
            return stripped
    if str(doc.get("source") or "").lower() == "v8_source" and content.startswith("Topic:"):
        _, sep, remainder = content.partition("\n\n")
        if sep and remainder.strip():
            return remainder
    return content


def _resolve_doc_content(doc: Dict[str, object], bm25_db_path: Path | None = None) -> str:
    content = str(doc.get("content") or "")
    if content:
        return content

    doc_id = str(doc.get("doc_id") or "")
    if bm25_db_path is not None:
        content = _lookup_bm25_content(bm25_db_path, doc_id)
        if content:
            return _strip_embedded_context(doc, content)

    rel_path = str(doc.get("path") or doc.get("parent_file") or "")
    v8_root = os.getenv("V8_PATH", "").strip()
    if rel_path and v8_root and str(doc.get("source") or "").lower() == "v8_source":
        file_path = Path(v8_root).expanduser() / rel_path
        try:
            return file_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            pass

    return str(doc.get("context") or "")


def _hydrate_doc(doc: Dict[str, object], bm25_db_path: Path | None = None) -> Dict[str, object]:
    hydrated = dict(doc)
    hydrated["content"] = _resolve_doc_content(hydrated, bm25_db_path)
    return hydrated


def _rrf_fuse(
    result_lists: List[List[Dict[str, object]]], rrf_k: int = 60
) -> List[Dict[str, object]]:
    scores = {}
    for results in result_lists:
        for rank, item in enumerate(results):
            doc_id = item.get("doc_id")
            if not doc_id:
                continue
            score = 1.0 / (rrf_k + rank + 1)
            if doc_id in scores:
                scores[doc_id]["rrf_score"] += score
                for key, value in item.items():
                    if key not in scores[doc_id] or scores[doc_id][key] in (None, ""):
                        scores[doc_id][key] = value
            else:
                merged = dict(item)
                merged["rrf_score"] = score
                scores[doc_id] = merged
    return sorted(scores.values(), key=lambda d: d.get("rrf_score", 0.0), reverse=True)


def _lite_rerank(
    query: str, docs: List[Dict[str, object]]
) -> List[Dict[str, object]]:
    query_tokens = set(_tokenize(query))
    if not query_tokens:
        return docs
    ranked = []
    for doc in docs:
        content = str(doc.get("content", ""))
        doc_tokens = set(_tokenize(content))
        overlap = len(query_tokens & doc_tokens) / max(1, len(query_tokens))
        base = float(doc.get("rrf_score", 0.0)) + float(doc.get("similarity", 0.0))
        doc = dict(doc)
        doc["rerank_score"] = (0.65 * base) + (0.35 * overlap)
        ranked.append(doc)
    return sorted(ranked, key=lambda d: d.get("rerank_score", 0.0), reverse=True)


def _maybe_cross_rerank(
    query: str, docs: List[Dict[str, object]]
) -> List[Dict[str, object]]:
    mode = os.getenv("RAG_RERANKER", "lite").lower()
    if mode not in ("cross", "full"):
        return _lite_rerank(query, docs)
    model_name = os.getenv("RAG_RERANKER_MODEL", "").strip()
    if not model_name:
        return _lite_rerank(query, docs)
    try:
        from sentence_transformers import CrossEncoder
    except Exception:
        return _lite_rerank(query, docs)
    try:
        reranker = CrossEncoder(model_name)
    except Exception:
        return _lite_rerank(query, docs)
    pairs = [(query, str(doc.get("content", ""))) for doc in docs]
    scores = reranker.predict(pairs)
    ranked = []
    for doc, score in zip(docs, scores):
        doc = dict(doc)
        doc["rerank_score"] = float(score)
        ranked.append(doc)
    return sorted(ranked, key=lambda d: d.get("rerank_score", 0.0), reverse=True)


def _format_rag_result(doc: dict) -> str:
    lines = []
    topic = doc.get("topic")
    path = doc.get("path") or doc.get("parent_file")
    issue_id = doc.get("issue_id")
    url = doc.get("url")
    doc_id = doc.get("doc_id")
    if topic:
        lines.append(f"Topic: {topic}")
    if path:
        lines.append(f"File: {path}")
    if issue_id:
        lines.append(f"Issue: {issue_id}")
    if url:
        lines.append(f"URL: {url}")
    if doc_id:
        lines.append(f"ChunkID: {doc_id}")
    if doc.get("chunk_index") is not None and doc.get("total_chunks"):
        lines.append(f"Chunk: {doc['chunk_index'] + 1}/{doc['total_chunks']}")
    if doc.get("start_char") is not None and doc.get("end_char") is not None:
        lines.append(f"Chars: {doc['start_char']}-{doc['end_char']}")
    if doc.get("start_line") is not None and doc.get("end_line") is not None:
        lines.append(f"Lines: {doc['start_line']}-{doc['end_line']}")
    if lines:
        lines.append("")
    content = doc.get("content", "")
    lines.append(content)
    return "\n".join(lines).strip()
