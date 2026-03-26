"""
RAG knowledge base tools: search_knowledge_base, search_knowledge_base_hybrid, get_knowledge_doc.
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


_KNOWLEDGE_BASE_CORPUS_DESCRIPTION = (
    "Search the public documentation corpus only: MDN JavaScript reference and guides, "
    "C++ reference docs from cppreference/devdocs, and V8 public docs, blog posts, and API pages from v8.dev. "
    "Do not use this for local repository internals, Fuzzilli Swift code, ProgramTemplate implementation details, "
    "or V8 source files."
)

_KNOWLEDGE_BASE_USAGE_DESCRIPTION = (
    "Use this for public concepts, APIs, language semantics, and high-level design/background. "
    "For repo code or implementation internals, use source-search tools instead."
)

_KNOWLEDGE_BASE_TOPIC_DESCRIPTION = (
    "Optional coarse doc-corpus filter: `v8` for public V8 docs/blog/API pages, "
    "`javascript` for MDN JavaScript docs, or `cpp` for C++ reference docs."
)


class FAISSKnowledgeBase:
    _instance = None

    def __init__(self):
        if not FAISS_AVAILABLE:
            raise RuntimeError("FAISS dependencies not available")
        base_dir = _rag_base_dir() / "v8_knowlagebase"
        if not base_dir.exists():
            raise FileNotFoundError(f"Knowledge base not found: {base_dir.resolve()}")
        index_file = base_dir / "v8_knowlagebase.index"
        metadata_file = base_dir / "v8_knowlagebase_metadata.json"
        model_file = base_dir / "v8_knowlagebase_model.pkl"
        if not all([index_file.exists(), metadata_file.exists(), model_file.exists()]):
            raise FileNotFoundError(
                f"Knowledge base files incomplete: {index_file.resolve()}, "
                f"{metadata_file.resolve()}, {model_file.resolve()}"
            )
        self.index = faiss.read_index(str(index_file))
        with open(metadata_file, "r") as f:
            self.metadata = json.load(f)
        self.doc_id_to_doc = {doc.get("doc_id"): doc for doc in self.metadata if doc.get("doc_id")}
        self.bm25_db_path = base_dir / "v8_knowlagebase_bm25.sqlite"
        with open(model_file, "rb") as f:
            model_name = pickle.load(f)
        self.model = _load_sentence_transformer(model_name)

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def search_vector(self, query: str, top_k: int = 5, topic_filter: str = None):
        if self.model is None:
            return self.search_lexical(query, top_k, topic_filter)
        query_embedding = self.model.encode([query], convert_to_numpy=True)
        search_k = top_k * 3 if topic_filter else top_k
        distances, indices = self.index.search(query_embedding.astype("float32"), search_k)
        results = []
        for idx, distance in zip(indices[0], distances[0]):
            if idx < len(self.metadata):
                doc = _hydrate_doc(self.metadata[idx], self.bm25_db_path)
                if topic_filter and topic_filter.lower() not in str(doc.get("topic", "")).lower():
                    continue
                similarity = 1.0 / (1.0 + distance)
                results.append(
                    {
                        "doc_id": doc.get("doc_id"),
                        "path": doc.get("path"),
                        "topic": doc.get("topic"),
                        "content": doc.get("content", ""),
                        "parent_id": doc.get("parent_id"),
                        "chunk_index": doc.get("chunk_index"),
                        "total_chunks": doc.get("total_chunks"),
                        "start_char": doc.get("start_char"),
                        "end_char": doc.get("end_char"),
                        "start_line": doc.get("start_line"),
                        "end_line": doc.get("end_line"),
                        "similarity": float(similarity),
                    }
                )
                if len(results) >= top_k:
                    break
        return results

    def search_lexical(self, query: str, top_k: int = 5, topic_filter: str = None):
        results = []
        search_k = top_k * 3 if topic_filter else top_k
        for doc_id, score in _bm25_search(self.bm25_db_path, query, search_k):
            base_doc = self.doc_id_to_doc.get(doc_id)
            doc = _hydrate_doc(base_doc, self.bm25_db_path) if base_doc else None
            if not doc:
                continue
            if topic_filter and topic_filter.lower() not in str(doc.get("topic", "")).lower():
                continue
            results.append(
                {
                    "doc_id": doc.get("doc_id"),
                    "path": doc.get("path"),
                    "topic": doc.get("topic"),
                    "content": doc.get("content", ""),
                    "parent_id": doc.get("parent_id"),
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
        topic_filter: str = None,
        vector_k: int = 40,
        bm25_k: int = 60,
        rerank_k: int = 12,
    ):
        vector_results = self.search_vector(query, vector_k, topic_filter) if self.model is not None else []
        bm25_hits = _bm25_search(self.bm25_db_path, query, bm25_k)
        bm25_results = []
        for doc_id, score in bm25_hits:
            base_doc = self.doc_id_to_doc.get(doc_id)
            doc = _hydrate_doc(base_doc, self.bm25_db_path) if base_doc else None
            if not doc:
                continue
            if topic_filter and topic_filter.lower() not in doc.get("topic", "").lower():
                continue
            bm25_results.append(
                {
                    "doc_id": doc_id,
                    "path": doc.get("path"),
                    "topic": doc.get("topic"),
                    "content": doc.get("content", ""),
                    "parent_id": doc.get("parent_id"),
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

    def search(self, query: str, top_k: int = 5, topic_filter: str = None):
        return self.search_vector(query, top_k, topic_filter)


def _search_knowledge_base_executor(params: dict) -> str:
    query = params.get("query", "")
    top_k = params.get("top_k", 3)
    topic_filter = params.get("topic_filter", "")
    if not query:
        return json.dumps({"error": "query parameter is required"})
    if not FAISS_AVAILABLE:
        return json.dumps(
            {"error": "Knowledge base not available. Install: pip install numpy faiss-cpu sentence-transformers"}
        )
    try:
        kb = FAISSKnowledgeBase.get_instance()
        top_k = max(1, min(10, int(top_k)))
        results = kb.search(query, top_k, topic_filter if topic_filter else None)
        output = []
        for result in results:
            formatted = _format_rag_result(result)
            output.append(
                {
                    "doc_id": result.get("doc_id"),
                    "topic": result["topic"],
                    "file": result["path"],
                    "similarity": round(result["similarity"], 3),
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
        return json.dumps({"error": f"Failed to search knowledge base: {str(e)}"})


search_knowledge_base_tool = IkaTools(
    name="search_knowledge_base",
    description=(
        "Semantic search over public documentation. "
        + _KNOWLEDGE_BASE_CORPUS_DESCRIPTION
        + " "
        + _KNOWLEDGE_BASE_USAGE_DESCRIPTION
    ),
    parameters={
        "query": {
            "type": "string",
            "description": (
                "Natural language query for public docs such as MDN JavaScript behavior, C++ library/reference material, "
                "or V8 public docs/blog/API pages. Avoid repo-internal implementation queries. "
                "For public-doc context, prefer shorter focused queries such as "
                "`V8 Liftoff function body decoder validating function body WebAssembly` or "
                "`V8 wasm compilation pipeline Liftoff validation`."
            ),
            "required": True,
        },
        "top_k": {"type": "integer", "description": "Number of results to return (default 3, max 10)", "required": False},
        "topic_filter": {"type": "string", "description": _KNOWLEDGE_BASE_TOPIC_DESCRIPTION, "required": False},
    },
    execute_function=_search_knowledge_base_executor,
)


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
        return json.dumps(
            {"error": "Knowledge base not available. Install: pip install numpy faiss-cpu sentence-transformers"}
        )
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
            output.append(
                {
                    "doc_id": result.get("doc_id"),
                    "topic": result.get("topic"),
                    "file": result.get("path"),
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
        return json.dumps({"error": f"Failed to search knowledge base (hybrid): {str(e)}"})


search_knowledge_base_hybrid_tool = IkaTools(
    name="search_knowledge_base_hybrid",
    description=(
        "Hybrid search over the same public documentation corpus with BM25, vector search, and reranking for higher recall. "
        + _KNOWLEDGE_BASE_CORPUS_DESCRIPTION
        + " "
        + _KNOWLEDGE_BASE_USAGE_DESCRIPTION
    ),
    parameters={
        "query": {
            "type": "string",
            "description": (
                "Natural language query for public docs such as MDN JavaScript behavior, C++ library/reference material, "
                "or V8 public docs/blog/API pages. Avoid repo-internal implementation queries. "
                "For public-doc context, prefer shorter focused queries such as "
                "`V8 Liftoff function body decoder validating function body WebAssembly` or "
                "`V8 wasm compilation pipeline Liftoff validation`."
            ),
            "required": True,
        },
        "top_k": {"type": "integer", "description": "Number of results to return (default 6, max 8)", "required": False},
        "topic_filter": {"type": "string", "description": _KNOWLEDGE_BASE_TOPIC_DESCRIPTION, "required": False},
        "vector_k": {"type": "integer", "description": "Vector candidates to retrieve (default 40, max 80)", "required": False},
        "bm25_k": {"type": "integer", "description": "BM25 candidates to retrieve (default 60, max 120)", "required": False},
        "rerank_k": {"type": "integer", "description": "Candidates to rerank (default 12, max 20)", "required": False},
    },
    execute_function=_search_knowledge_base_hybrid_executor,
)


def _get_knowledge_doc_executor(params: dict) -> str:
    file_path = params.get("file_path", "")
    if not file_path:
        return json.dumps({"error": "file_path parameter is required"})
    if not FAISS_AVAILABLE:
        return json.dumps({"error": "Knowledge base not available"})
    try:
        kb = FAISSKnowledgeBase.get_instance()
        matches = [doc for doc in kb.metadata if doc.get("path") == file_path]
        if not matches:
            return json.dumps({"error": f"Document not found: {file_path}"})
        matches.sort(key=lambda d: d.get("chunk_index", 0))
        output = []
        for doc in matches:
            hydrated = _hydrate_doc(doc, kb.bm25_db_path)
            output.append(
                {
                    "doc_id": hydrated.get("doc_id"),
                    "topic": hydrated.get("topic"),
                    "file": hydrated.get("path"),
                    "chunk_index": hydrated.get("chunk_index"),
                    "total_chunks": hydrated.get("total_chunks"),
                    "start_char": hydrated.get("start_char"),
                    "end_char": hydrated.get("end_char"),
                    "start_line": hydrated.get("start_line"),
                    "end_line": hydrated.get("end_line"),
                    "content": hydrated.get("content", ""),
                }
            )
        return json.dumps(output, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Failed to retrieve document: {str(e)}"})


get_knowledge_doc_tool = IkaTools(
    name="get_knowledge_doc",
    description=(
        "Fetch a full document from the public docs corpus by path from search results. "
        "These paths refer to indexed MDN pages, cppreference/devdocs entries, or v8.dev docs/blog/API pages."
    ),
    parameters={
        "file_path": {"type": "string", "description": "The relative file path from search results", "required": True}
    },
    execute_function=_get_knowledge_doc_executor,
)
