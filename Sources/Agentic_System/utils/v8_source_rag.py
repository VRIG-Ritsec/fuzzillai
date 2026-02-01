#!/usr/bin/env python3

import os
import sys
import sqlite3
import gc
from pathlib import Path
from typing import List, Dict, Tuple, Iterable
import json
import pickle
import hashlib

def _detect_device():
    try:
        import torch
        if torch.cuda.is_available():
            gpu_memory = torch.cuda.get_device_properties(0).total_memory / (1024**3)
            print(f"GPU detected: {torch.cuda.get_device_name(0)} ({gpu_memory:.1f}GB)")
            if gpu_memory >= 8:
                return 'cuda', True
            else:
                print(f"  Warning: GPU has only {gpu_memory:.1f}GB memory, may not help with large datasets")
                return 'cuda', False
    except ImportError:
        pass
    return 'cpu', False

try:
    import numpy as np
    from sentence_transformers import SentenceTransformer
    import faiss
except ImportError as e:
    print(f"Error: {e}")
    print("Install with: pip3 install --user --break-system-packages numpy sentence-transformers faiss-cpu")
    sys.exit(1)

V8_PATH = os.getenv("V8_PATH", "")

V8_TEXT_EXTENSIONS = {'.cc', '.h', '.cpp', '.hpp', '.c', '.js', '.ts', '.json', '.txt', '.md', '.torque'}

def _find_split_point(content: str, start: int, end: int) -> int:
    for sep in ("\n\n", "\n", " "):
        idx = content.rfind(sep, start, end)
        if idx != -1 and idx > start:
            return idx + len(sep)
    return end


def chunk_document(content: str, chunk_size: int = 1000, overlap: int = 200) -> List[Dict[str, int | str]]:
    """
    Split document into overlapping chunks.
    Returns list of dicts with 'text', 'start_char', 'end_char'.
    """
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
            chunks.append({
                "text": chunk_text,
                "start_char": start,
                "end_char": end,
            })
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


def iter_text_chunks(base_dir: Path, max_files: int = None) -> Iterable[Dict[str, object]]:
    if not base_dir.exists():
        print(f"Error: V8 directory does not exist: {base_dir}")
        sys.exit(1)
    
    print(f"Scanning V8 directory: {base_dir}")
    if max_files:
        print(f"Limiting to {max_files} files for memory efficiency")
    total_chunks = 0
    files_processed = 0

    for root, dirs, files in os.walk(base_dir):
        dirs[:] = [d for d in dirs if not d.startswith('.') and d != '__pycache__' and d != 'out']
        
        for file in files:
            if file.startswith('.'):
                continue
            
            filepath = Path(root) / file
            
            if filepath.suffix.lower() not in V8_TEXT_EXTENSIONS:
                continue
            
            try:
                file_size = filepath.stat().st_size
                if file_size > 10 * 1024 * 1024:
                    print(f"Skipping very large file ({file_size / 1024 / 1024:.1f}MB): {filepath}")
                    continue
                
                content = filepath.read_text(encoding='utf-8', errors='ignore')
                
                if len(content.strip()) == 0:
                    continue
                
                rel_path = filepath.relative_to(base_dir)
                sub_dir = rel_path.parts[0] if len(rel_path.parts) > 1 else 'root'
                
                topic = f"V8 {sub_dir}"
                
                chunks = chunk_document(content)
                if not chunks:
                    continue

                files_processed += 1
                if max_files and files_processed > max_files:
                    print(f"Reached file limit ({max_files}), stopping...")
                    return

                total_for_file = len(chunks)
                for chunk_index, chunk in enumerate(chunks):
                    start_line, end_line = _line_range(content, chunk["start_char"], chunk["end_char"])
                    parent_id = _stable_id(str(rel_path))
                    doc_id = _stable_id(f"{rel_path}:{chunk_index}:{chunk['start_char']}:{chunk['end_char']}")
                    context = (
                        f"Topic: {topic}\n"
                        f"File: {rel_path}\n"
                        f"Chunk: {chunk_index + 1}/{total_for_file}\n"
                        f"Chars: {chunk['start_char']}-{chunk['end_char']}"
                    )
                    total_chunks += 1
                    if total_chunks % 500 == 0:
                        print(f"Collected {total_chunks} chunks from {files_processed} files...")

                    yield {
                        'doc_id': doc_id,
                        'path': str(rel_path),
                        'parent_file': str(rel_path),
                        'parent_id': parent_id,
                        'topic': topic,
                        'doc_type': 'code',
                        'source': 'v8_source',
                        'content': chunk["text"],
                        'context': context,
                        'chunk_index': chunk_index,
                        'total_chunks': total_for_file,
                        'start_char': chunk["start_char"],
                        'end_char': chunk["end_char"],
                        'start_line': start_line,
                        'end_line': end_line,
                        'char_range': f"{chunk['start_char']}-{chunk['end_char']}",
                    }
                
                del content
                del chunks
                if files_processed % 50 == 0:
                    gc.collect()
            except MemoryError as e:
                print(f"\nERROR: Out of memory while processing {filepath}")
                print("Try limiting files: export RAG_MAX_FILES=500")
                raise
            except Exception as e:
                print(f"Error reading {filepath}: {e}")
                continue


def _embedding_text(doc: Dict[str, object]) -> str:
    context = str(doc.get("context", "")).strip()
    content = str(doc.get("content", "")).strip()
    if context:
        return f"{context}\n\n{content}".strip()
    return content


def _init_bm25(db_path: Path) -> sqlite3.Connection | None:
    try:
        conn = sqlite3.connect(str(db_path))
    except sqlite3.Error as e:
        print(f"Failed to open BM25 DB: {e}")
        return None

    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        try:
            conn.execute("CREATE VIRTUAL TABLE docs USING fts5(doc_id UNINDEXED, content, tokenize='porter')")
        except sqlite3.OperationalError as e:
            print(f"FTS5 unavailable, skipping BM25 index: {e}")
            conn.close()
            return None
        return conn
    except sqlite3.Error as e:
        print(f"Failed to initialize BM25 DB: {e}")
        conn.close()
        return None

def create_vector_db(documents: Iterable[Dict[str, object]], output_dir: Path, batch_size: int = 2, flush_interval: int = 500, force_cpu: bool = False):
    print("\nCreating embeddings (streaming)...")
    print(f"Ultra memory-efficient mode: batch_size={batch_size}, flush_interval={flush_interval}")
    
    device, has_good_gpu = _detect_device()
    if force_cpu or os.getenv('RAG_FORCE_CPU', '').lower() in ('1', 'true', 'yes'):
        device = 'cpu'
        print("Using CPU (forced)")
    elif device == 'cuda':
        print(f"Using GPU: {device}")
        if not has_good_gpu:
            print("  Warning: GPU memory may be limited. Consider using CPU for very large datasets.")
    else:
        print("Using CPU (no GPU detected)")
    
    print("WARNING: Processing large codebase. Monitor memory usage with: watch -n 1 free -h")
    if device == 'cuda':
        print("  Monitor GPU memory with: watch -n 1 nvidia-smi")

    if device == 'cpu':
        os.environ.setdefault('ACCELERATE_USE_CPU', '1')
        os.environ.setdefault('ACCELERATE_USE_META_DEVICE', '0')
    os.environ.setdefault('TOKENIZERS_PARALLELISM', 'false')
    
    print("Loading model (this may take a moment)...")
    try:
        model = SentenceTransformer('all-MiniLM-L6-v2', device=device)
        if hasattr(model, 'eval'):
            model.eval()
        print(f"Model loaded successfully on {device}")
    except Exception as e:
        print(f"ERROR: Failed to load model on {device}: {e}")
        if device == 'cuda':
            print("Falling back to CPU...")
            device = 'cpu'
            model = SentenceTransformer('all-MiniLM-L6-v2', device='cpu')
            if hasattr(model, 'eval'):
                model.eval()
            print("Model loaded successfully on CPU")
        else:
            raise
    output_dir.mkdir(parents=True, exist_ok=True)

    metadata_path = output_dir / 'v8_source_rag_metadata.json'
    meta_file = metadata_path.open('w', encoding='utf-8')
    meta_file.write("[\n")
    first_meta = True

    bm25_conn = _init_bm25(output_dir / "v8_source_rag_bm25.sqlite")
    bm25_rows = []

    index = None
    total_docs = 0
    batch_docs = []
    batch_texts = []
    intermediate_indices = []
    temp_dir = output_dir / 'temp_indices'
    temp_dir.mkdir(exist_ok=True)
    
    device_used = device

    def flush_bm25():
        if not bm25_conn or not bm25_rows:
            return
        try:
            bm25_conn.executemany("INSERT INTO docs(doc_id, content) VALUES (?, ?)", bm25_rows)
            bm25_conn.commit()
        except sqlite3.Error as e:
            print(f"BM25 insert failed: {e}")
        bm25_rows.clear()

    def flush_batch():
        nonlocal index, total_docs, first_meta
        if not batch_docs:
            return
        try:
            embeddings = model.encode(batch_texts, show_progress_bar=False, convert_to_numpy=True, device=device_used, normalize_embeddings=False)
            if index is None:
                index = faiss.IndexFlatL2(embeddings.shape[1])
            index.add(embeddings.astype('float32'))

            for doc in batch_docs:
                if not first_meta:
                    meta_file.write(",\n")
                meta_file.write(json.dumps(doc))
                first_meta = False
                total_docs += 1

            meta_file.flush()
            batch_docs.clear()
            batch_texts.clear()
            del embeddings
            gc.collect()
            
            if total_docs % 100 == 0:
                print(f"  Processed {total_docs} documents...")
        except MemoryError as e:
            print(f"\nERROR: Out of memory at {total_docs} documents!")
            print("Try reducing batch size: export RAG_EMBED_BATCH=1")
            print("Or limit files: export RAG_MAX_FILES=500")
            raise

    def save_intermediate_index():
        nonlocal index, intermediate_indices
        if index is None or index.ntotal == 0:
            return
        temp_index_path = temp_dir / f'intermediate_{len(intermediate_indices)}.index'
        faiss.write_index(index, str(temp_index_path))
        intermediate_indices.append(temp_index_path)
        dim = index.d
        ntotal = index.ntotal
        del index
        gc.collect()
        index = faiss.IndexFlatL2(dim)
        print(f"  Saved intermediate index with {ntotal} vectors (total docs: {total_docs})")

    for doc in documents:
        embedding_text = _embedding_text(doc)
        batch_docs.append(doc)
        batch_texts.append(embedding_text)
        if bm25_conn is not None:
            doc_id = doc.get("doc_id")
            if doc_id:
                bm25_rows.append((str(doc_id), embedding_text))
        if len(batch_docs) >= batch_size:
            flush_batch()
            if len(bm25_rows) >= 500:
                flush_bm25()
            if total_docs > 0 and total_docs % flush_interval == 0:
                save_intermediate_index()
                gc.collect()
                print(f"Processed {total_docs} documents, saved intermediate index")

    flush_batch()
    flush_bm25()
    gc.collect()

    if index is not None and index.ntotal > 0:
        save_intermediate_index()
        gc.collect()

    meta_file.write("\n]\n")
    meta_file.close()

    if bm25_conn is not None:
        bm25_conn.close()
        print("  - BM25: v8_source_rag_bm25.sqlite")

    if not intermediate_indices and index is None:
        print("No documents indexed.")
        temp_dir.rmdir()
        return

    print(f"\nMerging {len(intermediate_indices)} intermediate indices...")
    if intermediate_indices:
        merged_index = None
        for i, temp_path in enumerate(intermediate_indices):
            temp_index = faiss.read_index(str(temp_path))
            if merged_index is None:
                merged_index = temp_index
            else:
                merged_index.merge_from(temp_index)
            temp_path.unlink()
        if merged_index is not None:
            faiss.write_index(merged_index, str(output_dir / 'v8_source_rag.index'))
            print(f"  Merged index contains {merged_index.ntotal} vectors")
    else:
        faiss.write_index(index, str(output_dir / 'v8_source_rag.index'))

    temp_dir.rmdir()

    with open(output_dir / 'v8_source_rag_model.pkl', 'wb') as f:
        pickle.dump('all-MiniLM-L6-v2', f)

    print(f"\nVector database saved to {output_dir}")
    print("  - Index: v8_source_rag.index")
    print("  - Metadata: v8_source_rag_metadata.json")
    print("  - Model info: v8_source_rag_model.pkl")
    print(f"\nTotal documents indexed: {total_docs}")

def main():
    if not V8_PATH:
        print("Error: V8_PATH environment variable not set")
        print("Do: export V8_PATH='path to v8 base dir'")
        print("Example: export V8_PATH=/path/to/v8/v8/src")
        sys.exit(1)
    
    base_dir = Path(V8_PATH)
    
    if not base_dir.exists():
        print(f"Error: V8_PATH directory does not exist: {base_dir}")
        sys.exit(1)
    
    print(f"Scanning V8 directory: {base_dir}")
    print("Collecting text files from all subdirectories...")
    print("\nMEMORY WARNING: This script processes large amounts of data.")
    print("Default settings are optimized for memory-constrained systems.")
    print("Monitor memory with: watch -n 1 free -h\n")
    
    max_files = os.getenv("RAG_MAX_FILES")
    max_files = int(max_files) if max_files else None
    if max_files:
        print(f"Limiting processing to {max_files} files (set RAG_MAX_FILES to override)")
    else:
        print("Processing all files. To limit, set: export RAG_MAX_FILES=1000")
    
    print()
    
    default_rag_dir = Path(__file__).resolve().parent.parent / "rag_db"
    rag_base_dir = Path(os.getenv("RAG_BASE_DIR", str(default_rag_dir))).expanduser()
    output_dir = rag_base_dir / "v8_source_rag"
    print(f"Saving to: {output_dir}")
    batch_size = int(os.getenv("RAG_EMBED_BATCH", "2"))
    flush_interval = int(os.getenv("RAG_FLUSH_INTERVAL", "500"))
    force_cpu = os.getenv("RAG_FORCE_CPU", "").lower() in ('1', 'true', 'yes')
    
    print(f"Batch size: {batch_size}, Flush interval: {flush_interval} documents")
    print("\nIMPORTANT: Defaults are set for memory-constrained systems.")
    print("If you have more RAM available, you can increase performance with:")
    print("  export RAG_EMBED_BATCH=4")
    print("  export RAG_FLUSH_INTERVAL=1000")
    print("\nFor even more memory savings, use:")
    print("  export RAG_EMBED_BATCH=1")
    print("  export RAG_FLUSH_INTERVAL=250")
    print("  export RAG_MAX_FILES=500")
    print("\nGPU usage:")
    print("  - GPU will be used automatically if available (8GB+ VRAM recommended)")
    print("  - To force CPU: export RAG_FORCE_CPU=1")
    print("  - With GPU, you can use larger batches: export RAG_EMBED_BATCH=8")
    print()
    
    try:
        documents = iter_text_chunks(base_dir, max_files=max_files)
        create_vector_db(documents, output_dir, batch_size=batch_size, flush_interval=flush_interval, force_cpu=force_cpu)
    except MemoryError as e:
        print("\n" + "="*60)
        print("FATAL: Out of memory!")
        print("="*60)
        print("\nTry these settings:")
        print("  export RAG_EMBED_BATCH=1")
        print("  export RAG_FLUSH_INTERVAL=250")
        print("  export RAG_MAX_FILES=500")
        print("\nOr process specific subdirectories separately.")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n\nInterrupted by user. Partial results may be saved.")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nFATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == '__main__':
    main()
