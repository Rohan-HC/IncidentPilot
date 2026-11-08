from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from incidentpilot.api import create_app
from incidentpilot.auth import Role
from incidentpilot.runbooks import RunbookIndex

from test_agent import ScriptedModel
from test_runbooks import TinyEncoder


def runbooks(tmp_path):
    index = RunbookIndex(tmp_path / "chroma", TinyEncoder())
    index.index("db-pool.md", "Database pool", "Check active connections and pool limit.")
    return index


def test_api_requires_approval_and_persists_audit(tmp_path, auth_config, auth_headers):
    db = tmp_path / "incidentpilot.db"
    model = ScriptedModel([
        AIMessage(content="", tool_calls=[
            {"name": "get_metrics", "args": {"incident_id": "db-pool-001"}, "id": "m"},
            {"name": "get_logs", "args": {"incident_id": "db-pool-001"}, "id": "l"},
        ]),
        AIMessage(content="The pool is saturated."),
    ])
    client = TestClient(create_app(
        model=model, database_path=db, runbook_index=runbooks(tmp_path), auth_config=auth_config
    ))
    operator = auth_headers(client, Role.OPERATOR)
    approver = auth_headers(client, Role.APPROVER)
    analyzed = client.post("/incidents/db-pool-001/analyze", headers=operator)

    assert analyzed.status_code == 200
    assert analyzed.json()["evidence_status"] == "sufficient"
    proposal_id = analyzed.json()["proposal"]["id"]
    run_id = analyzed.json()["run_id"]
    assert client.post(f"/proposals/{proposal_id}/execute", headers=operator).status_code == 409

    approval = client.post(f"/proposals/{proposal_id}/approve", headers=approver)
    execution = client.post(f"/proposals/{proposal_id}/execute", headers=operator)
    reopened = TestClient(create_app(
        database_path=db, runbook_index=runbooks(tmp_path), auth_config=auth_config
    ))

    assert approval.status_code == 200
    assert execution.json()["verified"] is True
    assert reopened.post(f"/proposals/{proposal_id}/execute", headers=operator).json() == execution.json()
    assert len(reopened.get(f"/proposals/{proposal_id}/audit", headers=operator).json()) == 3
    assert reopened.get(f"/runs/{run_id}", headers=operator).json()["evidence_status"] == "sufficient"


def test_insufficient_evidence_creates_no_proposal(tmp_path, auth_config, auth_headers):
    model = ScriptedModel([AIMessage(content="No evidence to support action.")])
    client = TestClient(create_app(
        model=model,
        database_path=tmp_path / "incidentpilot.db",
        runbook_index=runbooks(tmp_path),
        auth_config=auth_config,
    ))

    response = client.post("/incidents/unknown-001/analyze", headers=auth_headers(client))

    assert response.status_code == 200
    assert response.json()["evidence_status"] == "insufficient"
    assert response.json()["proposal"] is None
