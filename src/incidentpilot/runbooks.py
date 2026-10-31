"""Local, persistent semantic search over synthetic runbooks."""

from pathlib import Path
from typing import Any


class RunbookIndex:
    def __init__(self, path: str | Path, encoder: Any | None = None):
        import chromadb

        if encoder is None:
            from sentence_transformers import SentenceTransformer

            encoder = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
        self.encoder = encoder
        self.collection = chromadb.PersistentClient(path=str(path)).get_or_create_collection(
            "incidentpilot_runbooks", metadata={"hnsw:space": "cosine"}, embedding_function=None
        )

    def index(self, source: str, title: str, text: str) -> None:
        document = f"{title}\n{text}"
        vector = self.encoder.encode([document])[0]
        self.collection.upsert(
            ids=[source],
            documents=[document],
            metadatas=[{"source": source, "title": title}],
            embeddings=[[float(value) for value in vector]],
        )

    def search(self, query: str, limit: int = 3) -> list[dict[str, Any]]:
        if not query.strip() or self.collection.count() == 0:
            return []
        vector = self.encoder.encode([query])[0]
        found = self.collection.query(
            query_embeddings=[[float(value) for value in vector]],
            n_results=min(limit, self.collection.count()),
            include=["documents", "metadatas", "distances"],
        )
        return [
            {"source": metadata["source"], "title": metadata["title"], "text": document, "distance": distance}
            for metadata, document, distance in zip(
                found["metadatas"][0], found["documents"][0], found["distances"][0]
            )
            if distance <= 0.4
        ]
