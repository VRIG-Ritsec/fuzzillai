#!/usr/bin/env python3

import gc
import hashlib
import json
import multiprocessing
import os
import pickle
import resource
import sys
from json import JSONDecodeError, JSONDecoder
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Tuple

# Limit background thread/process spawning to reduce memory pressure
os.environ.setdefault("LOKY_MAX_CPU_COUNT", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


MODEL_NAME = os.getenv("RAG_MODEL", "all-MiniLM-L6-v2")
ENCODE_BATCH_SIZE = max(1, int(os.getenv("RAG_EMBED_BATCH", "2")))
INDEX_FLUSH_INTERVAL = max(1, int(os.getenv("RAG_FLUSH_INTERVAL", "200")))
CHUNK_SIZE = max(128, int(os.getenv("RAG_CHUNK_SIZE", "10000")))
CHUNK_OVERLAP = max(0, int(os.getenv("RAG_CHUNK_OVERLAP", "200")))
MAX_FILES = int(os.getenv("RAG_MAX_FILES", "0") or "0") or None
FORCE_CPU = _env_flag("RAG_FORCE_CPU")
DISABLE_BM25 = _env_flag("RAG_DISABLE_BM25")
SUBPROCESS_BATCH_SIZE = max(1, int(os.getenv("RAG_SUBPROCESS_BATCH", "500")))
V8_PATH = os.getenv("V8_PATH", "")

MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024
V8_TEXT_EXTENSIONS = {
    ".cc",
    ".h",
    ".cpp",
    ".hpp",
    ".c",
    ".js",
    ".ts",
    ".json",
    ".txt",
    ".md",
    ".torque",
}

_agentic_dir = Path(__file__).resolve().parents[1].parent
if str(_agentic_dir) not in sys.path:
    sys.path.insert(0, str(_agentic_dir))

from RAG.paths import metadata_root

try:
    import faiss
    import importlib.util
    if importlib.util.find_spec("sentence_transformers") is None:
        raise ImportError("No module named 'sentence_transformers'")
except ImportError as e:
    print(f"Error: {e}")
    print(
        "Install with: pip3 install --user --break-system-packages "
        "sentence-transformers faiss-cpu"
    )
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
            print(f"  Warning: GPU has only {gpu_memory:.1f}GB VRAM; CPU may be more stable")
            return "cuda", False
    except ImportError:
        pass
    return "cpu", False


def _stable_id(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def _find_split_point(content: str, start: int, end: int) -> int:
    for sep in ("\n\n", "\n", " "):
        idx = content.rfind(sep, start, end)
        if idx != -1 and idx > start:
            return idx + len(sep)
    return end


def chunk_ranges(
    content: str,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> List[Tuple[int, int]]:
    ranges: List[Tuple[int, int]] = []
    text_len = len(content)
    if text_len == 0:
        return ranges

    start = 0
    while start < text_len:
        end = min(start + chunk_size, text_len)
        if end < text_len:
            end = _find_split_point(content, start, end)
        if content[start:end].strip():
            ranges.append((start, end))
        if end >= text_len:
            break
        start = max(0, end - overlap)
    return ranges


def _collect_file_paths(base_dir: Path) -> List[Path]:
    file_paths: List[Path] = []
    for root, dirs, files in os.walk(base_dir):
        dirs[:] = [
            d for d in dirs if not d.startswith(".") and d != "__pycache__" and d != "out"
        ]
        for file_name in files:
            if file_name.startswith("."):
                continue
            filepath = Path(root) / file_name
            if filepath.suffix.lower() not in V8_TEXT_EXTENSIONS:
                continue
            try:
                if filepath.stat().st_size > MAX_FILE_SIZE_BYTES:
                    continue
            except OSError:
                continue
            file_paths.append(filepath)
    file_paths.sort(key=lambda p: (p.stat().st_size, str(p)))
    return file_paths


def iter_source_files(base_dir: Path, max_files: int | None = None) -> Iterator[Dict[str, object]]:
    if not base_dir.exists():
        print(f"Error: V8 directory does not exist: {base_dir}")
        sys.exit(1)

    file_paths = _collect_file_paths(base_dir)
    if max_files is not None and max_files > 0:
        file_paths = file_paths[:max_files]
        print(f"Limiting processing to {len(file_paths)} files (RAG_MAX_FILES={max_files})")
    else:
        print(f"Found {len(file_paths)} eligible files")

    files_processed = 0
    for filepath in tqdm(file_paths, desc="Scanning files", unit="file"):
        try:
            file_size = filepath.stat().st_size
        except OSError:
            continue
        if file_size == 0:
            continue

        rel_path = filepath.relative_to(base_dir)
        sub_dir = rel_path.parts[0] if len(rel_path.parts) > 1 else "root"
        topic = f"V8 {sub_dir}"

        files_processed += 1
        if files_processed % 200 == 0:
            rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)
            print(f"  Scanned {files_processed} files | RSS: {rss_mb:.0f} MB")

        # Only pass metadata + absolute path; subprocess reads content itself
        yield {
            "doc_id": _stable_id(str(rel_path)),
            "path": str(rel_path),
            "abs_path": str(filepath),
            "parent_file": str(rel_path),
            "parent_id": _stable_id(str(rel_path)),
            "topic": topic,
            "doc_type": "code",
            "source": "v8_source",
            "chunk_index": 0,
            "total_chunks": 1,
        }



def _iter_json_array_prefix(
    stream, json_path: Path, chunk_size: int = 1024 * 1024
) -> Iterable[Dict]:
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


def _batched(iterable: Iterable, n: int) -> Iterator[List]:
    """Yield successive batches of size n from an iterable."""
    batch: List = []
    for item in iterable:
        batch.append(item)
        if len(batch) >= n:
            yield batch
            batch = []
    if batch:
        yield batch


def _subprocess_encode_worker(
    input_path: str,
    output_path: str,
    model_name: str,
    device: str,
    encode_batch_size: int,
) -> None:
    """Run in a child process. Loads model fresh, encodes a batch, writes
    results to output_path, then exits — OS reclaims all native memory."""
    import numpy as np
    import torch
    from sentence_transformers import SentenceTransformer

    with open(input_path, "rb") as f:
        batch_docs = pickle.load(f)

    model = SentenceTransformer(model_name, device=device)
    model.eval()

    results: List[Tuple[Dict, "np.ndarray"]] = []

    for doc_idx, doc in enumerate(batch_docs):
        content = str(doc.get("content", ""))
        ranges = chunk_ranges(content)
        if not ranges:
            continue

        file_sum = None
        file_chunk_count = 0
        rel_path = str(doc.get("path", ""))
        topic = str(doc.get("topic", ""))
        total_ranges = len(ranges)

        for i in range(0, total_ranges, encode_batch_size):
            batch_ranges = ranges[i : i + encode_batch_size]
            batch_texts = []
            for chunk_index, (start_char, end_char) in enumerate(batch_ranges, start=i):
                batch_texts.append(
                    (
                        f"Topic: {topic}\n"
                        f"File: {rel_path}\n"
                        f"Chunk: {chunk_index + 1}/{total_ranges}\n"
                        f"Chars: {start_char}-{end_char}\n\n"
                        f"{content[start_char:end_char]}"
                    ).strip()
                )

            with torch.inference_mode():
                embeddings = model.encode(
                    batch_texts,
                    batch_size=encode_batch_size,
                    show_progress_bar=False,
                    convert_to_numpy=True,
                    device=device,
                    normalize_embeddings=False,
                )

            batch_sum = embeddings.sum(axis=0)
            file_sum = batch_sum if file_sum is None else file_sum + batch_sum
            file_chunk_count += embeddings.shape[0]
            del batch_texts, embeddings, batch_sum

        if file_sum is None or file_chunk_count == 0:
            continue

        file_embedding = (file_sum / file_chunk_count).astype("float32")
        doc["embedding_chunk_count"] = file_chunk_count
        # Strip content before returning to parent — keeps IPC small
        doc.pop("content", None)
        doc.pop("context", None)
        results.append((doc, file_embedding))
        del file_sum, content

        if (doc_idx + 1) % 50 == 0:
            rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)
            print(f"    [child] {doc_idx + 1}/{len(batch_docs)} files, RSS: {rss_mb:.0f} MB",
                  flush=True)

    del model
    gc.collect()

    with open(output_path, "wb") as f:
        pickle.dump(results, f)


def create_vector_db(
    files_iter: Iterable[Dict[str, object]],
    output_dir: Path,
    encode_batch_size: int = ENCODE_BATCH_SIZE,
    flush_interval: int = INDEX_FLUSH_INTERVAL,
    force_cpu: bool = FORCE_CPU,
    disable_bm25: bool = DISABLE_BM25,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    device, has_good_gpu = _detect_device()
    if force_cpu:
        device = "cpu"
        print("Using CPU (forced)")
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

    index = None
    total_files = 0
    intermediate_indices: List[Path] = []
    temp_dir = output_dir / "temp_indices"
    temp_dir.mkdir(exist_ok=True)
    for stale in temp_dir.glob("intermediate_*.index"):
        stale.unlink(missing_ok=True)
    for stale in temp_dir.glob("batch_*.pkl"):
        stale.unlink(missing_ok=True)
    next_flush_at = flush_interval

    print("Creating one embedding per file (subprocess-batched)...")
    print(
        f"Settings: chunk_size={CHUNK_SIZE}, overlap={CHUNK_OVERLAP}, "
        f"encode_batch={encode_batch_size}, flush_interval={flush_interval}, "
        f"subprocess_batch={SUBPROCESS_BATCH_SIZE}"
    )
    print("Each subprocess loads a fresh model, encodes a batch, then exits.")
    print("This prevents native memory leaks from accumulating.\n")

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
        print(f"  Saved intermediate FAISS index ({temp_vector_count} vectors)")

    ctx = multiprocessing.get_context("spawn")
    metadata_path = output_dir / "v8_source_rag_metadata.json"
    batch_num = 0

    with metadata_path.open("w", encoding="utf-8") as mf:
        mf.write("[")
        first_doc = True

        for batch_docs in _batched(files_iter, SUBPROCESS_BATCH_SIZE):
            batch_num += 1
            input_path = temp_dir / f"batch_input_{batch_num}.pkl"
            output_path = temp_dir / f"batch_output_{batch_num}.pkl"

            rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)
            print(
                f"\n--- Batch {batch_num}: {len(batch_docs)} files "
                f"(parent RSS: {rss_mb:.0f} MB) ---",
                flush=True,
            )

            # Write batch to temp file for child process
            with input_path.open("wb") as f:
                pickle.dump(batch_docs, f)
            del batch_docs

            p = ctx.Process(
                target=_subprocess_encode_worker,
                args=(
                    str(input_path), str(output_path),
                    MODEL_NAME, device, encode_batch_size,
                ),
            )
            p.start()
            p.join()

            input_path.unlink(missing_ok=True)

            if p.exitcode != 0:
                print(
                    f"  WARNING: batch {batch_num} subprocess failed "
                    f"(exit code {p.exitcode})",
                    flush=True,
                )
                output_path.unlink(missing_ok=True)
                continue

            if not output_path.exists():
                print(f"  WARNING: batch {batch_num} produced no output")
                continue

            with output_path.open("rb") as f:
                results = pickle.load(f)
            output_path.unlink(missing_ok=True)

            for doc_meta, embedding in results:
                embedding_2d = embedding.reshape(1, -1)
                if index is None:
                    index = faiss.IndexFlatL2(embedding_2d.shape[1])
                index.add(embedding_2d)
                total_files += 1

                if not first_doc:
                    mf.write(",")
                mf.write(json.dumps(doc_meta, ensure_ascii=False))
                first_doc = False

            mf.flush()
            del results
            gc.collect()

            rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)
            print(
                f"  Batch {batch_num} done: {total_files} files indexed, "
                f"parent RSS: {rss_mb:.0f} MB",
                flush=True,
            )

            if total_files >= next_flush_at:
                save_intermediate_index()
                next_flush_at = total_files + flush_interval

        mf.write("]")

    if index is not None and index.ntotal > 0:
        save_intermediate_index()

    if not intermediate_indices:
        print("No source files found to index.")
        metadata_path.unlink(missing_ok=True)
        temp_dir.rmdir()
        return

    print(f"\nCreated embeddings for {total_files} source files")

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
    faiss.write_index(merged_index, str(output_dir / "v8_source_rag.index"))

    with (output_dir / "v8_source_rag_model.pkl").open("wb") as f:
        pickle.dump(MODEL_NAME, f)

    if create_bm25_index is not None and not disable_bm25:
        bm25_path = output_dir / "v8_source_rag_bm25.sqlite"
        v8_base = Path(V8_PATH).expanduser() if V8_PATH else None

        def _iter_bm25_docs() -> Iterator[Dict[str, str]]:
            with metadata_path.open("r", encoding="utf-8") as f:
                for doc in tqdm(
                    _iter_json_array_prefix(f, metadata_path),
                    desc="BM25 indexing",
                    unit="doc",
                ):
                    doc_id = doc.get("doc_id")
                    if not doc_id:
                        continue
                    # Re-read content from source file since metadata
                    # no longer stores it.
                    rel_path = doc.get("path", "")
                    if v8_base and rel_path:
                        try:
                            content = (v8_base / rel_path).read_text(
                                encoding="utf-8", errors="ignore"
                            )
                        except Exception:
                            content = ""
                    else:
                        content = str(doc.get("content", ""))
                    topic = doc.get("topic", "")
                    context = f"Topic: {topic}\nFile: {rel_path}\nEmbedding granularity: file"
                    bm25_text = f"{context}\n\n{content}".strip()
                    if bm25_text:
                        yield {"doc_id": doc_id, "bm25_text": bm25_text}

        create_bm25_index(_iter_bm25_docs(), bm25_path)
        print("  - BM25: v8_source_rag_bm25.sqlite")
    elif disable_bm25:
        print("BM25 disabled via RAG_DISABLE_BM25")

    print(f"\nVector database saved to {output_dir}")
    print("  - Index: v8_source_rag.index")
    print("  - Metadata: v8_source_rag_metadata.json")
    print("  - Model info: v8_source_rag_model.pkl")
    print(f"\nTotal files indexed: {total_files}")


def recover_from_partial(output_dir: Path) -> bool:
    temp_dir = output_dir / "temp_indices"
    metadata_path = output_dir / "v8_source_rag_metadata.json"
    final_index_path = output_dir / "v8_source_rag.index"

    if not temp_dir.exists() or final_index_path.exists():
        return False

    intermediates = sorted(temp_dir.glob("intermediate_*.index"))
    if not intermediates:
        return False

    print("Recovering from partial run...")
    if metadata_path.exists() and metadata_path.stat().st_size > 0:
        with metadata_path.open("r+b") as mf:
            mf.seek(-1, 2)
            if mf.read(1) != b"]":
                mf.seek(0, 2)
                mf.write(b"]")
                print("  Repaired metadata (appended missing ']')")

    merged_index = None
    for temp_path in tqdm(intermediates, desc="Merging indices", unit="index"):
        temp_index = faiss.read_index(str(temp_path))
        if merged_index is None:
            merged_index = temp_index
        else:
            merged_index.merge_from(temp_index)
        temp_path.unlink(missing_ok=True)
        gc.collect()

    temp_dir.rmdir()
    if merged_index is None:
        return False

    faiss.write_index(merged_index, str(final_index_path))
    with (output_dir / "v8_source_rag_model.pkl").open("wb") as f:
        pickle.dump(MODEL_NAME, f)

    total_count = merged_index.ntotal
    del merged_index
    gc.collect()

    if create_bm25_index is not None and not DISABLE_BM25 and metadata_path.exists():
        bm25_path = output_dir / "v8_source_rag_bm25.sqlite"
        v8_base = Path(V8_PATH).expanduser() if V8_PATH else None

        def _iter_bm25_docs() -> Iterator[Dict[str, str]]:
            with metadata_path.open("r", encoding="utf-8") as f:
                for doc in tqdm(
                    _iter_json_array_prefix(f, metadata_path),
                    desc="BM25 indexing",
                    unit="doc",
                ):
                    doc_id = doc.get("doc_id")
                    if not doc_id:
                        continue
                    rel_path = doc.get("path", "")
                    if v8_base and rel_path:
                        try:
                            content = (v8_base / rel_path).read_text(
                                encoding="utf-8", errors="ignore"
                            )
                        except Exception:
                            content = ""
                    else:
                        content = str(doc.get("content", ""))
                    topic = doc.get("topic", "")
                    context = f"Topic: {topic}\nFile: {rel_path}\nEmbedding granularity: file"
                    bm25_text = f"{context}\n\n{content}".strip()
                    if bm25_text:
                        yield {"doc_id": doc_id, "bm25_text": bm25_text}

        create_bm25_index(_iter_bm25_docs(), bm25_path)
        print("  - BM25: v8_source_rag_bm25.sqlite")
    elif DISABLE_BM25:
        print("BM25 disabled via RAG_DISABLE_BM25")

    print(f"\nRecovery complete. {total_count} file vectors in v8_source_rag.index")
    return True


def main() -> None:
    output_dir = metadata_root() / "v8_source_rag"
    if len(sys.argv) > 1 and sys.argv[1] == "--recover":
        if recover_from_partial(output_dir):
            return
        print("Nothing to recover (no temp_indices or index already complete)")
        sys.exit(1)

    if not V8_PATH:
        print("Error: V8_PATH environment variable not set")
        print("Do: export V8_PATH='path to v8 base dir'")
        print("Example: export V8_PATH=/path/to/v8/v8/src")
        sys.exit(1)

    base_dir = Path(V8_PATH).expanduser()
    if not base_dir.exists():
        print(f"Error: V8_PATH directory does not exist: {base_dir}")
        sys.exit(1)

    print(f"Scanning V8 directory: {base_dir}")
    print("Collecting text files from all subdirectories...")
    print("\nMEMORY WARNING: This script processes large amounts of data.")
    print("This version creates one vector embedding per file.")
    print("Monitor memory with: watch -n 1 free -h\n")

    print(f"Saving to: {output_dir}")
    print(
        f"Settings: chunk_size={CHUNK_SIZE}, overlap={CHUNK_OVERLAP}, "
        f"encode_batch={ENCODE_BATCH_SIZE}, flush_interval={INDEX_FLUSH_INTERVAL}"
    )
    if MAX_FILES is not None:
        print(f"Limiting to {MAX_FILES} files")
    print("Use RAG_DISABLE_BM25=1 to skip the SQLite FTS index if needed.")
    print("Use RAG_EMBED_BATCH=1 for the lowest peak memory.")
    print()

    files_iter = iter_source_files(base_dir, max_files=MAX_FILES)
    try:
        create_vector_db(
            files_iter,
            output_dir,
            encode_batch_size=ENCODE_BATCH_SIZE,
            flush_interval=INDEX_FLUSH_INTERVAL,
            force_cpu=FORCE_CPU,
            disable_bm25=DISABLE_BM25,
        )
    except KeyboardInterrupt:
        print("\nInterrupted by user. Partial metadata and temporary FAISS chunks may exist.")
        sys.exit(1)
    except MemoryError:
        print("\nFATAL: out of memory")
        print("Try:")
        print("  export RAG_FORCE_CPU=1")
        print("  export RAG_EMBED_BATCH=1")
        print("  export RAG_FLUSH_INTERVAL=100")
        print("  export RAG_DISABLE_BM25=1")
        sys.exit(1)
    except Exception as e:
        print(f"\nFATAL ERROR: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
