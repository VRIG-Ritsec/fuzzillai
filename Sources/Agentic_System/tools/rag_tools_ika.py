#!/usr/bin/env python3
"""
RAG Tools using direct IkaTools class instantiation.
Each tool is defined as an IkaTools instance with an explicit executor function.
"""

import sys
import os
import json
import re
import sqlite3
from pathlib import Path
from typing import List, Dict, Tuple

_ikacore_src = Path(__file__).resolve().parent.parent / "IkaCore" / "src"
if str(_ikacore_src) not in sys.path:
    sys.path.insert(0, str(_ikacore_src))

from IkaCore.tools import IkaTools

# Try to import FAISS dependencies
try:
    import numpy as np
    import faiss
    import pickle
    os.environ.setdefault('ACCELERATE_USE_CPU', '1')
    os.environ.setdefault('ACCELERATE_USE_META_DEVICE', '0')
    from sentence_transformers import SentenceTransformer
    FAISS_AVAILABLE = True
except ImportError:
    FAISS_AVAILABLE = False


# =============================================================================
# Helper Functions
# =============================================================================

def _rag_base_dir() -> Path:
    default_dir = Path(__file__).resolve().parent.parent / "rag_db"
    base_dir = Path(os.getenv("RAG_BASE_DIR", str(default_dir))).expanduser()
    return base_dir


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


def _rrf_fuse(result_lists: List[List[Dict[str, object]]], rrf_k: int = 60) -> List[Dict[str, object]]:
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


def _lite_rerank(query: str, docs: List[Dict[str, object]]) -> List[Dict[str, object]]:
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


def _maybe_cross_rerank(query: str, docs: List[Dict[str, object]]) -> List[Dict[str, object]]:
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


# =============================================================================
# FAISS Knowledge Base Classes
# =============================================================================

class FAISSKnowledgeBase:
    _instance = None

    def __init__(self):
        if not FAISS_AVAILABLE:
            raise RuntimeError("FAISS dependencies not available")

        base_dir = _rag_base_dir() / "v8_knowlagebase"

        if not base_dir.exists():
            raise FileNotFoundError(f"Knowledge base not found: {base_dir.resolve()}")

        index_file = base_dir / 'v8_knowlagebase.index'
        metadata_file = base_dir / 'v8_knowlagebase_metadata.json'
        model_file = base_dir / 'v8_knowlagebase_model.pkl'

        if not all([index_file.exists(), metadata_file.exists(), model_file.exists()]):
            raise FileNotFoundError(
                f"Knowledge base files incomplete: {index_file.resolve()}, "
                f"{metadata_file.resolve()}, {model_file.resolve()}"
            )

        self.index = faiss.read_index(str(index_file))

        with open(metadata_file, 'r') as f:
            self.metadata = json.load(f)

        self.doc_id_to_doc = {doc.get("doc_id"): doc for doc in self.metadata if doc.get("doc_id")}
        self.bm25_db_path = base_dir / "v8_knowlagebase_bm25.sqlite"

        with open(model_file, 'rb') as f:
            model_name = pickle.load(f)

        try:
            self.model = SentenceTransformer(model_name, device='cpu')
        except (TypeError, ValueError):
            self.model = SentenceTransformer(model_name)
            self.model = self.model.to('cpu')

        if hasattr(self.model, 'eval'):
            self.model.eval()

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def search_vector(self, query: str, top_k: int = 5, topic_filter: str = None):
        query_embedding = self.model.encode([query], convert_to_numpy=True)
        search_k = top_k * 3 if topic_filter else top_k
        distances, indices = self.index.search(query_embedding.astype('float32'), search_k)

        results = []
        for idx, distance in zip(indices[0], distances[0]):
            if idx < len(self.metadata):
                doc = self.metadata[idx]
                if topic_filter and topic_filter.lower() not in doc['topic'].lower():
                    continue
                similarity = 1.0 / (1.0 + distance)
                results.append({
                    'doc_id': doc.get('doc_id'),
                    'path': doc['path'],
                    'topic': doc['topic'],
                    'content': doc['content'],
                    'parent_id': doc.get('parent_id'),
                    'chunk_index': doc.get('chunk_index'),
                    'total_chunks': doc.get('total_chunks'),
                    'start_char': doc.get('start_char'),
                    'end_char': doc.get('end_char'),
                    'start_line': doc.get('start_line'),
                    'end_line': doc.get('end_line'),
                    'similarity': float(similarity)
                })
                if len(results) >= top_k:
                    break
        return results

    def search_hybrid(
        self,
        query: str,
        top_k: int = 5,
        topic_filter: str = None,
        vector_k: int = 40,
        bm25_k: int = 60,
        rerank_k: int = 12,
    ):
        vector_results = self.search_vector(query, vector_k, topic_filter)
        bm25_hits = _bm25_search(self.bm25_db_path, query, bm25_k)
        bm25_results = []
        for doc_id, score in bm25_hits:
            doc = self.doc_id_to_doc.get(doc_id)
            if not doc:
                continue
            if topic_filter and topic_filter.lower() not in doc.get('topic', '').lower():
                continue
            bm25_results.append({
                'doc_id': doc_id,
                'path': doc.get('path'),
                'topic': doc.get('topic'),
                'content': doc.get('content', ''),
                'parent_id': doc.get('parent_id'),
                'chunk_index': doc.get('chunk_index'),
                'total_chunks': doc.get('total_chunks'),
                'start_char': doc.get('start_char'),
                'end_char': doc.get('end_char'),
                'start_line': doc.get('start_line'),
                'end_line': doc.get('end_line'),
                'bm25_score': float(score),
            })

        fused = _rrf_fuse([vector_results, bm25_results], rrf_k=60)
        rerank_pool = fused[:max(top_k, rerank_k)]
        reranked = _maybe_cross_rerank(query, rerank_pool)
        return reranked[:top_k]

    def search(self, query: str, top_k: int = 5, topic_filter: str = None):
        return self.search_vector(query, top_k, topic_filter)


class FAISSV8SourceRag:
    _instance = None

    def __init__(self):
        if not FAISS_AVAILABLE:
            raise RuntimeError("FAISS dependencies not available")

        base_dir = _rag_base_dir() / "v8_source_rag"

        if not base_dir.exists():
            raise FileNotFoundError(f"V8 source RAG not found: {base_dir.resolve()}")

        index_file = base_dir / 'v8_source_rag.index'
        metadata_file = base_dir / 'v8_source_rag_metadata.json'
        model_file = base_dir / 'v8_source_rag_model.pkl'

        if not all([index_file.exists(), metadata_file.exists(), model_file.exists()]):
            raise FileNotFoundError(
                f"V8 source RAG files incomplete: {index_file.resolve()}, "
                f"{metadata_file.resolve()}, {model_file.resolve()}"
            )

        self.index = faiss.read_index(str(index_file))

        with open(metadata_file, 'r') as f:
            self.metadata = json.load(f)

        self.doc_id_to_doc = {doc.get("doc_id"): doc for doc in self.metadata if doc.get("doc_id")}
        self.bm25_db_path = base_dir / "v8_source_rag_bm25.sqlite"

        with open(model_file, 'rb') as f:
            model_name = pickle.load(f)

        try:
            self.model = SentenceTransformer(model_name, device='cpu')
        except (TypeError, ValueError):
            self.model = SentenceTransformer(model_name)
            self.model = self.model.to('cpu')

        if hasattr(self.model, 'eval'):
            self.model.eval()

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def search_vector(self, query: str, top_k: int = 5, topic_filter: str = None):
        query_embedding = self.model.encode([query], convert_to_numpy=True)
        search_k = top_k * 3 if topic_filter else top_k
        distances, indices = self.index.search(query_embedding.astype('float32'), search_k)

        results = []
        for idx, distance in zip(indices[0], distances[0]):
            if idx < len(self.metadata):
                doc = self.metadata[idx]
                if topic_filter and topic_filter.lower() not in doc['topic'].lower():
                    continue
                similarity = 1.0 / (1.0 + distance)
                results.append({
                    'doc_id': doc.get('doc_id'),
                    'path': doc['path'],
                    'topic': doc['topic'],
                    'content': doc['content'],
                    'parent_id': doc.get('parent_id'),
                    'chunk_index': doc.get('chunk_index'),
                    'total_chunks': doc.get('total_chunks'),
                    'start_char': doc.get('start_char'),
                    'end_char': doc.get('end_char'),
                    'start_line': doc.get('start_line'),
                    'end_line': doc.get('end_line'),
                    'similarity': float(similarity)
                })
                if len(results) >= top_k:
                    break
        return results

    def search_hybrid(
        self,
        query: str,
        top_k: int = 5,
        topic_filter: str = None,
        vector_k: int = 40,
        bm25_k: int = 60,
        rerank_k: int = 12,
    ):
        vector_results = self.search_vector(query, vector_k, topic_filter)
        bm25_hits = _bm25_search(self.bm25_db_path, query, bm25_k)
        bm25_results = []
        for doc_id, score in bm25_hits:
            doc = self.doc_id_to_doc.get(doc_id)
            if not doc:
                continue
            if topic_filter and topic_filter.lower() not in doc.get('topic', '').lower():
                continue
            bm25_results.append({
                'doc_id': doc_id,
                'path': doc.get('path'),
                'topic': doc.get('topic'),
                'content': doc.get('content', ''),
                'parent_id': doc.get('parent_id'),
                'chunk_index': doc.get('chunk_index'),
                'total_chunks': doc.get('total_chunks'),
                'start_char': doc.get('start_char'),
                'end_char': doc.get('end_char'),
                'start_line': doc.get('start_line'),
                'end_line': doc.get('end_line'),
                'bm25_score': float(score),
            })

        fused = _rrf_fuse([vector_results, bm25_results], rrf_k=60)
        rerank_pool = fused[:max(top_k, rerank_k)]
        reranked = _maybe_cross_rerank(query, rerank_pool)
        return reranked[:top_k]

    def search(self, query: str, top_k: int = 5, topic_filter: str = None):
        return self.search_vector(query, top_k, topic_filter)


class FAISSChromiumIssuesRag:
    _instance = None

    def __init__(self):
        if not FAISS_AVAILABLE:
            raise RuntimeError("FAISS dependencies not available")

        base_dir = _rag_base_dir() / "chromium_issues_rag"

        if not base_dir.exists():
            raise FileNotFoundError(f"Chromium issues RAG not found: {base_dir.resolve()}")

        index_file = base_dir / "chromium_issues_rag.index"
        metadata_file = base_dir / "chromium_issues_rag_metadata.json"
        model_file = base_dir / "chromium_issues_rag_model.pkl"

        if not all([index_file.exists(), metadata_file.exists(), model_file.exists()]):
            raise FileNotFoundError(
                f"Chromium issues RAG files incomplete: {index_file.resolve()}, "
                f"{metadata_file.resolve()}, {model_file.resolve()}"
            )

        self.index = faiss.read_index(str(index_file))

        with open(metadata_file, "r") as f:
            self.metadata = json.load(f)

        self.doc_id_to_doc = {doc.get("doc_id"): doc for doc in self.metadata if doc.get("doc_id")}
        self.bm25_db_path = base_dir / "chromium_issues_rag_bm25.sqlite"

        with open(model_file, "rb") as f:
            model_name = pickle.load(f)

        try:
            self.model = SentenceTransformer(model_name, device="cpu")
        except (TypeError, ValueError):
            self.model = SentenceTransformer(model_name)
            self.model = self.model.to("cpu")

        if hasattr(self.model, "eval"):
            self.model.eval()

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def search_vector(self, query: str, top_k: int = 5):
        query_embedding = self.model.encode([query], convert_to_numpy=True)
        distances, indices = self.index.search(query_embedding.astype("float32"), top_k)

        results = []
        for idx, distance in zip(indices[0], distances[0]):
            if idx < len(self.metadata):
                doc = self.metadata[idx]
                similarity = 1.0 / (1.0 + distance)
                results.append({
                    "doc_id": doc.get("doc_id"),
                    "issue_id": doc.get("issue_id"),
                    "url": doc.get("url"),
                    "topic": doc.get("topic"),
                    "content": doc.get("content"),
                    "chunk_index": doc.get("chunk_index"),
                    "total_chunks": doc.get("total_chunks"),
                    "start_char": doc.get("start_char"),
                    "end_char": doc.get("end_char"),
                    "start_line": doc.get("start_line"),
                    "end_line": doc.get("end_line"),
                    "similarity": float(similarity),
                })
        return results

    def search_hybrid(
        self,
        query: str,
        top_k: int = 5,
        vector_k: int = 40,
        bm25_k: int = 60,
        rerank_k: int = 12,
    ):
        vector_results = self.search_vector(query, vector_k)
        bm25_hits = _bm25_search(self.bm25_db_path, query, bm25_k)
        bm25_results = []
        for doc_id, score in bm25_hits:
            doc = self.doc_id_to_doc.get(doc_id)
            if not doc:
                continue
            bm25_results.append({
                "doc_id": doc_id,
                "issue_id": doc.get("issue_id"),
                "url": doc.get("url"),
                "topic": doc.get("topic"),
                "content": doc.get("content", ""),
                "chunk_index": doc.get("chunk_index"),
                "total_chunks": doc.get("total_chunks"),
                "start_char": doc.get("start_char"),
                "end_char": doc.get("end_char"),
                "start_line": doc.get("start_line"),
                "end_line": doc.get("end_line"),
                "bm25_score": float(score),
            })

        fused = _rrf_fuse([vector_results, bm25_results], rrf_k=60)
        rerank_pool = fused[:max(top_k, rerank_k)]
        reranked = _maybe_cross_rerank(query, rerank_pool)
        return reranked[:top_k]

    def search(self, query: str, top_k: int = 5):
        return self.search_vector(query, top_k)


# =============================================================================
# Tool Executors and IkaTools Definitions
# =============================================================================

# --- search_knowledge_base ---
def _search_knowledge_base_executor(params: dict) -> str:
    query = params.get("query", "")
    top_k = params.get("top_k", 3)
    topic_filter = params.get("topic_filter", "")

    if not query:
        return json.dumps({"error": "query parameter is required"})

    if not FAISS_AVAILABLE:
        return json.dumps({"error": "Knowledge base not available. Install: pip install numpy faiss-cpu sentence-transformers"})

    try:
        kb = FAISSKnowledgeBase.get_instance()
        top_k = max(1, min(10, int(top_k)))
        results = kb.search(query, top_k, topic_filter if topic_filter else None)

        output = []
        for result in results:
            formatted = _format_rag_result(result)
            output.append({
                'doc_id': result.get('doc_id'),
                'topic': result['topic'],
                'file': result['path'],
                'similarity': round(result['similarity'], 3),
                'chunk_index': result.get('chunk_index'),
                'total_chunks': result.get('total_chunks'),
                'start_char': result.get('start_char'),
                'end_char': result.get('end_char'),
                'start_line': result.get('start_line'),
                'end_line': result.get('end_line'),
                'content': formatted
            })
        return json.dumps(output, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Failed to search knowledge base: {str(e)}"})

search_knowledge_base_tool = IkaTools(
    name="search_knowledge_base",
    description="Searches the V8/JavaScript/C++ knowledge base using semantic search",
    parameters={
        "query": {
            "type": "string",
            "description": "Natural language query about V8, JavaScript, or C++ concepts",
            "required": True
        },
        "top_k": {
            "type": "integer",
            "description": "Number of results to return (default 3, max 10)",
            "required": False
        },
        "topic_filter": {
            "type": "string",
            "description": "Optional topic filter: 'v8', 'javascript', 'cpp', or empty for all",
            "required": False
        }
    },
    execute_function=_search_knowledge_base_executor
)


# --- search_knowledge_base_hybrid ---
def _search_knowledge_base_hybrid_executor(params: dict) -> str:
    query = params.get("query", "")
    top_k = params.get("top_k", 6)
    topic_filter = params.get("topic_filter", "")
    vector_k = params.get("vector_k", 40)
    bm25_k = params.get("bm25_k", 60)
    rerank_k = params.get("rerank_k", 12)

    if not query:
        return json.dumps({"error": "query parameter is required"})

    if not FAISS_AVAILABLE:
        return json.dumps({"error": "Knowledge base not available. Install: pip install numpy faiss-cpu sentence-transformers"})

    try:
        kb = FAISSKnowledgeBase.get_instance()
        top_k = max(1, min(8, int(top_k)))
        vector_k = max(top_k, min(80, int(vector_k)))
        bm25_k = max(top_k, min(120, int(bm25_k)))
        rerank_k = max(top_k, min(20, int(rerank_k)))
        results = kb.search_hybrid(query, top_k, topic_filter if topic_filter else None, vector_k, bm25_k, rerank_k)

        output = []
        for result in results:
            formatted = _format_rag_result(result)
            output.append({
                'doc_id': result.get('doc_id'),
                'topic': result.get('topic'),
                'file': result.get('path'),
                'similarity': round(result.get('similarity', 0.0), 3),
                'chunk_index': result.get('chunk_index'),
                'total_chunks': result.get('total_chunks'),
                'start_char': result.get('start_char'),
                'end_char': result.get('end_char'),
                'start_line': result.get('start_line'),
                'end_line': result.get('end_line'),
                'content': formatted
            })
        return json.dumps(output, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Failed to search knowledge base (hybrid): {str(e)}"})


search_knowledge_base_hybrid_tool = IkaTools(
    name="search_knowledge_base_hybrid",
    description="Hybrid search (BM25 + vector + rerank) over the knowledge base",
    parameters={
        "query": {
            "type": "string",
            "description": "Natural language query about V8, JavaScript, or C++ concepts",
            "required": True
        },
        "top_k": {
            "type": "integer",
            "description": "Number of results to return (default 6, max 8)",
            "required": False
        },
        "topic_filter": {
            "type": "string",
            "description": "Optional topic filter: 'v8', 'javascript', 'cpp', or empty for all",
            "required": False
        },
        "vector_k": {
            "type": "integer",
            "description": "Vector candidates to retrieve (default 40, max 80)",
            "required": False
        },
        "bm25_k": {
            "type": "integer",
            "description": "BM25 candidates to retrieve (default 60, max 120)",
            "required": False
        },
        "rerank_k": {
            "type": "integer",
            "description": "Candidates to rerank (default 12, max 20)",
            "required": False
        },
    },
    execute_function=_search_knowledge_base_hybrid_executor
)


# --- get_knowledge_doc ---
def _get_knowledge_doc_executor(params: dict) -> str:
    file_path = params.get("file_path", "")

    if not file_path:
        return json.dumps({"error": "file_path parameter is required"})

    if not FAISS_AVAILABLE:
        return json.dumps({"error": "Knowledge base not available"})

    try:
        kb = FAISSKnowledgeBase.get_instance()

        matches = [doc for doc in kb.metadata if doc.get('path') == file_path]
        if not matches:
            return json.dumps({"error": f"Document not found: {file_path}"})

        matches.sort(key=lambda d: d.get("chunk_index", 0))
        output = []
        for doc in matches:
            output.append({
                'doc_id': doc.get('doc_id'),
                'topic': doc.get('topic'),
                'file': doc.get('path'),
                'chunk_index': doc.get('chunk_index'),
                'total_chunks': doc.get('total_chunks'),
                'start_char': doc.get('start_char'),
                'end_char': doc.get('end_char'),
                'start_line': doc.get('start_line'),
                'end_line': doc.get('end_line'),
                'content': doc.get('content', '')
            })
        return json.dumps(output, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Failed to retrieve document: {str(e)}"})

get_knowledge_doc_tool = IkaTools(
    name="get_knowledge_doc",
    description="Retrieves a full document from the knowledge base by its file path",
    parameters={
        "file_path": {
            "type": "string",
            "description": "The relative file path from search results",
            "required": True
        }
    },
    execute_function=_get_knowledge_doc_executor
)


# --- search_v8_source_rag ---
def _search_v8_source_rag_executor(params: dict) -> str:
    query = params.get("query", "")
    top_k = params.get("top_k", 3)
    topic_filter = params.get("topic_filter", "")

    if not query:
        return json.dumps({"error": "query parameter is required"})

    if not FAISS_AVAILABLE:
        return json.dumps({"error": "V8 source RAG not available. Install: pip install numpy faiss-cpu sentence-transformers"})

    try:
        kb = FAISSV8SourceRag.get_instance()
        top_k = max(1, min(10, int(top_k)))
        results = kb.search(query, top_k, topic_filter if topic_filter else None)

        output = []
        for result in results:
            formatted = _format_rag_result(result)
            output.append({
                'doc_id': result.get('doc_id'),
                'topic': result['topic'],
                'file': result['path'],
                'similarity': round(result['similarity'], 3),
                'chunk_index': result.get('chunk_index'),
                'total_chunks': result.get('total_chunks'),
                'start_char': result.get('start_char'),
                'end_char': result.get('end_char'),
                'start_line': result.get('start_line'),
                'end_line': result.get('end_line'),
                'content': formatted
            })
        return json.dumps(output, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Failed to search V8 source RAG: {str(e)}"})

search_v8_source_rag_tool = IkaTools(
    name="search_v8_source_rag",
    description="Searches the V8 source code RAG using semantic search",
    parameters={
        "query": {
            "type": "string",
            "description": "Natural language query about V8 source code",
            "required": True
        },
        "top_k": {
            "type": "integer",
            "description": "Number of results to return (default 3, max 10)",
            "required": False
        },
        "topic_filter": {
            "type": "string",
            "description": "Optional topic filter (e.g., 'ic', 'compiler', 'runtime')",
            "required": False
        }
    },
    execute_function=_search_v8_source_rag_executor
)


# --- search_v8_source_rag_hybrid ---
def _search_v8_source_rag_hybrid_executor(params: dict) -> str:
    query = params.get("query", "")
    top_k = params.get("top_k", 6)
    topic_filter = params.get("topic_filter", "")
    vector_k = params.get("vector_k", 40)
    bm25_k = params.get("bm25_k", 60)
    rerank_k = params.get("rerank_k", 12)

    if not query:
        return json.dumps({"error": "query parameter is required"})

    if not FAISS_AVAILABLE:
        return json.dumps({"error": "V8 source RAG not available. Install: pip install numpy faiss-cpu sentence-transformers"})

    try:
        kb = FAISSV8SourceRag.get_instance()
        top_k = max(1, min(8, int(top_k)))
        vector_k = max(top_k, min(80, int(vector_k)))
        bm25_k = max(top_k, min(120, int(bm25_k)))
        rerank_k = max(top_k, min(20, int(rerank_k)))
        results = kb.search_hybrid(query, top_k, topic_filter if topic_filter else None, vector_k, bm25_k, rerank_k)

        output = []
        for result in results:
            formatted = _format_rag_result(result)
            output.append({
                'doc_id': result.get('doc_id'),
                'topic': result.get('topic'),
                'file': result.get('path'),
                'similarity': round(result.get('similarity', 0.0), 3),
                'chunk_index': result.get('chunk_index'),
                'total_chunks': result.get('total_chunks'),
                'start_char': result.get('start_char'),
                'end_char': result.get('end_char'),
                'start_line': result.get('start_line'),
                'end_line': result.get('end_line'),
                'content': formatted
            })
        return json.dumps(output, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Failed to search V8 source RAG (hybrid): {str(e)}"})


search_v8_source_rag_hybrid_tool = IkaTools(
    name="search_v8_source_rag_hybrid",
    description="Hybrid search (BM25 + vector + rerank) over the V8 source RAG",
    parameters={
        "query": {
            "type": "string",
            "description": "Natural language query about V8 source code",
            "required": True
        },
        "top_k": {
            "type": "integer",
            "description": "Number of results to return (default 6, max 8)",
            "required": False
        },
        "topic_filter": {
            "type": "string",
            "description": "Optional topic filter (e.g., 'ic', 'compiler', 'runtime')",
            "required": False
        },
        "vector_k": {
            "type": "integer",
            "description": "Vector candidates to retrieve (default 40, max 80)",
            "required": False
        },
        "bm25_k": {
            "type": "integer",
            "description": "BM25 candidates to retrieve (default 60, max 120)",
            "required": False
        },
        "rerank_k": {
            "type": "integer",
            "description": "Candidates to rerank (default 12, max 20)",
            "required": False
        },
    },
    execute_function=_search_v8_source_rag_hybrid_executor
)


# --- get_v8_source_rag_doc ---
def _get_v8_source_rag_doc_executor(params: dict) -> str:
    file_path = params.get("file_path", "")

    if not file_path:
        return json.dumps({"error": "file_path parameter is required"})

    if not FAISS_AVAILABLE:
        return json.dumps({"error": "V8 source RAG not available"})

    try:
        kb = FAISSV8SourceRag.get_instance()

        matches = [doc for doc in kb.metadata if doc.get('path') == file_path]
        if not matches:
            return json.dumps({"error": f"V8 source RAG document not found: {file_path}"})

        matches.sort(key=lambda d: d.get("chunk_index", 0))
        output = []
        for doc in matches:
            output.append({
                'topic': doc.get('topic'),
                'file': doc.get('path'),
                'chunk_index': doc.get('chunk_index'),
                'total_chunks': doc.get('total_chunks'),
                'start_char': doc.get('start_char'),
                'end_char': doc.get('end_char'),
                'start_line': doc.get('start_line'),
                'end_line': doc.get('end_line'),
                'content': doc.get('content', '')
            })
        return json.dumps(output, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Failed to retrieve V8 source RAG document: {str(e)}"})

get_v8_source_rag_doc_tool = IkaTools(
    name="get_v8_source_rag_doc",
    description="Retrieves a full document from the V8 source RAG by its file path",
    parameters={
        "file_path": {
            "type": "string",
            "description": "The relative file path from search results",
            "required": True
        }
    },
    execute_function=_get_v8_source_rag_doc_executor
)


# --- search_chromium_issues_rag ---
def _search_chromium_issues_rag_executor(params: dict) -> str:
    query = params.get("query", "")
    top_k = params.get("top_k", 3)

    if not query:
        return json.dumps({"error": "query parameter is required"})

    if not FAISS_AVAILABLE:
        return json.dumps({"error": "Chromium issues RAG not available. Install: pip install numpy faiss-cpu sentence-transformers"})

    try:
        kb = FAISSChromiumIssuesRag.get_instance()
        top_k = max(1, min(10, int(top_k)))
        results = kb.search(query, top_k)

        output = []
        for result in results:
            formatted = _format_rag_result(result)
            output.append({
                "doc_id": result.get("doc_id"),
                "issue_id": result.get("issue_id"),
                "url": result.get("url"),
                "similarity": round(result.get("similarity", 0.0), 3),
                "chunk_index": result.get("chunk_index"),
                "total_chunks": result.get("total_chunks"),
                "start_char": result.get("start_char"),
                "end_char": result.get("end_char"),
                "start_line": result.get("start_line"),
                "end_line": result.get("end_line"),
                "content": formatted,
            })
        return json.dumps(output, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Failed to search Chromium issues RAG: {str(e)}"})


search_chromium_issues_rag_tool = IkaTools(
    name="search_chromium_issues_rag",
    description="Searches Chromium issues using semantic search",
    parameters={
        "query": {
            "type": "string",
            "description": "Natural language query about Chromium issues",
            "required": True
        },
        "top_k": {
            "type": "integer",
            "description": "Number of results to return (default 3, max 10)",
            "required": False
        }
    },
    execute_function=_search_chromium_issues_rag_executor
)


# --- search_chromium_issues_rag_hybrid ---
def _search_chromium_issues_rag_hybrid_executor(params: dict) -> str:
    query = params.get("query", "")
    top_k = params.get("top_k", 6)
    vector_k = params.get("vector_k", 40)
    bm25_k = params.get("bm25_k", 60)
    rerank_k = params.get("rerank_k", 12)

    if not query:
        return json.dumps({"error": "query parameter is required"})

    if not FAISS_AVAILABLE:
        return json.dumps({"error": "Chromium issues RAG not available. Install: pip install numpy faiss-cpu sentence-transformers"})

    try:
        kb = FAISSChromiumIssuesRag.get_instance()
        top_k = max(1, min(8, int(top_k)))
        vector_k = max(top_k, min(80, int(vector_k)))
        bm25_k = max(top_k, min(120, int(bm25_k)))
        rerank_k = max(top_k, min(20, int(rerank_k)))
        results = kb.search_hybrid(query, top_k, vector_k, bm25_k, rerank_k)

        output = []
        for result in results:
            formatted = _format_rag_result(result)
            output.append({
                "doc_id": result.get("doc_id"),
                "issue_id": result.get("issue_id"),
                "url": result.get("url"),
                "similarity": round(result.get("similarity", 0.0), 3),
                "chunk_index": result.get("chunk_index"),
                "total_chunks": result.get("total_chunks"),
                "start_char": result.get("start_char"),
                "end_char": result.get("end_char"),
                "start_line": result.get("start_line"),
                "end_line": result.get("end_line"),
                "content": formatted,
            })
        return json.dumps(output, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Failed to search Chromium issues RAG (hybrid): {str(e)}"})


search_chromium_issues_rag_hybrid_tool = IkaTools(
    name="search_chromium_issues_rag_hybrid",
    description="Hybrid search (BM25 + vector + rerank) over Chromium issues",
    parameters={
        "query": {
            "type": "string",
            "description": "Natural language query about Chromium issues",
            "required": True
        },
        "top_k": {
            "type": "integer",
            "description": "Number of results to return (default 6, max 8)",
            "required": False
        },
        "vector_k": {
            "type": "integer",
            "description": "Vector candidates to retrieve (default 40, max 80)",
            "required": False
        },
        "bm25_k": {
            "type": "integer",
            "description": "BM25 candidates to retrieve (default 60, max 120)",
            "required": False
        },
        "rerank_k": {
            "type": "integer",
            "description": "Candidates to rerank (default 12, max 20)",
            "required": False
        }
    },
    execute_function=_search_chromium_issues_rag_hybrid_executor
)


# =============================================================================
# Export all tools as a dictionary
# =============================================================================

ALL_RAG_TOOLS = {
    "search_knowledge_base": search_knowledge_base_tool,
    "search_knowledge_base_hybrid": search_knowledge_base_hybrid_tool,
    "get_knowledge_doc": get_knowledge_doc_tool,
    "search_v8_source_rag": search_v8_source_rag_tool,
    "search_v8_source_rag_hybrid": search_v8_source_rag_hybrid_tool,
    "get_v8_source_rag_doc": get_v8_source_rag_doc_tool,
    "search_chromium_issues_rag": search_chromium_issues_rag_tool,
    "search_chromium_issues_rag_hybrid": search_chromium_issues_rag_hybrid_tool,
}
