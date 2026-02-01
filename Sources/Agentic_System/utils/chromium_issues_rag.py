#!/usr/bin/env python3

import json
import os
import sys
import hashlib
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

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


def chunk_document(content: str, chunk_size: int = 800, overlap: int = 150) -> List[Dict[str, int | str]]:
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


def _iter_issues(json_path: Path) -> Iterable[Dict]:
    with json_path.open("r", encoding="utf-8", errors="ignore") as f:
        first_char = f.read(1)
        f.seek(0)
        if first_char == "[":
            try:
                import ijson  # type: ignore
                for item in ijson.items(f, "item"):
                    yield item
                return
            except Exception:
                pass
            data = json.load(f)
            for item in data:
                yield item
        else:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                yield json.loads(line)


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


def collect_issues(json_path: Path) -> List[Dict[str, object]]:
    documents: List[Dict[str, object]] = []

    for issue in _iter_issues(json_path):
        issue_id, url, content = _extract_issue_text(issue)
        if not content:
            continue

        chunks = chunk_document(content)
        if not chunks:
            continue

        total_chunks = len(chunks)
        parent_id = _stable_id(f"issue:{issue_id}")
        for chunk_index, chunk in enumerate(chunks):
            start_line, end_line = _line_range(content, chunk["start_char"], chunk["end_char"])
            doc_id = _stable_id(f"{issue_id}:{chunk_index}:{chunk['start_char']}:{chunk['end_char']}")
            context = (
                f"Topic: Chromium Issue\n"
                f"Issue: {issue_id}\n"
                f"URL: {url}\n"
                f"Chunk: {chunk_index + 1}/{total_chunks}\n"
                f"Chars: {chunk['start_char']}-{chunk['end_char']}"
            )
            documents.append({
                "doc_id": doc_id,
                "issue_id": issue_id,
                "url": url,
                "topic": "Chromium Issue",
                "doc_type": "issue",
                "source": "chromium_issues",
                "parent_id": parent_id,
                "content": chunk["text"],
                "context": context,
                "chunk_index": chunk_index,
                "total_chunks": total_chunks,
                "start_char": chunk["start_char"],
                "end_char": chunk["end_char"],
                "start_line": start_line,
                "end_line": end_line,
                "char_range": f"{chunk['start_char']}-{chunk['end_char']}",
            })

        if len(documents) % 200 == 0:
            print(f"Collected {len(documents)} issue chunks...")

    return documents


def _embedding_text(doc: Dict[str, object]) -> str:
    context = str(doc.get("context", "")).strip()
    content = str(doc.get("content", "")).strip()
    if context:
        return f"{context}\n\n{content}".strip()
    return content


def create_vector_db(documents: List[Dict[str, object]], output_dir: Path):
    print(f"\nCreating embeddings for {len(documents)} issue chunks...")

    model = SentenceTransformer("all-MiniLM-L6-v2")

    contents = [_embedding_text(doc) for doc in documents]
    embeddings = model.encode(contents, show_progress_bar=True, convert_to_numpy=True)

    print(f"\nCreated embeddings with shape: {embeddings.shape}")

    dimension = embeddings.shape[1]
    index = faiss.IndexFlatL2(dimension)
    index.add(embeddings.astype("float32"))

    output_dir.mkdir(parents=True, exist_ok=True)

    faiss.write_index(index, str(output_dir / "chromium_issues_rag.index"))

    with open(output_dir / "chromium_issues_rag_metadata.json", "w") as f:
        json.dump(documents, f, indent=2)

    with open(output_dir / "chromium_issues_rag_model.pkl", "wb") as f:
        import pickle
        pickle.dump("all-MiniLM-L6-v2", f)

    if create_bm25_index is not None:
        bm25_path = output_dir / "chromium_issues_rag_bm25.sqlite"
        bm25_docs = [{"doc_id": doc.get("doc_id"), "bm25_text": _embedding_text(doc)} for doc in documents]
        create_bm25_index(bm25_docs, bm25_path)
        print("  - BM25: chromium_issues_rag_bm25.sqlite")

    print(f"\nVector database saved to {output_dir}")
    print("  - Index: chromium_issues_rag.index")
    print("  - Metadata: chromium_issues_rag_metadata.json")
    print("  - Model info: chromium_issues_rag_model.pkl")
    print(f"\nTotal issue chunks indexed: {len(documents)}")


def main():
    default_json = Path("/mnt/vdc/chromium_scraping/chromium_issues_from_tracker.json")
    json_path = Path(os.getenv("CHROMIUM_ISSUES_JSON", str(default_json))).expanduser()
    if len(sys.argv) > 1:
        json_path = Path(sys.argv[1]).expanduser()

    if not json_path.exists():
        print(f"Error: Chromium issues JSON not found: {json_path}")
        sys.exit(1)

    default_rag_dir = Path(__file__).resolve().parent.parent / "rag_db"
    rag_base_dir = Path(os.getenv("RAG_BASE_DIR", str(default_rag_dir))).expanduser()
    output_dir = rag_base_dir / "chromium_issues_rag"

    print(f"Loading issues from: {json_path.resolve()}")
    documents = collect_issues(json_path)

    if not documents:
        print("No issue content found to index!")
        return

    create_vector_db(documents, output_dir)


if __name__ == "__main__":
    main()
