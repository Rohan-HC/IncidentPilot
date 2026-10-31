"""Evidence-gated LangGraph workflow over synthetic observations."""

from typing import Any, TypedDict

import httpx
from langgraph.graph import END, START, StateGraph
from ollama import ResponseError

from incidentpilot.agent import investigate
from incidentpilot.simulator import get_incident
from incidentpilot.tools import get_default_runbook_index


class IncidentState(TypedDict, total=False):
    incident_id: str
    investigation: dict[str, Any]
    evidence_status: str
    proposed_action: str | None
    runbook_hits: list[dict[str, Any]]
    retrieval_error: str | None
    model_error: str | None


def _observation(state: IncidentState, name: str) -> dict[str, Any]:
    for item in state["investigation"]["tool_results"]:
        if item["name"] == name and item["error"] is None:
            return item["result"] or {}
    return {}


def assess_evidence(state: IncidentState) -> IncidentState:
    if state.get("model_error"):
        return {"evidence_status": "blocked", "proposed_action": None}
    metrics = _observation(state, "get_metrics")
    logs = _observation(state, "get_logs").get("lines", [])
    deployments = _observation(state, "get_recent_deployments").get("deployments", [])
    sources = {hit["source"] for hit in state.get("runbook_hits", [])}
    if state["incident_id"] == "db-pool-001":
        sufficient = (
            metrics.get("active_connections", -1) >= metrics.get("pool_limit", float("inf"))
            and any("pool exhausted" in line.lower() for line in logs)
            and "db-pool.md" in sources
        )
        action = "increase_pool_limit"
    elif state["incident_id"] == "deploy-001":
        sufficient = (
            metrics.get("error_rate_percent", 0) >= 10
            and any("payment_endpoint" in line.lower() for line in logs)
            and any(item.get("version") == "v2.4.1" for item in deployments)
            and "faulty-deploy.md" in sources
        )
        action = "rollback_deployment"
    else:
        sufficient = False
        action = None
    return {
        "evidence_status": "sufficient" if sufficient else "insufficient",
        "proposed_action": action if sufficient else None,
    }


def build_graph(model: Any, checkpointer: Any, runbook_index: Any | None = None):
    def retrieve(state: IncidentState) -> IncidentState:
        incident = get_incident(state["incident_id"])
        try:
            index = runbook_index if runbook_index is not None else get_default_runbook_index()
            return {"runbook_hits": index.search(f"{incident.title}. {incident.symptom}"), "retrieval_error": None}
        except Exception as exc:
            return {"runbook_hits": [], "retrieval_error": str(exc)}

    def observe(state: IncidentState) -> IncidentState:
        try:
            result = investigate(get_incident(state["incident_id"]), model, state.get("runbook_hits", []))
            return {"investigation": result.model_dump(), "model_error": None}
        except (ImportError, ConnectionError, OSError, httpx.HTTPError, ResponseError) as exc:
            return {
                "investigation": {
                    "incident_id": state["incident_id"], "answer": "Local model unavailable; investigation blocked.",
                    "tool_results": [], "simulated": True,
                },
                "model_error": str(exc),
            }

    builder = StateGraph(IncidentState)
    builder.add_node("retrieve", retrieve)
    builder.add_node("observe", observe)
    builder.add_node("assess", assess_evidence)
    builder.add_edge(START, "retrieve")
    builder.add_edge("retrieve", "observe")
    builder.add_edge("observe", "assess")
    builder.add_edge("assess", END)
    return builder.compile(checkpointer=checkpointer)
