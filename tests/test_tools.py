import pytest

from incidentpilot.simulator import get_incident
from incidentpilot.runbooks import RunbookIndex
from incidentpilot.tools import dispatch_tool

from test_runbooks import TinyEncoder


def test_read_only_tools_return_scenario_evidence(tmp_path):
    incident = get_incident("db-pool-001")
    index = RunbookIndex(tmp_path / "chroma", TinyEncoder())
    index.index("db-pool", "Database pool", "Inspect pool exhaustion and connection limits.")

    metrics = dispatch_tool("get_metrics", {"incident_id": incident.id})
    logs = dispatch_tool("get_logs", {"incident_id": incident.id})
    deployments = dispatch_tool("get_recent_deployments", {"incident_id": incident.id})
    runbooks = dispatch_tool("search_runbooks", {"query": "database connection pool"}, runbook_index=index)

    assert metrics["active_connections"] == 100
    assert any("pool" in line.lower() for line in logs["lines"])
    assert deployments["deployments"] == []
    assert any("pool" in hit["title"].lower() for hit in runbooks["hits"])


def test_unknown_tool_is_rejected():
    with pytest.raises(ValueError, match="Unknown tool"):
        dispatch_tool("delete_database", {"incident_id": "db-pool-001"})


def test_unknown_incident_is_rejected_by_tool():
    with pytest.raises(KeyError):
        dispatch_tool("get_metrics", {"incident_id": "missing"})


@pytest.mark.parametrize(
    "args",
    [
        {"incident_id": 123},
        {"incident_id": "db-pool-001", "url": "https://example.test"},
        {"incident_id": "db-pool-001", "python": "__import__('os')"},
        {"incident_id": "db-pool-001", "command": "whoami"},
    ],
)
def test_tools_reject_invalid_types_and_executable_instructions(args):
    with pytest.raises(ValueError):
        dispatch_tool("get_metrics", args)


def test_search_runbooks_uses_persistent_semantic_index(tmp_path):
    index = RunbookIndex(tmp_path / "chroma", TinyEncoder())
    index.index("db-pool", "Database pool", "Check pool saturation before proposing remediation.")

    result = dispatch_tool("search_runbooks", {"query": "database pool"}, runbook_index=index)

    assert result["hits"][0]["source"] == "db-pool"
    assert "pool saturation" in result["hits"][0]["text"]
