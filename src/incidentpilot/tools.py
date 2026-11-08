import os
from functools import lru_cache
from importlib.resources import files
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from incidentpilot.runbooks import RunbookIndex
from incidentpilot.simulator import get_incident


class ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class GetMetrics(ToolArguments):
    """Read synthetic metrics for the incident."""

    incident_id: str = Field(
        strict=True, min_length=1, max_length=128, description="The incident ID being investigated"
    )


class GetLogs(ToolArguments):
    """Read synthetic log lines for the incident."""

    incident_id: str = Field(
        strict=True, min_length=1, max_length=128, description="The incident ID being investigated"
    )


class GetRecentDeployments(ToolArguments):
    """Read synthetic recent deployments for the incident service."""

    incident_id: str = Field(
        strict=True, min_length=1, max_length=128, description="The incident ID being investigated"
    )


class SearchRunbooks(ToolArguments):
    """Search local incident response runbook summaries."""

    query: str = Field(
        strict=True, min_length=1, max_length=512, description="Search terms for a runbook"
    )


MODEL_TOOLS = [GetMetrics, GetLogs, GetRecentDeployments, SearchRunbooks]

TOOL_SCHEMAS: dict[str, type[ToolArguments]] = {
    "get_metrics": GetMetrics,
    "get_logs": GetLogs,
    "get_recent_deployments": GetRecentDeployments,
    "search_runbooks": SearchRunbooks,
}


@lru_cache(maxsize=1)
def get_default_runbook_index() -> RunbookIndex:
    index = RunbookIndex(os.environ.get("INCIDENTPILOT_RUNBOOK_PATH", "data/runbooks.db"))
    directory = files("incidentpilot").joinpath("data", "runbooks")
    for document in directory.iterdir():
        if document.name.endswith(".md"):
            content = document.read_text(encoding="utf-8")
            title = content.splitlines()[0].removeprefix("# ")
            index.index(document.name, title, content)
    return index


def dispatch_tool(
    name: str, args: dict[str, Any], runbook_index: RunbookIndex | None = None
) -> dict[str, Any]:
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
