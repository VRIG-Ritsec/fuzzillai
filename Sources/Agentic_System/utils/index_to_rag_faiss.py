#!/usr/bin/env python3

import os
import sys
from pathlib import Path
from typing import List, Dict, Tuple
import json
import pickle
import hashlib

try:
    import numpy as np
    from sentence_transformers import SentenceTransformer
    import faiss
except ImportError as e:
    print(f"Error: {e}")
    print("Install with: pip3 install --user --break-system-packages numpy sentence-transformers faiss-cpu")
    sys.exit(1)

try:
    from utils.bm25_sqlite import create_bm25_index
except ModuleNotFoundError:
    try:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent))
        from bm25_sqlite import create_bm25_index
    except Exception as e:
        print(f"Error importing bm25_sqlite: {e}")
        create_bm25_index = None


def _find_split_point(content: str, start: int, end: int) -> int:
    for sep in ("\n\n", "\n", " "):
        idx = content.rfind(sep, start, end)
        if idx != -1 and idx > start:
            return idx + len(sep)
    return end


def chunk_document(content: str, chunk_size: int = 1000, overlap: int = 200) -> List[Dict[str, int | str]]:
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

def collect_text_files(base_dir: Path) -> List[Dict[str, object]]:
    documents = []
    
    for root, dirs, files in os.walk(base_dir):
        dirs[:] = [d for d in dirs if not d.startswith('.') and d != '__pycache__']
        
        for file in files:
            if file.startswith('.'):
                continue
                
            filepath = Path(root) / file
            
            if file.endswith('.py') or file.endswith('.pyc'):
                continue
                
            if file.endswith('.txt') or file.endswith('.md'):
                try:
                    content = filepath.read_text(encoding='utf-8', errors='ignore')
                    
                    if len(content.strip()) == 0:
                        continue
                    
                    rel_path = filepath.relative_to(base_dir)
                    
                    if 'v8' in str(rel_path):
                        topic = 'V8 JavaScript Engine'
                    elif 'mdm_js' in str(rel_path) or 'mdn' in str(rel_path).lower():
                        topic = 'MDN JavaScript Reference'
                    elif 'cpp' in str(rel_path):
                        topic = 'C++ Standard Library'
                    elif 'whitepapers' in str(rel_path):
                        topic = 'Fuzzing Research Papers'
                    else:
                        topic = 'General Documentation'
                    
                    chunks = chunk_document(content)
                    if not chunks:
                        continue

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
                        documents.append({
                            'doc_id': doc_id,
                            'path': str(rel_path),
                            'parent_file': str(rel_path),
                            'parent_id': parent_id,
                            'topic': topic,
                            'doc_type': 'documentation',
                            'source': 'knowlage_docs',
                            'content': chunk["text"],
                            'context': context,
                            'chunk_index': chunk_index,
                            'total_chunks': total_chunks,
                            'start_char': chunk["start_char"],
                            'end_char': chunk["end_char"],
                            'start_line': start_line,
                            'end_line': end_line,
                            'char_range': f"{chunk['start_char']}-{chunk['end_char']}",
                        })

                    print(f"Collected: {rel_path}")
                    
                except Exception as e:
                    print(f"Error reading {filepath}: {e}")
                    continue
    
    return documents

def _embedding_text(doc: Dict[str, object]) -> str:
    context = str(doc.get("context", "")).strip()
    content = str(doc.get("content", "")).strip()
    if context:
        return f"{context}\n\n{content}".strip()
    return content


def create_vector_db(documents: List[Dict[str, object]], output_dir: Path):
    print(f"\nCreating embeddings for {len(documents)} documents...")
    
    model = SentenceTransformer('all-MiniLM-L6-v2')
    
    contents = [_embedding_text(doc) for doc in documents]
    embeddings = model.encode(contents, show_progress_bar=True, convert_to_numpy=True)
    
    print(f"\nCreated embeddings with shape: {embeddings.shape}")
    
    dimension = embeddings.shape[1]
    index = faiss.IndexFlatL2(dimension)
    index.add(embeddings.astype('float32'))
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    faiss.write_index(index, str(output_dir / 'v8_knowlagebase.index'))
    
    with open(output_dir / 'v8_knowlagebase_metadata.json', 'w') as f:
        json.dump(documents, f, indent=2)
    
    with open(output_dir / 'v8_knowlagebase_model.pkl', 'wb') as f:
        pickle.dump('all-MiniLM-L6-v2', f)

    if create_bm25_index is not None:
        bm25_path = output_dir / "v8_knowlagebase_bm25.sqlite"
        bm25_docs = [{"doc_id": doc.get("doc_id"), "bm25_text": _embedding_text(doc)} for doc in documents]
        create_bm25_index(bm25_docs, bm25_path)
        print(f"  - BM25: v8_knowlagebase_bm25.sqlite")

    print(f"\n Vector database saved to {output_dir}")
    print(f"  - Index: v8_knowlagebase.index")
    print(f"  - Metadata: v8_knowlagebase_metadata.json")
    print(f"  - Model info: v8_knowlagebase_model.pkl")
    print(f"\nTotal documents indexed: {len(documents)}")

def main():
    if len(sys.argv) > 1:
        base_dir = Path(sys.argv[1]).expanduser()
    else:
        default_base = Path(__file__).resolve().parent.parent / "knowlage_docs"
        base_dir = Path(os.getenv("KNOWLEDGE_DOCS_DIR", str(default_base))).expanduser()

    if not base_dir.exists():
        print(f"Error: Knowledge docs directory does not exist: {base_dir}")
        sys.exit(1)

    print(f"Scanning directory: {base_dir.resolve()}")
    print("Collecting text files...\n")
    
    documents = collect_text_files(base_dir)
    
    if not documents:
        print("No documents found to index!")
        return
    
    default_rag_dir = Path(__file__).resolve().parent.parent / "rag_db"
    rag_base_dir = Path(os.getenv("RAG_BASE_DIR", str(default_rag_dir))).expanduser()
    output_dir = rag_base_dir / "v8_knowlagebase"
    create_vector_db(documents, output_dir)

if __name__ == '__main__':
    main()
