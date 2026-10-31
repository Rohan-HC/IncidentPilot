import pytest

from incidentpilot.simulator import get_incident
from incidentpilot.tools import dispatch_tool


def test_read_only_tools_return_scenario_evidence():
    incident = get_incident("db-pool-001")

    metrics = dispatch_tool("get_metrics", {"incident_id": incident.id})
    logs = dispatch_tool("get_logs", {"incident_id": incident.id})
    deployments = dispatch_tool("get_recent_deployments", {"incident_id": incident.id})
    runbooks = dispatch_tool("search_runbooks", {"query": "database connection pool"})

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
