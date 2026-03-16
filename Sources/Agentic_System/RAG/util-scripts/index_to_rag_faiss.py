#!/usr/bin/env python3

import gc
import hashlib
import json
import os
import pickle
import sys
from json import JSONDecodeError, JSONDecoder
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Tuple


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


MODEL_NAME = os.getenv("KNOWLEDGE_RAG_MODEL", "all-MiniLM-L6-v2")
DOC_BATCH_SIZE = max(1, int(os.getenv("KNOWLEDGE_RAG_DOC_BATCH", "32")))
ENCODE_BATCH_SIZE = max(1, int(os.getenv("KNOWLEDGE_RAG_ENCODE_BATCH", "4")))
INDEX_FLUSH_INTERVAL = max(1, int(os.getenv("KNOWLEDGE_RAG_FLUSH_INTERVAL", "4000")))
CHUNK_SIZE = max(128, int(os.getenv("KNOWLEDGE_RAG_CHUNK_SIZE", "1000")))
CHUNK_OVERLAP = max(0, int(os.getenv("KNOWLEDGE_RAG_CHUNK_OVERLAP", "200")))
MAX_FILES = int(os.getenv("KNOWLEDGE_RAG_MAX_FILES", "0") or "0") or None
FORCE_CPU = _env_flag("KNOWLEDGE_RAG_FORCE_CPU")
DISABLE_BM25 = _env_flag("KNOWLEDGE_RAG_DISABLE_BM25")

_agentic_dir = Path(__file__).resolve().parents[1].parent
if str(_agentic_dir) not in sys.path:
    sys.path.insert(0, str(_agentic_dir))

from RAG.paths import knowledge_docs_root, metadata_root

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


def _find_split_point(content: str, start: int, end: int) -> int:
    for sep in ("\n\n", "\n", " "):
        idx = content.rfind(sep, start, end)
        if idx != -1 and idx > start:
            return idx + len(sep)
    return end


def chunk_document(
    content: str,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> List[Dict[str, int | str]]:
    chunks = []
    text_len = len(content)
    if text_len == 0:
        return chunks

    start = 0
    while start < text_len:
        end = min(start + chunk_size, text_len)
        if end < text_len:
            end = _find_split_point(content, start, end)
        chunk_text = content[start:end]
        if chunk_text.strip():
            chunks.append(
                {
                    "text": chunk_text,
                    "start_char": start,
                    "end_char": end,
                }
            )
        if end >= text_len:
            break
        start = max(0, end - overlap)

    return chunks


def _line_range(content: str, start_char: int, end_char: int) -> Tuple[int, int]:
    start_line = content.count("\n", 0, start_char) + 1
    end_line = content.count("\n", 0, end_char) + 1
    return start_line, end_line


def _stable_id(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def _topic_for_path(rel_path: Path) -> str:
    rel_text = str(rel_path)
    if "v8" in rel_text:
        return "V8 JavaScript Engine"
    if "mdm_js" in rel_text or "mdn" in rel_text.lower():
        return "MDN JavaScript Reference"
    if "cpp" in rel_text:
        return "C++ Standard Library"
    if "whitepapers" in rel_text:
        return "Fuzzing Research Papers"
    return "General Documentation"


def _iter_text_paths(base_dir: Path) -> List[Path]:
    file_paths: List[Path] = []
    for root, dirs, files in os.walk(base_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]
        for file in files:
            if file.startswith("."):
                continue
            if file.endswith(".py") or file.endswith(".pyc"):
                continue
            if file.endswith(".txt") or file.endswith(".md"):
                file_paths.append(Path(root) / file)
    file_paths.sort()
    return file_paths


def iter_documents(base_dir: Path, max_files: int | None = None) -> Iterator[Dict[str, object]]:
    file_paths = _iter_text_paths(base_dir)
    if max_files is not None and max_files > 0:
        file_paths = file_paths[:max_files]
        print(f"Limiting to {len(file_paths)} files (KNOWLEDGE_RAG_MAX_FILES={max_files})")
    else:
        print(f"Found {len(file_paths)} eligible documentation files")

    files_processed = 0
    chunks_emitted = 0
    for filepath in tqdm(file_paths, desc="Scanning files", unit="file"):
        try:
            content = filepath.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            print(f"Error reading {filepath}: {e}")
            continue

        if not content.strip():
            continue

        rel_path = filepath.relative_to(base_dir)
        topic = _topic_for_path(rel_path)
        chunks = chunk_document(content)
        if not chunks:
            continue

        files_processed += 1
        total_chunks = len(chunks)
        parent_id = _stable_id(str(rel_path))

        for chunk_index, chunk in enumerate(chunks):
            start_line, end_line = _line_range(content, chunk["start_char"], chunk["end_char"])
            doc_id = _stable_id(f"{rel_path}:{chunk_index}:{chunk['start_char']}:{chunk['end_char']}")
            context = (
                f"Topic: {topic}\n"
                f"File: {rel_path}\n"
                f"Chunk: {chunk_index + 1}/{total_chunks}\n"
                f"Chars: {chunk['start_char']}-{chunk['end_char']}"
            )
            chunks_emitted += 1
            yield {
                "doc_id": doc_id,
                "path": str(rel_path),
                "parent_file": str(rel_path),
                "parent_id": parent_id,
                "topic": topic,
                "doc_type": "documentation",
                "source": "knowlage_docs",
                "content": chunk["text"],
                "context": context,
                "chunk_index": chunk_index,
                "total_chunks": total_chunks,
                "start_char": chunk["start_char"],
                "end_char": chunk["end_char"],
                "start_line": start_line,
                "end_line": end_line,
                "char_range": f"{chunk['start_char']}-{chunk['end_char']}",
            }

        if files_processed % 100 == 0:
            print(f"Processed {files_processed} files and emitted {chunks_emitted} chunks")
            gc.collect()


def _embedding_text(doc: Dict[str, object]) -> str:
    context = str(doc.get("context", "")).strip()
    content = str(doc.get("content", "")).strip()
    if context:
        return f"{context}\n\n{content}".strip()
    return content


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
                    f"Recovered {item_count} records before failure: {e}"
                )
                warned_truncated = True
                return

        if not skip_ws():
            print(
                f"Warning: truncated JSON array in {json_path}. "
                f"Recovered {item_count} records before EOF."
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
            f"Recovered {item_count} records."
        )


def create_vector_db(
    documents_iter: Iterable[Dict[str, object]],
    output_dir: Path,
    doc_batch_size: int = DOC_BATCH_SIZE,
    encode_batch_size: int = ENCODE_BATCH_SIZE,
    flush_interval: int = INDEX_FLUSH_INTERVAL,
    force_cpu: bool = FORCE_CPU,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    device, has_good_gpu = _detect_device()
    if force_cpu:
        device = "cpu"
        print("Using CPU (forced by KNOWLEDGE_RAG_FORCE_CPU)")
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

    index = None
    total_count = 0
    batch_docs: List[Dict[str, object]] = []
    batch_texts: List[str] = []
    intermediate_indices: List[Path] = []
    temp_dir = output_dir / "temp_indices"
    temp_dir.mkdir(exist_ok=True)
    next_flush_at = flush_interval

    print("Creating embeddings (streaming)...")
    print(
        f"Memory-oriented settings: chunk_size={CHUNK_SIZE}, overlap={CHUNK_OVERLAP}, "
        f"doc_batch={doc_batch_size}, encode_batch={encode_batch_size}, flush_interval={flush_interval}"
    )
    print("This configuration is tuned for a 32 GB machine: moderate throughput with lower peak memory.")
    print("Monitor memory with: watch -n 1 free -h")

    def process_batch() -> None:
        nonlocal index, total_count
        if not batch_docs:
            return
        embeddings = model.encode(
            batch_texts,
            batch_size=encode_batch_size,
            show_progress_bar=False,
            convert_to_numpy=True,
            device=device,
            normalize_embeddings=False,
        )
        if index is None:
            index = faiss.IndexFlatL2(embeddings.shape[1])
        index.add(embeddings.astype("float32"))
        total_count += len(batch_docs)
        del embeddings
        gc.collect()

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

    metadata_path = output_dir / "v8_knowlagebase_metadata.json"
    with metadata_path.open("w", encoding="utf-8") as mf:
        mf.write("[")
        first_doc = True

        for doc in tqdm(documents_iter, desc="Indexing chunks", unit="chunk"):
            batch_docs.append(doc)
            batch_texts.append(_embedding_text(doc))
            if len(batch_docs) >= doc_batch_size:
                process_batch()
                for batch_doc in batch_docs:
                    if not first_doc:
                        mf.write(",")
                    mf.write(json.dumps(batch_doc, ensure_ascii=False))
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
            for batch_doc in batch_docs:
                if not first_doc:
                    mf.write(",")
                mf.write(json.dumps(batch_doc, ensure_ascii=False))
                first_doc = False
            batch_docs.clear()
            batch_texts.clear()
            mf.flush()
        mf.write("]")

    if index is not None and index.ntotal > 0:
        save_intermediate_index()

    if not intermediate_indices:
        print("No documents found to index!")
        metadata_path.unlink(missing_ok=True)
        temp_dir.rmdir()
        return

    print(f"\nCreated embeddings for {total_count} documentation chunks")

    merged_index = None
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
    faiss.write_index(merged_index, str(output_dir / "v8_knowlagebase.index"))

    with (output_dir / "v8_knowlagebase_model.pkl").open("wb") as f:
        pickle.dump(MODEL_NAME, f)

    if create_bm25_index is not None and not DISABLE_BM25:
        bm25_path = output_dir / "v8_knowlagebase_bm25.sqlite"

        def _iter_bm25_docs() -> Iterator[Dict[str, str]]:
            with metadata_path.open("r", encoding="utf-8") as f:
                for doc in tqdm(
                    _iter_json_array_prefix(f, metadata_path),
                    desc="BM25 indexing",
                    unit="doc",
                ):
                    doc_id = doc.get("doc_id")
                    bm25_text = _embedding_text(doc)
                    if doc_id and bm25_text.strip():
                        yield {"doc_id": doc_id, "bm25_text": bm25_text}

        create_bm25_index(_iter_bm25_docs(), bm25_path)
        print("  - BM25: v8_knowlagebase_bm25.sqlite")
    elif DISABLE_BM25:
        print("BM25 disabled via KNOWLEDGE_RAG_DISABLE_BM25")

    print(f"\nVector database saved to {output_dir}")
    print("  - Index: v8_knowlagebase.index")
    print("  - Metadata: v8_knowlagebase_metadata.json")
    print("  - Model info: v8_knowlagebase_model.pkl")
    print(f"\nTotal documents indexed: {total_count}")


def main() -> None:
    if len(sys.argv) > 1:
        base_dir = Path(sys.argv[1]).expanduser()
    else:
        base_dir = knowledge_docs_root()

    if not base_dir.exists():
        print(f"Error: Knowledge docs directory does not exist: {base_dir}")
        sys.exit(1)

    output_dir = metadata_root() / "v8_knowlagebase"

    print(f"Scanning directory: {base_dir.resolve()}")
    print(
        f"Settings: chunk_size={CHUNK_SIZE}, overlap={CHUNK_OVERLAP}, "
        f"doc_batch={DOC_BATCH_SIZE}, encode_batch={ENCODE_BATCH_SIZE}, "
        f"flush_interval={INDEX_FLUSH_INTERVAL}"
    )
    if MAX_FILES is not None:
        print(f"Limiting to {MAX_FILES} files")
    print("Progress bars require tqdm, which is already listed in requirements.txt")

    documents_iter = iter_documents(base_dir, max_files=MAX_FILES)
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
        print("  export KNOWLEDGE_RAG_FORCE_CPU=1")
        print("  export KNOWLEDGE_RAG_DOC_BATCH=16")
        print("  export KNOWLEDGE_RAG_ENCODE_BATCH=2")
        print("  export KNOWLEDGE_RAG_FLUSH_INTERVAL=2000")
        print("If you want more speed instead, try:")
        print("  export KNOWLEDGE_RAG_DOC_BATCH=48")
        print("  export KNOWLEDGE_RAG_ENCODE_BATCH=6")
        print("  export KNOWLEDGE_RAG_FLUSH_INTERVAL=6000")
        print("  export KNOWLEDGE_RAG_MAX_FILES=1000")
        print("  export KNOWLEDGE_RAG_DISABLE_BM25=1")
        sys.exit(1)


if __name__ == "__main__":
    main()
