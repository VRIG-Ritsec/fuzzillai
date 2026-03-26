"""
RAG Chromium issues tools: search_chromium_issues_rag, search_chromium_issues_rag_hybrid.
"""

import json
import pickle
import sys
from pathlib import Path

_agentic_dir = Path(__file__).resolve().parent.parent.parent
_ikacore_src = _agentic_dir / "IkaCore" / "src"
if str(_ikacore_src) not in sys.path:
    sys.path.insert(0, str(_ikacore_src))

from IkaCore.tools import IkaTools

import json

from ._shared import (
    FAISS_AVAILABLE,
    _rag_base_dir,
    _bm25_search,
    _rrf_fuse,
    _maybe_cross_rerank,
    _hydrate_doc,
    _load_sentence_transformer,
    _score_to_similarity,
    _format_rag_result,
)

if FAISS_AVAILABLE:
    import faiss
    import pickle
    from sentence_transformers import SentenceTransformer

if FAISS_AVAILABLE:
    import faiss
    from sentence_transformers import SentenceTransformer


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
        self.model = _load_sentence_transformer(model_name)

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def search_vector(self, query: str, top_k: int = 5):
        if self.model is None:
            return self.search_lexical(query, top_k)
        query_embedding = self.model.encode([query], convert_to_numpy=True)
        distances, indices = self.index.search(query_embedding.astype("float32"), top_k)
        results = []
        for idx, distance in zip(indices[0], distances[0]):
            if idx < len(self.metadata):
                doc = _hydrate_doc(self.metadata[idx], self.bm25_db_path)
                similarity = 1.0 / (1.0 + distance)
                results.append(
                    {
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
                    }
                )
        return results

    def search_lexical(self, query: str, top_k: int = 5):
        results = []
        for doc_id, score in _bm25_search(self.bm25_db_path, query, top_k):
            base_doc = self.doc_id_to_doc.get(doc_id)
            doc = _hydrate_doc(base_doc, self.bm25_db_path) if base_doc else None
            if not doc:
                continue
            results.append(
                {
                    "doc_id": doc.get("doc_id"),
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
                    "similarity": _score_to_similarity(score),
                }
            )
            if len(results) >= top_k:
                break
        return results

    def search_hybrid(
        self,
        query: str,
        top_k: int = 5,
        vector_k: int = 40,
        bm25_k: int = 60,
        rerank_k: int = 12,
    ):
        vector_results = self.search_vector(query, vector_k) if self.model is not None else []
        bm25_hits = _bm25_search(self.bm25_db_path, query, bm25_k)
        bm25_results = []
        for doc_id, score in bm25_hits:
            base_doc = self.doc_id_to_doc.get(doc_id)
            doc = _hydrate_doc(base_doc, self.bm25_db_path) if base_doc else None
            if not doc:
                continue
            bm25_results.append(
                {
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
                }
            )
        if not vector_results:
            rerank_pool = bm25_results[: max(top_k, rerank_k)]
            reranked = _maybe_cross_rerank(query, rerank_pool)
            return reranked[:top_k]
        fused = _rrf_fuse([vector_results, bm25_results], rrf_k=60)
        rerank_pool = fused[: max(top_k, rerank_k)]
        reranked = _maybe_cross_rerank(query, rerank_pool)
        return reranked[:top_k]

    def search(self, query: str, top_k: int = 5):
        return self.search_vector(query, top_k)


def _search_chromium_issues_rag_executor(params: dict) -> str:
    query = params.get("query", "")
    top_k = params.get("top_k", 3)
    if not query:
        return json.dumps({"error": "query parameter is required"})
    if not FAISS_AVAILABLE:
        return json.dumps(
            {"error": "Chromium issues RAG not available. Install: pip install numpy faiss-cpu sentence-transformers"}
        )
    try:
        kb = FAISSChromiumIssuesRag.get_instance()
        top_k = max(1, min(10, int(top_k)))
        results = kb.search(query, top_k)
        output = []
        for result in results:
            formatted = _format_rag_result(result)
            output.append(
                {
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
                }
            )
        return json.dumps(output, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Failed to search Chromium issues RAG: {str(e)}"})


search_chromium_issues_rag_tool = IkaTools(
    name="search_chromium_issues_rag",
    description="Semantic search over Chromium bug tracker. Use for crash reports, related issues, or prior fixes.",
    parameters={
        "query": {"type": "string", "description": "Natural language query about Chromium issues", "required": True},
        "top_k": {"type": "integer", "description": "Number of results to return (default 3, max 10)", "required": False},
    },
    execute_function=_search_chromium_issues_rag_executor,
)


def _search_chromium_issues_rag_hybrid_executor(params: dict) -> str:
    query = params.get("query", "")
    top_k = params.get("top_k", 6)
    vector_k = params.get("vector_k", 40)
    bm25_k = params.get("bm25_k", 60)
    rerank_k = params.get("rerank_k", 12)
    if not query:
        return json.dumps({"error": "query parameter is required"})
    if not FAISS_AVAILABLE:
        return json.dumps(
            {"error": "Chromium issues RAG not available. Install: pip install numpy faiss-cpu sentence-transformers"}
        )
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
            output.append(
                {
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
                }
            )
        return json.dumps(output, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Failed to search Chromium issues RAG (hybrid): {str(e)}"})


search_chromium_issues_rag_hybrid_tool = IkaTools(
    name="search_chromium_issues_rag_hybrid",
    description="Hybrid search over Chromium issues for broader recall.",
    parameters={
        "query": {"type": "string", "description": "Natural language query about Chromium issues", "required": True},
        "top_k": {"type": "integer", "description": "Number of results to return (default 6, max 8)", "required": False},
        "vector_k": {"type": "integer", "description": "Vector candidates to retrieve (default 40, max 80)", "required": False},
        "bm25_k": {"type": "integer", "description": "BM25 candidates to retrieve (default 60, max 120)", "required": False},
        "rerank_k": {"type": "integer", "description": "Candidates to rerank (default 12, max 20)", "required": False},
    },
    execute_function=_search_chromium_issues_rag_hybrid_executor,
)
