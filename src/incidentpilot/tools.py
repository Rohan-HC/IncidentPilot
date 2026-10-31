from functools import lru_cache
from importlib.resources import files
import os
from typing import Any

from pydantic import BaseModel, Field

from incidentpilot.simulator import get_incident
from incidentpilot.runbooks import RunbookIndex


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

@lru_cache(maxsize=1)
def get_default_runbook_index() -> RunbookIndex:
    index = RunbookIndex(os.environ.get("INCIDENTPILOT_CHROMA_PATH", "data/chroma"))
    directory = files("incidentpilot").joinpath("data", "runbooks")
    for document in directory.iterdir():
        if document.name.endswith(".md"):
            content = document.read_text(encoding="utf-8")
            title = content.splitlines()[0].removeprefix("# ")
            index.index(document.name, title, content)
    return index


def dispatch_tool(name: str, args: dict[str, Any], runbook_index: RunbookIndex | None = None) -> dict[str, Any]:
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
        query = SearchRunbooks.model_validate(args).query
        return {"hits": (runbook_index or get_default_runbook_index()).search(query)}
    raise ValueError(f"Unknown tool: {name}")
