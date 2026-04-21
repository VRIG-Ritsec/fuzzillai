"""
RAG V8 source tools: search_v8_source_rag, search_v8_source_rag_hybrid, get_v8_source_rag_doc.
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
    RAG_DOC_DEFAULT_MAX_CHUNKS,
    RAG_DOC_DEFAULT_MAX_LINES,
    RAG_DOC_MAX_CHUNKS,
    RAG_DOC_MAX_CHARS,
    RAG_DOC_MAX_LINES,
    _rag_base_dir,
    _bm25_search,
    _rrf_fuse,
    _maybe_cross_rerank,
    _hydrate_doc,
    _load_sentence_transformer,
    _score_to_similarity,
    _format_rag_result,
    _truncate_text,
)

if FAISS_AVAILABLE:
    import faiss
    import pickle
    from sentence_transformers import SentenceTransformer

if FAISS_AVAILABLE:
    import faiss
    from sentence_transformers import SentenceTransformer


V8_SOURCE_TOP_LEVEL_AREAS = [
    "api",
    "asmjs",
    "ast",
    "base",
    "baseline",
    "bigint",
    "builtins",
    "codegen",
    "common",
    "compiler",
    "compiler-dispatcher",
    "d8",
    "date",
    "debug",
    "deoptimizer",
    "diagnostics",
    "dumpling",
    "execution",
    "extensions",
    "flags",
    "fuzzilli",
    "handles",
    "heap",
    "ic",
    "init",
    "inspector",
    "interpreter",
    "json",
    "libplatform",
    "libsampler",
    "logging",
    "maglev",
    "numbers",
    "objects",
    "parsing",
    "profiler",
    "regexp",
    "roots",
    "runtime",
    "sandbox",
    "snapshot",
    "strings",
    "tasks",
    "torque",
    "tracing",
    "trap-handler",
    "utils",
    "wasm",
    "zone",
]

_V8_SOURCE_TOP_LEVEL_AREAS_TEXT = ", ".join(V8_SOURCE_TOP_LEVEL_AREAS)
_V8_SOURCE_FILTER_DESCRIPTION = (
    "Filter by V8 source path prefix, not by semantic topic. Prefer `path_prefix_filters` "
    "with one or more top-level areas such as "
    f"{_V8_SOURCE_TOP_LEVEL_AREAS_TEXT}. "
    "More specific prefixes like `compiler/turboshaft` are also allowed."
)


def _normalize_path_prefix(value) -> str:
    if value is None:
        return ""
    prefix = str(value).strip().lower().replace("\\", "/")
    if not prefix:
        return ""
    if prefix.startswith("v8/src/"):
        prefix = prefix[len("v8/src/") :]
    elif prefix.startswith("src/"):
        prefix = prefix[len("src/") :]
    if prefix.startswith("v8 "):
        prefix = prefix[len("v8 ") :]
    prefix = prefix.strip("/")
    return prefix


def _normalize_path_prefix_filters(values) -> list[str]:
    if values is None:
        return []
    if isinstance(values, (list, tuple, set)):
        raw_values = list(values)
    else:
        raw_values = [values]

    normalized: list[str] = []
    seen: set[str] = set()
    for value in raw_values:
        prefix = _normalize_path_prefix(value)
        if not prefix or prefix in seen:
            continue
        seen.add(prefix)
        normalized.append(prefix)
    return normalized


def _collect_path_prefix_filters(params: dict) -> list[str]:
    combined: list[str] = []
    for key in (
        "path_prefix_filters",
        "path_prefix",
        "area_filters",
        "area_filter",
        "topic_filters",
        "topic_filter",
    ):
        combined.extend(_normalize_path_prefix_filters(params.get(key)))
    return _normalize_path_prefix_filters(combined)


def _matches_path_prefix_filters(doc: dict, path_prefix_filters: list[str] | None) -> bool:
    if not path_prefix_filters:
        return True
    path = _normalize_path_prefix(doc.get("path") or doc.get("parent_file") or "")
    topic = _normalize_path_prefix(doc.get("topic") or "")
    for prefix in path_prefix_filters:
        if path and (path == prefix or path.startswith(prefix + "/")):
            return True
        if topic and (topic == prefix or topic.startswith(prefix + "/")):
            return True
    return False


def _candidate_search_k(top_k: int, path_prefix_filters: list[str] | None) -> int:
    if not path_prefix_filters:
        return top_k
    return top_k * max(3, len(path_prefix_filters) * 2)


class FAISSV8SourceRag:
    _instance = None

    def __init__(self):
        if not FAISS_AVAILABLE:
            raise RuntimeError("FAISS dependencies not available")
        base_dir = _rag_base_dir() / "v8_source_rag"
        if not base_dir.exists():
            raise FileNotFoundError(f"V8 source RAG not found: {base_dir.resolve()}")
        index_file = base_dir / "v8_source_rag.index"
        metadata_file = base_dir / "v8_source_rag_metadata.json"
        model_file = base_dir / "v8_source_rag_model.pkl"
        if not all([index_file.exists(), metadata_file.exists(), model_file.exists()]):
            raise FileNotFoundError(
                f"V8 source RAG files incomplete: {index_file.resolve()}, "
                f"{metadata_file.resolve()}, {model_file.resolve()}"
            )
        self.index = faiss.read_index(str(index_file))
        with open(metadata_file, "r") as f:
            self.metadata = json.load(f)
        self.doc_id_to_doc = {doc.get("doc_id"): doc for doc in self.metadata if doc.get("doc_id")}
        self.bm25_db_path = base_dir / "v8_source_rag_bm25.sqlite"
        with open(model_file, "rb") as f:
            model_name = pickle.load(f)
        self.model = _load_sentence_transformer(model_name)

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def search_vector(self, query: str, top_k: int = 5, path_prefix_filters: list[str] | None = None):
        if self.model is None:
            return self.search_lexical(query, top_k, path_prefix_filters)
        query_embedding = self.model.encode([query], convert_to_numpy=True)
        search_k = _candidate_search_k(top_k, path_prefix_filters)
        distances, indices = self.index.search(query_embedding.astype("float32"), search_k)
        results = []
        for idx, distance in zip(indices[0], distances[0]):
            if idx < len(self.metadata):
                doc = _hydrate_doc(self.metadata[idx], self.bm25_db_path)
                if not _matches_path_prefix_filters(doc, path_prefix_filters):
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

    def search_lexical(self, query: str, top_k: int = 5, path_prefix_filters: list[str] | None = None):
        results = []
        search_k = _candidate_search_k(top_k, path_prefix_filters)
        for doc_id, score in _bm25_search(self.bm25_db_path, query, search_k):
            base_doc = self.doc_id_to_doc.get(doc_id)
            doc = _hydrate_doc(base_doc, self.bm25_db_path) if base_doc else None
            if not doc:
                continue
            if not _matches_path_prefix_filters(doc, path_prefix_filters):
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
        path_prefix_filters: list[str] | None = None,
        vector_k: int = 40,
        bm25_k: int = 60,
        rerank_k: int = 12,
    ):
        vector_results = self.search_vector(query, vector_k, path_prefix_filters) if self.model is not None else []
        bm25_hits = _bm25_search(self.bm25_db_path, query, bm25_k)
        bm25_results = []
        for doc_id, score in bm25_hits:
            base_doc = self.doc_id_to_doc.get(doc_id)
            doc = _hydrate_doc(base_doc, self.bm25_db_path) if base_doc else None
            if not doc:
                continue
            if not _matches_path_prefix_filters(doc, path_prefix_filters):
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

    def search(self, query: str, top_k: int = 5, path_prefix_filters: list[str] | None = None):
        return self.search_vector(query, top_k, path_prefix_filters)


def _search_v8_source_rag_executor(params: dict) -> str:
    query = params.get("query", "")
    top_k = params.get("top_k", 3)
    path_prefix_filters = _collect_path_prefix_filters(params)
    if not query:
        return json.dumps({"error": "query parameter is required"})
    if not FAISS_AVAILABLE:
        return json.dumps(
            {"error": "V8 source RAG not available. Install: pip install numpy faiss-cpu sentence-transformers"}
        )
    try:
        kb = FAISSV8SourceRag.get_instance()
        top_k = max(1, min(10, int(top_k)))
        results = kb.search(query, top_k, path_prefix_filters or None)
        output = []
        for result in results:
            formatted, truncated, original_lines = _format_rag_result(
                result,
                continuation_hint="use get_v8_source_rag_doc with chunk_offset/max_chunks for more",
            )
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
                    "content_truncated": truncated,
                    "content_line_count": original_lines,
                    "content": formatted,
                }
            )
        return json.dumps(output, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Failed to search V8 source RAG: {str(e)}"})


search_v8_source_rag_tool = IkaTools(
    name="search_v8_source_rag",
    description=(
        "Semantic search over indexed V8 source. Use for code patterns, JIT logic, and runtime behavior. "
        + _V8_SOURCE_FILTER_DESCRIPTION
    ),
    parameters={
        "query": {"type": "string", "description": "Natural language query about V8 source code", "required": True},
        "top_k": {"type": "integer", "description": "Number of results to return (default 3, max 10)", "required": False},
        "path_prefix_filters": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Optional list of V8 source path prefixes. Results match if a file path starts with any prefix in the list. "
                f"Common top-level areas: {_V8_SOURCE_TOP_LEVEL_AREAS_TEXT}. "
                "Specific prefixes such as `compiler/turboshaft` are also allowed."
            ),
            "required": False,
        },
        "path_prefix": {
            "type": "string",
            "description": (
                "Optional single V8 source path prefix. Convenience alias for a one-item `path_prefix_filters` list."
            ),
            "required": False,
        },
        "topic_filter": {
            "type": "string",
            "description": (
                "Deprecated backward-compatible alias. Historically this behaved like a coarse folder filter based on "
                "the first path component. Prefer `path_prefix_filters`."
            ),
            "required": False,
        },
    },
    execute_function=_search_v8_source_rag_executor,
)


def _search_v8_source_rag_hybrid_executor(params: dict) -> str:
    query = params.get("query", "")
    top_k = params.get("top_k", 6)
    path_prefix_filters = _collect_path_prefix_filters(params)
    vector_k = params.get("vector_k", 40)
    bm25_k = params.get("bm25_k", 60)
    rerank_k = params.get("rerank_k", 12)
    if not query:
        return json.dumps({"error": "query parameter is required"})
    if not FAISS_AVAILABLE:
        return json.dumps(
            {"error": "V8 source RAG not available. Install: pip install numpy faiss-cpu sentence-transformers"}
        )
    try:
        kb = FAISSV8SourceRag.get_instance()
        top_k = max(1, min(8, int(top_k)))
        vector_k = max(top_k, min(80, int(vector_k)))
        bm25_k = max(top_k, min(120, int(bm25_k)))
        rerank_k = max(top_k, min(20, int(rerank_k)))
        results = kb.search_hybrid(query, top_k, path_prefix_filters or None, vector_k, bm25_k, rerank_k)
        output = []
        for result in results:
            formatted, truncated, original_lines = _format_rag_result(
                result,
                continuation_hint="use get_v8_source_rag_doc with chunk_offset/max_chunks for more",
            )
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
                    "content_truncated": truncated,
                    "content_line_count": original_lines,
                    "content": formatted,
                }
            )
        return json.dumps(output, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Failed to search V8 source RAG (hybrid): {str(e)}"})


search_v8_source_rag_hybrid_tool = IkaTools(
    name="search_v8_source_rag_hybrid",
    description=(
        "Hybrid search over V8 source for better recall. Use when search_v8_source_rag yields few hits. "
        + _V8_SOURCE_FILTER_DESCRIPTION
    ),
    parameters={
        "query": {"type": "string", "description": "Natural language query about V8 source code", "required": True},
        "top_k": {"type": "integer", "description": "Number of results to return (default 6, max 8)", "required": False},
        "path_prefix_filters": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Optional list of V8 source path prefixes. Results match if a file path starts with any prefix in the list. "
                f"Common top-level areas: {_V8_SOURCE_TOP_LEVEL_AREAS_TEXT}. "
                "Specific prefixes such as `compiler/turboshaft` are also allowed."
            ),
            "required": False,
        },
        "path_prefix": {
            "type": "string",
            "description": (
                "Optional single V8 source path prefix. Convenience alias for a one-item `path_prefix_filters` list."
            ),
            "required": False,
        },
        "topic_filter": {
            "type": "string",
            "description": (
                "Deprecated backward-compatible alias. Historically this behaved like a coarse folder filter based on "
                "the first path component. Prefer `path_prefix_filters`."
            ),
            "required": False,
        },
        "vector_k": {"type": "integer", "description": "Vector candidates to retrieve (default 40, max 80)", "required": False},
        "bm25_k": {"type": "integer", "description": "BM25 candidates to retrieve (default 60, max 120)", "required": False},
        "rerank_k": {"type": "integer", "description": "Candidates to rerank (default 12, max 20)", "required": False},
    },
    execute_function=_search_v8_source_rag_hybrid_executor,
)


def _get_v8_source_rag_doc_executor(params: dict) -> str:
    file_path = params.get("file_path", "")
    chunk_offset = params.get("chunk_offset", 0)
    max_chunks = params.get("max_chunks", RAG_DOC_DEFAULT_MAX_CHUNKS)
    max_total_lines = params.get("max_total_lines", RAG_DOC_DEFAULT_MAX_LINES)
    if not file_path:
        return json.dumps({"error": "file_path parameter is required"})
    if not FAISS_AVAILABLE:
        return json.dumps({"error": "V8 source RAG not available"})
    try:
        kb = FAISSV8SourceRag.get_instance()
        matches = [doc for doc in kb.metadata if doc.get("path") == file_path]
        if not matches:
            return json.dumps({"error": f"V8 source RAG document not found: {file_path}"})
        matches.sort(key=lambda d: d.get("chunk_index", 0))
        chunk_offset = max(0, int(chunk_offset))
        max_chunks = max(1, min(RAG_DOC_MAX_CHUNKS, int(max_chunks)))
        max_total_lines = max(1, min(RAG_DOC_MAX_LINES, int(max_total_lines)))
        selected = matches[chunk_offset : chunk_offset + max_chunks]
        if not selected:
            return json.dumps(
                {
                    "error": f"chunk_offset {chunk_offset} is out of range for document with {len(matches)} chunks"
                }
            )

        output = []
        remaining_lines = max_total_lines
        remaining_chars = RAG_DOC_MAX_CHARS
        batch_truncated = False
        for doc in selected:
            hydrated = _hydrate_doc(doc, kb.bm25_db_path)
            content, content_truncated, original_lines = _truncate_text(
                hydrated.get("content", ""),
                max_lines=remaining_lines,
                max_chars=remaining_chars,
            )
            remaining_lines -= min(original_lines, remaining_lines)
            remaining_chars = max(0, remaining_chars - len(content))
            if content_truncated:
                batch_truncated = True
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
                    "content_truncated": content_truncated,
                    "content_line_count": original_lines,
                    "content": content,
                }
            )
            if remaining_lines <= 0 or remaining_chars <= 0:
                batch_truncated = True
                break
        next_chunk_offset = chunk_offset + len(output)
        has_more = next_chunk_offset < len(matches)
        return json.dumps(
            {
                "file": file_path,
                "returned_chunk_offset": chunk_offset,
                "returned_chunk_count": len(output),
                "total_chunks": len(matches),
                "has_more": has_more,
                "next_chunk_offset": next_chunk_offset if has_more else None,
                "batch_truncated": batch_truncated,
                "chunks": output,
            },
            indent=2,
        )
    except Exception as e:
        return json.dumps({"error": f"Failed to retrieve V8 source RAG document: {str(e)}"})


get_v8_source_rag_doc_tool = IkaTools(
    name="get_v8_source_rag_doc",
    description=(
        "Fetch V8 source document chunks by path from search results. "
        "Results are batched to avoid oversized context windows; use chunk_offset to page through longer files."
    ),
    parameters={
        "file_path": {"type": "string", "description": "The relative file path from search results", "required": True},
        "chunk_offset": {
            "type": "integer",
            "description": "Optional zero-based chunk offset for paging through the document.",
            "required": False,
        },
        "max_chunks": {
            "type": "integer",
            "description": f"Optional chunk batch size (default {RAG_DOC_DEFAULT_MAX_CHUNKS}, max {RAG_DOC_MAX_CHUNKS}).",
            "required": False,
        },
        "max_total_lines": {
            "type": "integer",
            "description": f"Optional total line cap across the returned chunk batch (default {RAG_DOC_DEFAULT_MAX_LINES}, max {RAG_DOC_MAX_LINES}).",
            "required": False,
        },
    },
    execute_function=_get_v8_source_rag_doc_executor,
)
