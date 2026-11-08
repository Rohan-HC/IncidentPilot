"""Evidence-gated LangGraph workflow over synthetic observations."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from time import perf_counter, sleep
from typing import Any, Literal, TypedDict, TypeVar

import httpx
from langgraph.graph import END, START, StateGraph
from ollama import ResponseError

from incidentpilot.agent import investigate
from incidentpilot.observability import COUNTERS, log_event
from incidentpilot.simulator import get_incident
from incidentpilot.tools import get_default_runbook_index

InvestigationStatus = Literal[
    "pending", "running", "completed", "insufficient_evidence", "blocked", "failed"
]
logger = logging.getLogger("incidentpilot")


class IncidentState(TypedDict, total=False):
    incident_id: str
    status: InvestigationStatus
    investigation: dict[str, Any]
    evidence_status: str
    proposed_action: str | None
    runbook_hits: list[dict[str, Any]]
    retrieval_error: str | None
    model_error: str | None
    failure_category: str | None
    retry_count: int
    transitions: list[str]
    retrieval_duration_ms: float


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 2
    backoff_seconds: float = 0.05

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if self.backoff_seconds < 0:
            raise ValueError("backoff_seconds cannot be negative")


T = TypeVar("T")
_TRANSIENT_ERRORS = (ConnectionError, OSError, httpx.TransportError, ResponseError)


def _with_retry(operation: Callable[[], T], policy: RetryPolicy) -> tuple[T, int]:
    retries = 0
    for attempt in range(policy.max_attempts):
        try:
            return operation(), retries
        except _TRANSIENT_ERRORS:
            if attempt + 1 >= policy.max_attempts:
                raise
            retries += 1
            if policy.backoff_seconds:
                sleep(policy.backoff_seconds * (2**attempt))
    raise RuntimeError("retry loop exhausted")


def _observation(state: IncidentState, name: str) -> dict[str, Any]:
    investigation = state.get("investigation") or {}
    for item in investigation.get("tool_results", []):
        if isinstance(item, dict) and item.get("name") == name and item.get("error") is None:
            result = item.get("result")
            return result if isinstance(result, dict) else {}
    return {}


def _terminal(state: IncidentState, status: InvestigationStatus) -> IncidentState:
    transitions = list(state.get("transitions", []))
    previous = state.get("status", "running")
    transitions.append(f"{previous}->{status}")
    COUNTERS.increment(f"workflow_status_{status}_total")
    log_event(logger, "workflow_transition", previous=previous, status=status)
    return {"status": status, "transitions": transitions}


def assess_evidence(state: IncidentState) -> IncidentState:
    if state.get("retrieval_error"):
        return {
            **_terminal(state, "blocked"),
            "evidence_status": "insufficient",
            "proposed_action": None,
            "failure_category": "retrieval_unavailable",
        }
    if state.get("model_error"):
        terminal = "failed" if state.get("failure_category") == "model_failure" else "blocked"
        return {
            **_terminal(state, terminal),
            "evidence_status": "blocked",
            "proposed_action": None,
        }
    investigation = state.get("investigation") or {}
    if investigation.get("status") == "blocked":
        return {
            **_terminal(state, "blocked"),
            "evidence_status": "blocked",
            "proposed_action": None,
            "failure_category": investigation.get("error_category") or "investigation_blocked",
        }

    metrics = _observation(state, "get_metrics")
    logs = _observation(state, "get_logs").get("lines", [])
    deployments = _observation(state, "get_recent_deployments").get("deployments", [])
    sources = {hit.get("source") for hit in state.get("runbook_hits", []) if isinstance(hit, dict)}
    if state["incident_id"] == "db-pool-001":
        sufficient = (
            metrics.get("active_connections", -1) >= metrics.get("pool_limit", float("inf"))
            and any(isinstance(line, str) and "pool exhausted" in line.lower() for line in logs)
            and "db-pool.md" in sources
        )
        action = "increase_pool_limit"
    elif state["incident_id"] == "deploy-001":
        sufficient = (
            metrics.get("error_rate_percent", 0) >= 10
            and any(isinstance(line, str) and "payment_endpoint" in line.lower() for line in logs)
            and any(
                isinstance(item, dict) and item.get("version") == "v2.4.1" for item in deployments
            )
            and "faulty-deploy.md" in sources
        )
        action = "rollback_deployment"
    else:
        sufficient = False
        action = None
    status: InvestigationStatus = "completed" if sufficient else "insufficient_evidence"
    return {
        **_terminal(state, status),
        "evidence_status": "sufficient" if sufficient else "insufficient",
        "proposed_action": action if sufficient else None,
        "failure_category": None,
    }


def build_graph(
    model: Any,
    checkpointer: Any,
    runbook_index: Any | None = None,
    *,
    retry_policy: RetryPolicy | None = None,
    tool_dispatcher: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
    interrupt_after: list[str] | None = None,
):
    policy = retry_policy or RetryPolicy()

    def retrieve(state: IncidentState) -> IncidentState:
        retrieval_started = perf_counter()
        incident = get_incident(state["incident_id"])
        transitions = list(state.get("transitions", []))
        transitions.append(f"{state.get('status', 'pending')}->running")
        try:
            index = runbook_index if runbook_index is not None else get_default_runbook_index()
            hits, retries = _with_retry(
                lambda: index.search(f"{incident.title}. {incident.symptom}"), policy
            )
            duration_ms = round((perf_counter() - retrieval_started) * 1000, 3)
            COUNTERS.increment("retrievals_total")
            log_event(logger, "retrieval_completed", duration_ms=duration_ms, hit_count=len(hits))
            return {
                "status": "running",
                "transitions": transitions,
                "runbook_hits": hits,
                "retrieval_error": None,
                "retry_count": state.get("retry_count", 0) + retries,
                "retrieval_duration_ms": duration_ms,
            }
        except _TRANSIENT_ERRORS as exc:
            duration_ms = round((perf_counter() - retrieval_started) * 1000, 3)
            log_event(
                logger,
                "retrieval_failed",
                category="retrieval_unavailable",
                duration_ms=duration_ms,
            )
            return {
                "status": "running",
                "transitions": transitions,
                "runbook_hits": [],
                "retrieval_error": str(exc),
                "failure_category": "retrieval_unavailable",
                "retry_count": state.get("retry_count", 0) + policy.max_attempts - 1,
                "retrieval_duration_ms": duration_ms,
            }
        except Exception:
            duration_ms = round((perf_counter() - retrieval_started) * 1000, 3)
            log_event(
                logger, "retrieval_failed", category="retrieval_failure", duration_ms=duration_ms
            )
            return {
                "status": "running",
                "transitions": transitions,
                "runbook_hits": [],
                "retrieval_error": "Runbook retrieval failed",
                "failure_category": "retrieval_failure",
                "retry_count": state.get("retry_count", 0),
                "retrieval_duration_ms": duration_ms,
            }

    def observe(state: IncidentState) -> IncidentState:
        try:
            result, retries = _with_retry(
                lambda: investigate(
                    get_incident(state["incident_id"]),
                    model,
                    state.get("runbook_hits", []),
                    **({"tool_dispatcher": tool_dispatcher} if tool_dispatcher is not None else {}),
                ),
                policy,
            )
            return {
                "investigation": result.model_dump(),
                "model_error": None,
                "retry_count": state.get("retry_count", 0) + retries,
            }
        except _TRANSIENT_ERRORS:
            return {
                "investigation": {
                    "incident_id": state["incident_id"],
                    "answer": "Local model unavailable; investigation blocked.",
                    "tool_results": [],
                    "status": "blocked",
                    "error_category": "model_unavailable",
                    "simulated": True,
                },
                "model_error": "Local model is unavailable",
                "failure_category": "model_unavailable",
                "retry_count": state.get("retry_count", 0) + policy.max_attempts - 1,
            }
        except Exception:
            return {
                "investigation": {
                    "incident_id": state["incident_id"],
                    "answer": "Model invocation failed; investigation stopped.",
                    "tool_results": [],
                    "status": "blocked",
                    "error_category": "model_failure",
                    "simulated": True,
                },
                "model_error": "Model invocation failed",
                "failure_category": "model_failure",
                "retry_count": state.get("retry_count", 0),
            }

    builder = StateGraph(IncidentState)
    builder.add_node("retrieve", retrieve)
    builder.add_node("observe", observe)
    builder.add_node("assess", assess_evidence)
    builder.add_edge(START, "retrieve")
    builder.add_edge("retrieve", "observe")
    builder.add_edge("observe", "assess")
    builder.add_edge("assess", END)
    return builder.compile(checkpointer=checkpointer, interrupt_after=interrupt_after)
