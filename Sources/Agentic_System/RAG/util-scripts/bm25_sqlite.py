#!/usr/bin/env python3

import sqlite3
from pathlib import Path
from typing import Iterable, Tuple


def create_bm25_index(documents: Iterable[dict], db_path: Path, text_key: str = "bm25_text", id_key: str = "doc_id") -> None:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        try:
            conn.execute("CREATE VIRTUAL TABLE docs USING fts5(doc_id UNINDEXED, content, tokenize='porter')")
        except sqlite3.OperationalError as e:
            print(f"FTS5 unavailable, skipping BM25 index: {e}")
            return

        rows = []
        for doc in documents:
            doc_id = str(doc.get(id_key, ""))
            text = str(doc.get(text_key, ""))
            if not doc_id or not text.strip():
                continue
            rows.append((doc_id, text))

            if len(rows) >= 500:
                conn.executemany("INSERT INTO docs(doc_id, content) VALUES (?, ?)", rows)
                rows = []

        if rows:
            conn.executemany("INSERT INTO docs(doc_id, content) VALUES (?, ?)", rows)
        conn.commit()
    finally:
        conn.close()


def search_bm25(db_path: Path, query: str, top_k: int = 50) -> list[Tuple[str, float]]:
    if not query.strip():
        return []

    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.execute(
                "SELECT doc_id, bm25(docs) AS score FROM docs WHERE docs MATCH ? ORDER BY score LIMIT ?",
                (query, int(top_k)),
            )
        except sqlite3.OperationalError:
            return []
        return [(row["doc_id"], float(row["score"])) for row in cur.fetchall()]
    finally:
        conn.close()
