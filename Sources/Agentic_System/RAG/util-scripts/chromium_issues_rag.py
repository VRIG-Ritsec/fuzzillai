#!/usr/bin/env python3

import gc
import hashlib
import json
import os
import pickle
import sys
from json import JSONDecodeError, JSONDecoder
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Tuple


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


MODEL_NAME = os.getenv("CHROMIUM_RAG_MODEL", "all-MiniLM-L6-v2")
DOC_BATCH_SIZE = max(1, int(os.getenv("CHROMIUM_RAG_DOC_BATCH", "8")))
ENCODE_BATCH_SIZE = max(1, int(os.getenv("CHROMIUM_RAG_ENCODE_BATCH", "2")))
INDEX_FLUSH_INTERVAL = max(1, int(os.getenv("CHROMIUM_RAG_FLUSH_INTERVAL", "10000")))
CHUNK_SIZE = max(128, int(os.getenv("CHROMIUM_RAG_CHUNK_SIZE", "800")))
CHUNK_OVERLAP = max(0, int(os.getenv("CHROMIUM_RAG_CHUNK_OVERLAP", "150")))
MAX_ISSUES = int(os.getenv("CHROMIUM_RAG_MAX_ISSUES", "0") or "0") or None
FORCE_CPU = _env_flag("CHROMIUM_RAG_FORCE_CPU")
DISABLE_BM25 = _env_flag("CHROMIUM_RAG_DISABLE_BM25")
USE_IJSON = _env_flag("CHROMIUM_RAG_USE_IJSON")

_agentic_dir = Path(__file__).resolve().parents[1].parent
if str(_agentic_dir) not in sys.path:
    sys.path.insert(0, str(_agentic_dir))

from RAG.paths import chromium_issues_json, metadata_root

try:
    import numpy as np
    from sentence_transformers import SentenceTransformer
    import faiss
except ImportError as e:
    print(f"Error: {e}")
    print("Install with: pip3 install --user --break-system-packages numpy sentence-transformers faiss-cpu")
    sys.exit(1)

try:
    from tqdm import tqdm
except ImportError:
    def tqdm(iterable=None, *args, **kwargs):
        return iterable if iterable is not None else args[0] if args else iter([])

try:
    from bm25_sqlite import create_bm25_index
except Exception as e:
    print(f"Error importing bm25_sqlite: {e}")
    create_bm25_index = None


def _detect_device() -> Tuple[str, bool]:
    try:
        import torch
        if torch.cuda.is_available():
            gpu_memory = torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
            print(f"GPU detected: {torch.cuda.get_device_name(0)} ({gpu_memory:.1f}GB)")
            if gpu_memory >= 8:
                return "cuda", True
            print(f"  Warning: GPU has only {gpu_memory:.1f}GB VRAM; CPU may be more stable here")
            return "cuda", False
    except ImportError:
        pass
    return "cpu", False


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.text_parts: List[str] = []
        self.skip_tags = {"script", "style", "noscript", "meta", "link", "svg", "path"}
        self.skip = False

    def handle_starttag(self, tag: str, attrs) -> None:
        self.skip = tag.lower() in self.skip_tags

    def handle_endtag(self, tag: str) -> None:
        self.skip = False

    def handle_data(self, data: str) -> None:
        if not self.skip and data.strip():
            self.text_parts.append(data.strip())

    def get_text(self) -> str:
        text = " ".join(self.text_parts)
        text = unescape(text)
        return " ".join(text.split())


def _html_to_text(html: str) -> str:
    if not html:
        return ""
    parser = _TextExtractor()
    parser.feed(html)
    return parser.get_text()


def _find_split_point(content: str, start: int, end: int) -> int:
    for sep in ("\n\n", "\n", " "):
        idx = content.rfind(sep, start, end)
        if idx != -1 and idx > start:
            return idx + len(sep)
    return end


def iter_document_chunks(
    content: str,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> Iterator[Dict[str, int | str]]:
    text_len = len(content)
    if text_len == 0:
        return

    start = 0
    while start < text_len:
        end = min(start + chunk_size, text_len)
        if end < text_len:
            end = _find_split_point(content, start, end)
        chunk_text = content[start:end]
        if chunk_text.strip():
            yield {
                "text": chunk_text,
                "start_char": start,
                "end_char": end,
            }
        if end >= text_len:
            break
        start = max(0, end - overlap)


def chunk_document(content: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[Dict[str, int | str]]:
    return list(iter_document_chunks(content, chunk_size=chunk_size, overlap=overlap))


def count_document_chunks(content: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> int:
    return sum(1 for _ in iter_document_chunks(content, chunk_size=chunk_size, overlap=overlap))


def _line_range(content: str, start_char: int, end_char: int) -> Tuple[int, int]:
    start_line = content.count("\n", 0, start_char) + 1
    end_line = content.count("\n", 0, end_char) + 1
    return start_line, end_line


class _ProgressReader:
    def __init__(self, f, total_bytes: int, interval_mb: float = 1.0):
        self._f = f
        self._total = total_bytes
        self._interval = int(interval_mb * 1024 * 1024)
        self._last_reported = 0
        self._pos = 0

    def read(self, size: int = -1) -> bytes:
        data = self._f.read(size)
        self._pos += len(data)
        if self._pos - self._last_reported >= self._interval:
            pct = 100 * self._pos / self._total if self._total > 0 else 0
            print(f"  JSON parse: {self._pos / 1e6:.1f} MB read ({pct:.1f}%)", flush=True)
            self._last_reported = self._pos
        return data

    def seek(self, pos: int, whence: int = 0) -> int:
        return self._f.seek(pos, whence)

    def tell(self) -> int:
        return self._f.tell()

    def fileno(self) -> int:
        raise OSError("no fileno")


def _iter_issues(json_path: Path) -> Iterable[Dict]:
    with json_path.open("rb") as f:
        first_byte = f.read(1)
    if first_byte == b"[":
        if USE_IJSON:
            try:
                import ijson  # type: ignore
                backend_name = getattr(ijson, "backend_name", "default")
                total_bytes = json_path.stat().st_size
                print(f"Parsing JSON array with ijson ({total_bytes/1e6:.0f} MB, backend={backend_name})")
                sys.stdout.flush()
                with json_path.open("rb") as f:
                    reader = _ProgressReader(f, total_bytes)
                    for item in ijson.items(reader, "item"):
                        yield item
                return
            except Exception as e:
                print(f"ijson failed ({e}), falling back to built-in streaming parser...")
                sys.stdout.flush()
        with json_path.open("r", encoding="utf-8", errors="ignore") as f:
            print("Parsing JSON array with built-in streaming parser")
            yield from _iter_json_array_prefix(f, json_path)
    else:
        with json_path.open("r", encoding="utf-8", errors="ignore") as f:
            for line_number, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except JSONDecodeError as e:
                    print(
                        f"Warning: skipping malformed JSONL record at "
                        f"{json_path}:{line_number}: {e}"
                    )


def _iter_json_array_prefix(stream, json_path: Path, chunk_size: int = 1024 * 1024) -> Iterable[Dict]:
    decoder = JSONDecoder()
    buffer = ""
    pos = 0
    item_count = 0
    saw_array_start = False
    warned_truncated = False

    def fill_buffer() -> bool:
        nonlocal buffer, pos
        chunk = stream.read(chunk_size)
        if not chunk:
            return False
        if pos > 0:
            buffer = buffer[pos:] + chunk
            pos = 0
        else:
            buffer += chunk
        return True

    def skip_ws() -> bool:
        nonlocal pos
        while True:
            while pos < len(buffer) and buffer[pos].isspace():
                pos += 1
            if pos < len(buffer):
                return True
            if not fill_buffer():
                return False

    while True:
        if pos >= len(buffer) and not fill_buffer():
            break

        if not skip_ws():
            break

        if not saw_array_start:
            if buffer[pos] != "[":
                raise ValueError(f"Expected JSON array in {json_path}, found {buffer[pos]!r}")
            saw_array_start = True
            pos += 1
            continue

        if not skip_ws():
            break

        if buffer[pos] == "]":
            return

        while True:
            try:
                item, end = decoder.raw_decode(buffer, pos)
                pos = end
                item_count += 1
                yield item
                break
            except JSONDecodeError as e:
                if fill_buffer():
                    continue
                print(
                    f"Warning: truncated or malformed JSON array in {json_path}. "
                    f"Recovered {item_count} issue records before failure: {e}"
                )
                warned_truncated = True
                return

        if not skip_ws():
            print(
                f"Warning: truncated JSON array in {json_path}. "
                f"Recovered {item_count} issue records before EOF."
            )
            return

        if buffer[pos] == ",":
            pos += 1
            continue
        if buffer[pos] == "]":
            return

        print(
            f"Warning: unexpected character {buffer[pos]!r} after item {item_count} "
            f"in {json_path}. Stopping at valid prefix."
        )
        return

    if saw_array_start and item_count > 0 and not warned_truncated:
        print(
            f"Warning: reached EOF before closing JSON array in {json_path}. "
            f"Recovered {item_count} issue records."
        )


def _extract_issue_text(issue: Dict) -> Tuple[str, str, str]:
    issue_id = issue.get("issue_id") or issue.get("id") or issue.get("issueId") or ""
    url = issue.get("url") or issue.get("issue_url") or issue.get("issueUrl") or ""
    title = issue.get("title") or issue.get("summary") or ""

    parts = []
    if title:
        parts.append(f"Title: {title}")

    description = (
        issue.get("description_html")
        or issue.get("description")
        or issue.get("body_html")
        or issue.get("body")
        or issue.get("content")
        or ""
    )
    description_text = _html_to_text(description)
    if description_text:
        parts.append(description_text)

    comments = issue.get("comments") or issue.get("comment") or []
    if isinstance(comments, list):
        for comment in comments:
            if not isinstance(comment, dict):
                continue
            comment_html = (
                comment.get("comment_html")
                or comment.get("content_html")
                or comment.get("comment")
                or comment.get("content")
                or ""
            )
            comment_text = _html_to_text(comment_html)
            if comment_text:
                parts.append(comment_text)

    return str(issue_id), str(url), "\n".join(parts).strip()


def _stable_id(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def iter_issue_documents(json_path: Path, max_issues: int | None = None) -> Iterator[Dict[str, object]]:
    doc_count = 0
    issue_count = 0
    issues_iter = _iter_issues(json_path)
    if max_issues is not None and max_issues > 0:
        issues_iter = tqdm(issues_iter, total=max_issues, unit="issue", desc="Scanning issues")
    else:
        issues_iter = tqdm(issues_iter, unit="issue", desc="Scanning issues")
    for issue in issues_iter:
        issue_count += 1
        if max_issues is not None and issue_count > max_issues:
            print(f"Reached CHROMIUM_RAG_MAX_ISSUES={max_issues}; stopping early")
            break
        if issue_count % 100 == 0:
            gc.collect()
        issue_id, url, content = _extract_issue_text(issue)
        del issue
        if not content:
            continue

        parent_id = _stable_id(f"issue:{issue_id}")
        start_char = 0
        end_char = len(content)
        start_line, end_line = _line_range(content, start_char, end_char)
        doc_id = _stable_id(f"{issue_id}:full-entry")
        context = (
            f"Topic: Chromium Issue\n"
            f"Issue: {issue_id}\n"
            f"URL: {url}\n"
            f"Entry: full issue\n"
            f"Chars: {start_char}-{end_char}"
        )
        doc_count += 1
        yield {
            "doc_id": doc_id,
            "issue_id": issue_id,
            "url": url,
            "topic": "Chromium Issue",
            "doc_type": "issue",
            "source": "chromium_issues",
            "parent_id": parent_id,
            "content": content,
            "context": context,
            "chunk_index": 0,
            "total_chunks": 1,
            "start_char": start_char,
            "end_char": end_char,
            "start_line": start_line,
            "end_line": end_line,
            "char_range": f"{start_char}-{end_char}",
        }
        del content
        gc.collect()


def _embedding_text(doc: Dict[str, object]) -> str:
    context = str(doc.get("context", "")).strip()
    content = str(doc.get("content", "")).strip()
    if context:
        return f"{context}\n\n{content}".strip()
    return content


def create_vector_db(
    documents_iter: Iterable[Dict[str, object]],
    output_dir: Path,
    doc_batch_size: int = DOC_BATCH_SIZE,
    encode_batch_size: int = ENCODE_BATCH_SIZE,
    flush_interval: int = INDEX_FLUSH_INTERVAL,
    force_cpu: bool = FORCE_CPU,
):
    output_dir.mkdir(parents=True, exist_ok=True)

    device, has_good_gpu = _detect_device()
    if force_cpu:
        device = "cpu"
        print("Using CPU (forced by CHROMIUM_RAG_FORCE_CPU)")
    elif device == "cuda":
        print(f"Using GPU: {device}")
        if not has_good_gpu:
            print("  Warning: limited VRAM may still cause instability on large datasets")
    else:
        print("Using CPU (no GPU detected)")

    if device == "cpu":
        os.environ.setdefault("ACCELERATE_USE_CPU", "1")
        os.environ.setdefault("ACCELERATE_USE_META_DEVICE", "0")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

    print(f"Loading embedding model: {MODEL_NAME}")
    model = SentenceTransformer(MODEL_NAME, device=device)
    if hasattr(model, "eval"):
        model.eval()

    index: faiss.IndexFlatL2 | None = None
    total_count = 0
    batch_docs: List[Dict[str, object]] = []
    batch_texts: List[str] = []
    intermediate_indices: List[Path] = []
    temp_dir = output_dir / "temp_indices"
    temp_dir.mkdir(exist_ok=True)
    next_flush_at = flush_interval

    print("Creating embeddings (streaming)...")
    print(
        f"Memory-oriented settings: doc_batch={doc_batch_size}, "
        f"encode_batch={encode_batch_size}, flush_interval={flush_interval}"
    )
    print("Monitor memory with: watch -n 1 free -h")

    def process_batch() -> None:
        nonlocal index, total_count
        if not batch_docs:
            return
        try:
            embeddings = model.encode(
                batch_texts,
                batch_size=encode_batch_size,
                show_progress_bar=True,
                convert_to_numpy=True,
                device=device,
                normalize_embeddings=False,
            )
            dimension = embeddings.shape[1]
            if index is None:
                index = faiss.IndexFlatL2(dimension)
            index.add(embeddings.astype("float32"))
            total_count += len(batch_docs)
            del embeddings
            gc.collect()
        except Exception as e:
            error_text = str(e).lower()
            if isinstance(e, MemoryError) or "out of memory" in error_text:
                print("\nERROR: embedding batch ran out of memory")
                print("Try smaller settings:")
                print("  export CHROMIUM_RAG_DOC_BATCH=2")
                print("  export CHROMIUM_RAG_ENCODE_BATCH=1")
                print("  export CHROMIUM_RAG_FLUSH_INTERVAL=2000")
            raise

    def save_intermediate_index() -> None:
        nonlocal index
        if index is None or index.ntotal == 0:
            return
        temp_index_path = temp_dir / f"intermediate_{len(intermediate_indices):05d}.index"
        temp_vector_count = index.ntotal
        temp_dim = index.d
        faiss.write_index(index, str(temp_index_path))
        intermediate_indices.append(temp_index_path)
        del index
        gc.collect()
        index = faiss.IndexFlatL2(temp_dim)
        print(f"Saved intermediate index with {temp_vector_count} vectors")

    metadata_path = output_dir / "chromium_issues_rag_metadata.json"
    with open(metadata_path, "w", encoding="utf-8") as mf:
        mf.write("[")
        first_doc = True

        for doc in tqdm(documents_iter, unit="chunk", desc="Indexing"):
            batch_docs.append(doc)
            batch_texts.append(_embedding_text(doc))
            if len(batch_docs) >= doc_batch_size:
                process_batch()
                for d in batch_docs:
                    if not first_doc:
                        mf.write(",")
                    mf.write(json.dumps(d, ensure_ascii=False))
                    first_doc = False
                batch_docs.clear()
                batch_texts.clear()
                mf.flush()
                gc.collect()
                if total_count >= next_flush_at:
                    save_intermediate_index()
                    next_flush_at = total_count + flush_interval
                    gc.collect()

        if batch_docs:
            process_batch()
            for d in batch_docs:
                if not first_doc:
                    mf.write(",")
                mf.write(json.dumps(d, ensure_ascii=False))
                first_doc = False
            batch_docs.clear()
            batch_texts.clear()
            mf.flush()
        mf.write("]")

    if index is not None and index.ntotal > 0:
        save_intermediate_index()

    if not intermediate_indices:
        print("No issue content found to index!")
        metadata_path.unlink(missing_ok=True)
        temp_dir.rmdir()
        return

    print(f"\nCreated embeddings for {total_count} issue entries (encode batch {encode_batch_size})")

    merged_index: faiss.Index | None = None
    for temp_path in tqdm(intermediate_indices, desc="Merging indices", unit="index"):
        temp_index = faiss.read_index(str(temp_path))
        if merged_index is None:
            merged_index = temp_index
        else:
            merged_index.merge_from(temp_index)
        temp_path.unlink(missing_ok=True)
        gc.collect()

    temp_dir.rmdir()
    if merged_index is None:
        print("Failed to build final FAISS index")
        return
    faiss.write_index(merged_index, str(output_dir / "chromium_issues_rag.index"))

    with open(output_dir / "chromium_issues_rag_model.pkl", "wb") as f:
        pickle.dump(MODEL_NAME, f)

    if create_bm25_index is not None and not DISABLE_BM25:
        bm25_path = output_dir / "chromium_issues_rag_bm25.sqlite"
        try:
            import ijson

            def _iter_bm25_docs():
                with open(metadata_path, "rb") as f:
                    for doc in tqdm(ijson.items(f, "item"), desc="BM25 indexing", unit="doc"):
                        doc_id = doc.get("doc_id")
                        bm25_text = _embedding_text(doc)
                        if doc_id and bm25_text.strip():
                            yield {"doc_id": doc_id, "bm25_text": bm25_text}

            create_bm25_index(_iter_bm25_docs(), bm25_path)
            print("  - BM25: chromium_issues_rag_bm25.sqlite")
        except Exception as e:
            print(f"Skipping BM25 index build: {e}")
    elif DISABLE_BM25:
        print("BM25 disabled via CHROMIUM_RAG_DISABLE_BM25")

    print(f"\nVector database saved to {output_dir}")
    print("  - Index: chromium_issues_rag.index")
    print("  - Metadata: chromium_issues_rag_metadata.json")
    print("  - Model info: chromium_issues_rag_model.pkl")
    print(f"\nTotal issue entries indexed: {total_count}")


def main():
    json_path = chromium_issues_json()
    if len(sys.argv) > 1:
        json_path = Path(sys.argv[1]).expanduser()

    if not json_path.exists():
        print(f"Error: Chromium issues JSON not found: {json_path}")
        sys.exit(1)

    output_dir = metadata_root() / "chromium_issues_rag"

    print(f"Loading issues from: {json_path.resolve()}")
    print(
        f"Settings: granularity=issue, doc_batch={DOC_BATCH_SIZE}, encode_batch={ENCODE_BATCH_SIZE}, "
        f"flush_interval={INDEX_FLUSH_INTERVAL}"
    )
    if MAX_ISSUES is not None:
        print(f"Limiting to {MAX_ISSUES} issues")
    documents_iter = iter_issue_documents(json_path, max_issues=MAX_ISSUES)
    try:
        create_vector_db(
            documents_iter,
            output_dir,
            doc_batch_size=DOC_BATCH_SIZE,
            encode_batch_size=ENCODE_BATCH_SIZE,
            flush_interval=INDEX_FLUSH_INTERVAL,
            force_cpu=FORCE_CPU,
        )
    except KeyboardInterrupt:
        print("\nInterrupted by user. Partial metadata and temporary FAISS chunks may exist.")
        sys.exit(1)
    except MemoryError:
        print("\nFATAL: out of memory")
        print("Try:")
        print("  export CHROMIUM_RAG_FORCE_CPU=1")
        print("  export CHROMIUM_RAG_DOC_BATCH=2")
        print("  export CHROMIUM_RAG_ENCODE_BATCH=1")
        print("  export CHROMIUM_RAG_FLUSH_INTERVAL=2000")
        print("  export CHROMIUM_RAG_DISABLE_BM25=1")
        sys.exit(1)


if __name__ == "__main__":
    main()
