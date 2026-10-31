from typing import Any

from pydantic import BaseModel, Field

from incidentpilot.simulator import get_incident


class GetMetrics(BaseModel):
    """Read synthetic metrics for the incident."""

    incident_id: str = Field(description="The incident ID being investigated")


class GetLogs(BaseModel):
    """Read synthetic log lines for the incident."""

    incident_id: str = Field(description="The incident ID being investigated")


class GetRecentDeployments(BaseModel):
    """Read synthetic recent deployments for the incident service."""

    incident_id: str = Field(description="The incident ID being investigated")


class SearchRunbooks(BaseModel):
    """Search local incident response runbook summaries."""

    query: str = Field(description="Search terms for a runbook")


MODEL_TOOLS = [GetMetrics, GetLogs, GetRecentDeployments, SearchRunbooks]
_RUNBOOKS = [
    {"id": "rb-db-pool", "title": "Database connection pool exhaustion", "summary": "Compare active connections to pool limit; inspect client leaks and timeouts."},
    {"id": "rb-deploy", "title": "Faulty deployment", "summary": "Correlate the error increase with recent deployment and review the change."},
]


def dispatch_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Execute only the four named, read-only local tools."""
    if name == "get_metrics":
        incident = get_incident(GetMetrics.model_validate(args).incident_id)
        return dict(incident.metrics)
    if name == "get_logs":
        incident = get_incident(GetLogs.model_validate(args).incident_id)
        return {"lines": incident.logs}
    if name == "get_recent_deployments":
        incident = get_incident(GetRecentDeployments.model_validate(args).incident_id)
        return {"deployments": incident.deployments}
    if name == "search_runbooks":
        query = SearchRunbooks.model_validate(args).query.lower().split()
        hits = [item for item in _RUNBOOKS if any(word in (item["title"] + " " + item["summary"]).lower() for word in query)]
        return {"hits": hits}
    raise ValueError(f"Unknown tool: {name}")
