from incidentpilot.runbooks import RunbookIndex


class TinyEncoder:
    def encode(self, texts):
        vectors = []
        for text in texts:
            lower = text.lower()
            vectors.append(
                [
                    float("database" in lower or "pool" in lower),
                    float("deployment" in lower or "rollback" in lower),
                    float("unrelated" in lower),
                ]
            )
        return vectors


def test_persistent_runbook_search_cites_source(tmp_path):
    first = RunbookIndex(tmp_path / "chroma", TinyEncoder())
    first.index(
        "db-pool",
        "Database pool",
        "Check active connections and pool limit before changing configuration.",
    )

    reopened = RunbookIndex(tmp_path / "chroma", TinyEncoder())
    hits = reopened.search("database pool")

    assert len(hits) == 1
    assert hits[0]["source"] == "db-pool"
    assert "active connections" in hits[0]["text"]


def test_irrelevant_runbook_is_not_returned(tmp_path):
    index = RunbookIndex(tmp_path / "chroma", TinyEncoder())
    index.index("db-pool", "Database pool", "Check active connections.")

    assert index.search("unrelated") == []


def test_numpy_float32_encoder_is_accepted(tmp_path):
    import numpy as np

    class NumpyEncoder:
        def encode(self, texts):
            return np.asarray([[1.0, 0.0] for _ in texts], dtype=np.float32)

    index = RunbookIndex(tmp_path / "chroma", NumpyEncoder())
    index.index("db-pool", "Database pool", "Check active connections.")

    assert index.search("database pool")[0]["source"] == "db-pool"
