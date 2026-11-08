"""Local SQLite-backed semantic search over synthetic runbooks."""

from __future__ import annotations

import json
import sqlite3
from math import sqrt
from pathlib import Path
from typing import Any


def _cosine_distance(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 1.0
    left_norm = sqrt(sum(value * value for value in left))
    right_norm = sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 1.0
    similarity = sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)
    return 1.0 - max(-1.0, min(1.0, similarity))


class RunbookIndex:
    def __init__(self, path: str | Path, encoder: Any | None = None):
        requested = Path(path)
        self.path = requested if requested.suffix else requested / "runbooks.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if encoder is None:
            from sentence_transformers import SentenceTransformer

            encoder = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
        self.encoder = encoder
        with self._connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS runbooks (
                    source TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    text TEXT NOT NULL,
                    embedding_json TEXT NOT NULL
                )
            """)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)

    def index(self, source: str, title: str, text: str) -> None:
        if not source.strip() or not title.strip() or not text.strip():
            raise ValueError("Runbook source, title, and text are required")
        document = f"{title}\n{text}"
        vector = [float(value) for value in self.encoder.encode([document])[0]]
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO runbooks (source, title, text, embedding_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(source) DO UPDATE SET
                    title = excluded.title,
                    text = excluded.text,
                    embedding_json = excluded.embedding_json
                """,
                (source, title, document, json.dumps(vector, separators=(",", ":"))),
            )

    def search(self, query: str, limit: int = 3) -> list[dict[str, Any]]:
        if not query.strip() or limit < 1:
            return []
        query_vector = [float(value) for value in self.encoder.encode([query])[0]]
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT source, title, text, embedding_json FROM runbooks"
            ).fetchall()
        ranked = sorted(
            (
                {
                    "source": source,
                    "title": title,
                    "text": text,
                    "distance": _cosine_distance(query_vector, json.loads(embedding_json)),
                }
                for source, title, text, embedding_json in rows
            ),
            key=lambda item: (item["distance"], item["source"]),
        )
        return [item for item in ranked[:limit] if item["distance"] <= 0.4]
