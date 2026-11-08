import io
import json
import logging

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite import SqliteSaver

from incidentpilot.agent import investigate
from incidentpilot.api import create_app
from incidentpilot.auth import Role
from incidentpilot.observability import JsonFormatter, bind_context, log_event
from incidentpilot.simulator import get_incident
from incidentpilot.workflow import build_graph
from test_agent import ScriptedModel
from test_runbooks import TinyEncoder
from incidentpilot.runbooks import RunbookIndex


def runbooks(tmp_path):
    index = RunbookIndex(tmp_path / "chroma", TinyEncoder())
    index.index("db-pool.md", "Database pool", "Check active connections and pool limit.")
    return index


def test_request_correlation_id_is_echoed_and_counted(auth_config, auth_headers, tmp_path):
    client = TestClient(create_app(database_path=tmp_path / "state.db", auth_config=auth_config))

    response = client.get("/health/live", headers={"X-Correlation-ID": "trace-123"})
    operator = auth_headers(client, Role.OPERATOR)
    metrics = client.get("/ops/metrics", headers=operator).json()

    assert response.status_code == 200
    assert response.headers["X-Correlation-ID"] == "trace-123"
    assert metrics["http_requests_total"] >= 2


def test_json_formatter_includes_trace_context_without_secret_content():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("incidentpilot.test.observability")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    with bind_context(correlation_id="corr-1", investigation_id="run-1"):
        log_event(logger, "safe_event", tool="get_metrics", authorization="Bearer must-not-log")

    payload = json.loads(stream.getvalue())
    assert payload["event"] == "safe_event"
    assert payload["correlation_id"] == "corr-1"
    assert payload["investigation_id"] == "run-1"
    assert payload["tool"] == "get_metrics"
    assert "authorization" not in payload
    assert "must-not-log" not in stream.getvalue()


def test_liveness_is_independent_and_readiness_checks_auth_and_sqlite(auth_config, tmp_path):
    unconfigured = TestClient(create_app(database_path=tmp_path / "missing-auth.db"))
    configured = TestClient(create_app(database_path=tmp_path / "ready.db", auth_config=auth_config))

    assert unconfigured.get("/health/live").status_code == 200
    assert unconfigured.get("/health/ready").status_code == 503
    ready = configured.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json()["dependencies"] == {"sqlite": "ok", "authentication": "ok"}


def test_model_tool_and_retrieval_durations_are_recorded(tmp_path):
    model = ScriptedModel([
        AIMessage(content="", tool_calls=[{
            "name": "get_metrics", "args": {"incident_id": "db-pool-001"}, "id": "m"
        }]),
        AIMessage(content="Metrics collected."),
    ])
    direct = investigate(get_incident("db-pool-001"), model)

    assert direct.model_duration_ms >= 0
    assert direct.tool_results[0].duration_ms >= 0

    workflow_model = ScriptedModel([AIMessage(content="Evidence remains partial.")])
    with SqliteSaver.from_conn_string(str(tmp_path / "state.db")) as saver:
        state = build_graph(workflow_model, saver, runbook_index=runbooks(tmp_path)).invoke(
            {"incident_id": "db-pool-001", "status": "pending"},
            {"configurable": {"thread_id": "timed-run"}},
        )
    assert state["retrieval_duration_ms"] >= 0
    assert state["investigation"]["model_duration_ms"] >= 0


def test_workflow_and_approval_events_are_logged(caplog, auth_config, auth_headers, tmp_path):
    model = ScriptedModel([
        AIMessage(content="", tool_calls=[
            {"name": "get_metrics", "args": {"incident_id": "db-pool-001"}, "id": "m"},
            {"name": "get_logs", "args": {"incident_id": "db-pool-001"}, "id": "l"},
        ]),
        AIMessage(content="Pool is saturated."),
    ])
    client = TestClient(create_app(
        model=model,
        database_path=tmp_path / "state.db",
        runbook_index=runbooks(tmp_path),
        auth_config=auth_config,
    ))
    operator = auth_headers(client, Role.OPERATOR)
    approver = auth_headers(client, Role.APPROVER)

    with caplog.at_level(logging.INFO, logger="incidentpilot"):
        analyzed = client.post("/incidents/db-pool-001/analyze", headers=operator).json()
        proposal_id = analyzed["proposal"]["id"]
        client.post(f"/proposals/{proposal_id}/approve", headers=approver)
        client.post(f"/proposals/{proposal_id}/execute", headers=operator)

    events = {getattr(record, "event", None) for record in caplog.records}
    assert {"workflow_transition", "proposal_approved", "simulated_action_executed"} <= events
