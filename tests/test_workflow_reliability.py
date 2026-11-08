import httpx
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite import SqliteSaver

from incidentpilot.api import create_app
from incidentpilot.workflow import RetryPolicy, build_graph
from test_agent import ScriptedModel
from test_runbooks import TinyEncoder
from incidentpilot.runbooks import RunbookIndex


def runbooks(tmp_path):
    index = RunbookIndex(tmp_path / "chroma", TinyEncoder())
    index.index("db-pool.md", "Database pool", "Check active connections and pool limit.")
    return index


def invoke(tmp_path, model, *, index=None, run_id="reliable-run", retry_policy=None):
    with SqliteSaver.from_conn_string(str(tmp_path / "state.db")) as saver:
        return build_graph(
            model,
            saver,
            runbook_index=index or runbooks(tmp_path),
            retry_policy=retry_policy or RetryPolicy(max_attempts=2, backoff_seconds=0),
        ).invoke(
            {"incident_id": "db-pool-001", "status": "pending"},
            {"configurable": {"thread_id": run_id}},
        )


def test_transient_model_failure_recovers_with_bounded_retry(tmp_path):
    class RaisingModel(ScriptedModel):
        def invoke(self, messages):
            self.seen.append(list(messages))
            response = next(self.responses)
            if isinstance(response, Exception):
                raise response
            return response

    model = RaisingModel([
        httpx.ConnectError("temporary Ollama outage"),
        AIMessage(content="No corroborating observations."),
    ])

    state = invoke(tmp_path, model)

    assert state["status"] == "insufficient_evidence"
    assert state["retry_count"] == 1
    assert state["failure_category"] is None
    assert state["transitions"] == ["pending->running", "running->insufficient_evidence"]


def test_malformed_model_response_is_permanent_and_not_retried(tmp_path):
    model = ScriptedModel([None, AIMessage(content="must not be consumed")])

    state = invoke(tmp_path, model)

    assert state["status"] == "blocked"
    assert state["failure_category"] == "model_response"
    assert state["retry_count"] == 0
    assert len(model.seen) == 1
    assert state["proposed_action"] is None


def test_retrieval_failure_retries_then_blocks(tmp_path):
    class BrokenIndex:
        calls = 0

        def search(self, query):
            self.calls += 1
            raise OSError("vector index unavailable")

    index = BrokenIndex()
    state = invoke(tmp_path, ScriptedModel([AIMessage(content="No context.")]), index=index)

    assert index.calls == 2
    assert state["status"] == "blocked"
    assert state["failure_category"] == "retrieval_unavailable"
    assert state["retry_count"] == 1
    assert state["proposed_action"] is None


def test_permanent_model_failure_is_failed_without_retry(tmp_path):
    class BrokenModel(ScriptedModel):
        def invoke(self, messages):
            self.seen.append(list(messages))
            raise ValueError("unsupported response contract")

    model = BrokenModel([])

    state = invoke(tmp_path, model)

    assert state["status"] == "failed"
    assert state["failure_category"] == "model_failure"
    assert state["retry_count"] == 0
    assert state["proposed_action"] is None


def test_completed_checkpoint_restores_validated_terminal_state(tmp_path):
    db = tmp_path / "state.db"
    model = ScriptedModel([AIMessage(content="Evidence remains partial.")])
    with SqliteSaver.from_conn_string(str(db)) as saver:
        graph = build_graph(model, saver, runbook_index=runbooks(tmp_path))
        first = graph.invoke(
            {"incident_id": "db-pool-001", "status": "pending"},
            {"configurable": {"thread_id": "restart-run"}},
        )
    with SqliteSaver.from_conn_string(str(db)) as saver:
        restored = build_graph(model, saver, runbook_index=runbooks(tmp_path)).get_state(
            {"configurable": {"thread_id": "restart-run"}}
        ).values

    assert first["status"] == "insufficient_evidence"
    assert restored["status"] == "insufficient_evidence"
    assert restored["transitions"][-1] == "running->insufficient_evidence"


def test_api_idempotency_key_replays_without_duplicate_proposal(tmp_path, auth_config, auth_headers):
    model = ScriptedModel([
        AIMessage(content="", tool_calls=[
            {"name": "get_metrics", "args": {"incident_id": "db-pool-001"}, "id": "m"},
            {"name": "get_logs", "args": {"incident_id": "db-pool-001"}, "id": "l"},
        ]),
        AIMessage(content="Pool saturation is corroborated."),
    ])
    client = TestClient(create_app(
        model=model,
        database_path=tmp_path / "state.db",
        runbook_index=runbooks(tmp_path),
        auth_config=auth_config,
    ))
    headers = {**auth_headers(client), "Idempotency-Key": "db-pool-demo"}

    first = client.post("/incidents/db-pool-001/analyze", headers=headers)
    second = client.post("/incidents/db-pool-001/analyze", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["run_id"] == second.json()["run_id"]
    assert first.json()["proposal"]["id"] == second.json()["proposal"]["id"]
    assert len(model.seen) == 2
