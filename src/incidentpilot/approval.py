"""Durable approval and audit boundary for simulated actions only."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

ALLOWED_ACTIONS = {
    ("db-pool-001", "increase_pool_limit"),
    ("deploy-001", "rollback_deployment"),
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _simulate(incident_id: str, action: str) -> dict[str, Any]:
    if (incident_id, action) == ("db-pool-001", "increase_pool_limit"):
        before = {"active_connections": 100, "pool_limit": 100, "p95_latency_ms": 2400}
        after = {"active_connections": 80, "pool_limit": 120, "p95_latency_ms": 350}
        verified = (
            after["active_connections"] < after["pool_limit"]
            and after["p95_latency_ms"] < before["p95_latency_ms"]
        )
    elif (incident_id, action) == ("deploy-001", "rollback_deployment"):
        before = {"version": "v2.4.1", "error_rate_percent": 18.0}
        after = {"version": "v2.4.0", "error_rate_percent": 1.2}
        verified = (
            after["version"] == "v2.4.0"
            and after["error_rate_percent"] < before["error_rate_percent"]
        )
    else:
        raise ValueError("Action is not allowlisted")
    return {
        "simulated": True,
        "action": action,
        "before": before,
        "after": after,
        "verified": verified,
    }


class ApprovalStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS proposals (
                    id TEXT PRIMARY KEY, run_id TEXT UNIQUE NOT NULL, incident_id TEXT NOT NULL,
                    action TEXT NOT NULL, status TEXT NOT NULL, actor TEXT,
                    result_json TEXT, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, proposal_id TEXT NOT NULL,
                    event TEXT NOT NULL, actor TEXT, at TEXT NOT NULL, detail_json TEXT
                );
            """)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _proposal(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["result"] = json.loads(result.pop("result_json")) if result["result_json"] else None
        return result

    @staticmethod
    def _load(conn: sqlite3.Connection, proposal_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
        if row is None:
            raise KeyError(proposal_id)
        return row

    def create_proposal(
        self, run_id: str, incident_id: str, action: str, evidence_status: str
    ) -> dict[str, Any]:
        if (incident_id, action) not in ALLOWED_ACTIONS:
            raise ValueError("Action is not allowlisted for this incident")
        if evidence_status != "sufficient":
            raise ValueError("Insufficient evidence for a proposal")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM proposals WHERE run_id = ?", (run_id,)
            ).fetchone()
            if existing:
                if (existing["incident_id"], existing["action"]) != (incident_id, action):
                    raise ValueError("Run already has a different proposal")
                return self._proposal(existing)
            proposal_id = str(uuid4())
            conn.execute(
                "INSERT INTO proposals VALUES (?, ?, ?, ?, 'pending', NULL, NULL, ?)",
                (proposal_id, run_id, incident_id, action, _now()),
            )
            self._audit(conn, proposal_id, "proposed")
            return self._proposal(self._load(conn, proposal_id))

    @staticmethod
    def _audit(
        conn: sqlite3.Connection,
        proposal_id: str,
        event: str,
        actor: str | None = None,
        detail: dict | None = None,
    ) -> None:
        conn.execute(
            "INSERT INTO audit (proposal_id, event, actor, at, detail_json) VALUES (?, ?, ?, ?, ?)",
            (proposal_id, event, actor, _now(), json.dumps(detail) if detail is not None else None),
        )

    def get_proposal(self, proposal_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            return self._proposal(self._load(conn, proposal_id))

    def check_connection(self) -> bool:
        try:
            with self._connect() as conn:
                return conn.execute("SELECT 1").fetchone()[0] == 1
        except sqlite3.Error:
            return False

    def _decide(self, proposal_id: str, actor: str, status: str) -> dict[str, Any]:
        if not actor.strip():
            raise ValueError("Actor is required for explicit approval or denial")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._load(conn, proposal_id)
            if row["status"] == status:
                return self._proposal(row)
            if row["status"] != "pending":
                raise ValueError("Proposal is no longer pending")
            conn.execute(
                "UPDATE proposals SET status = ?, actor = ? WHERE id = ?",
                (status, actor, proposal_id),
            )
            self._audit(conn, proposal_id, status, actor)
            return self._proposal(self._load(conn, proposal_id))

    def approve(self, proposal_id: str, actor: str) -> dict[str, Any]:
        return self._decide(proposal_id, actor, "approved")

    def deny(self, proposal_id: str, actor: str) -> dict[str, Any]:
        return self._decide(proposal_id, actor, "denied")

    def execute(self, proposal_id: str, actor: str) -> dict[str, Any]:
        if not actor.strip():
            raise ValueError("Actor is required for execution")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._load(conn, proposal_id)
            if row["status"] == "executed":
                return json.loads(row["result_json"])
            if row["status"] != "approved":
                raise ValueError("Proposal must be approved before execution")
            result = _simulate(row["incident_id"], row["action"])
            conn.execute(
                "UPDATE proposals SET status = 'executed', result_json = ? WHERE id = ?",
                (json.dumps(result), proposal_id),
            )
            self._audit(conn, proposal_id, "executed", actor, result)
            return result

    def audit(self, proposal_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            self._load(conn, proposal_id)
            rows = conn.execute(
                "SELECT * FROM audit WHERE proposal_id = ? ORDER BY id", (proposal_id,)
            ).fetchall()
            return [dict(row) for row in rows]
