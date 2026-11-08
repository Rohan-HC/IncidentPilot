from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from test_agent import ScriptedModel
from test_runbooks import TinyEncoder

from incidentpilot.runbooks import RunbookIndex
from incidentpilot.workflow import build_graph


def runbooks(tmp_path):
    index = RunbookIndex(tmp_path / "chroma", TinyEncoder())
    index.index("db-pool.md", "Database pool", "Check active connections and pool limit.")
    index.index("faulty-deploy.md", "Faulty deployment", "Compare errors and rollback version.")
    return index


def test_graph_proposes_only_from_correlated_evidence(tmp_path):
    model = ScriptedModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "get_metrics", "args": {"incident_id": "db-pool-001"}, "id": "m"},
                    {"name": "get_logs", "args": {"incident_id": "db-pool-001"}, "id": "l"},
                ],
            ),
            AIMessage(content="Pool is saturated."),
        ]
    )
    with SqliteSaver.from_conn_string(str(tmp_path / "state.db")) as saver:
        graph = build_graph(model, saver, runbook_index=runbooks(tmp_path))
        state = graph.invoke(
            {"incident_id": "db-pool-001"}, {"configurable": {"thread_id": "run-1"}}
        )

    assert state["evidence_status"] == "sufficient"
    assert state["proposed_action"] == "increase_pool_limit"
    assert state["investigation"]["tool_results"][0]["result"]["active_connections"] == 100


def test_graph_records_insufficient_evidence_and_restores_checkpoint(tmp_path):
    db = tmp_path / "state.db"
    model = ScriptedModel([AIMessage(content="No corroborating observations.")])
    with SqliteSaver.from_conn_string(str(db)) as saver:
        state = build_graph(model, saver, runbook_index=runbooks(tmp_path)).invoke(
            {"incident_id": "unknown-001"}, {"configurable": {"thread_id": "run-unknown"}}
        )
    with SqliteSaver.from_conn_string(str(db)) as saver:
        restored = build_graph(model, saver, runbook_index=runbooks(tmp_path)).get_state(
            {"configurable": {"thread_id": "run-unknown"}}
        )

    assert state["evidence_status"] == "insufficient"
    assert state["proposed_action"] is None
    assert restored.values["incident_id"] == "unknown-001"


def test_faulty_deployment_requires_metrics_logs_and_deployment(tmp_path):
    model = ScriptedModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "get_metrics", "args": {"incident_id": "deploy-001"}, "id": "m"},
                    {"name": "get_logs", "args": {"incident_id": "deploy-001"}, "id": "l"},
                    {
                        "name": "get_recent_deployments",
                        "args": {"incident_id": "deploy-001"},
                        "id": "d",
                    },
                ],
            ),
            AIMessage(content="Version v2.4.1 correlates with errors."),
        ]
    )
    with SqliteSaver.from_conn_string(str(tmp_path / "state.db")) as saver:
        state = build_graph(model, saver, runbook_index=runbooks(tmp_path)).invoke(
            {"incident_id": "deploy-001"}, {"configurable": {"thread_id": "run-deploy"}}
        )

    assert state["evidence_status"] == "sufficient"
    assert state["proposed_action"] == "rollback_deployment"


def test_missing_runbook_blocks_proposal_despite_metrics_and_logs(tmp_path):
    model = ScriptedModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "get_metrics", "args": {"incident_id": "db-pool-001"}, "id": "m"},
                    {"name": "get_logs", "args": {"incident_id": "db-pool-001"}, "id": "l"},
                ],
            ),
            AIMessage(content="Pool is saturated."),
        ]
    )
    empty_index = RunbookIndex(tmp_path / "empty-chroma", TinyEncoder())
    with SqliteSaver.from_conn_string(str(tmp_path / "state.db")) as saver:
        state = build_graph(model, saver, runbook_index=empty_index).invoke(
            {"incident_id": "db-pool-001"}, {"configurable": {"thread_id": "run-no-runbook"}}
        )

    assert state["evidence_status"] == "insufficient"
    assert state["proposed_action"] is None


def test_retrieval_failure_is_recorded_and_cannot_propose(tmp_path):
    class BrokenIndex:
        def search(self, query):
            raise OSError("local vector index unavailable")

    model = ScriptedModel([AIMessage(content="No runbook context available.")])
    with SqliteSaver.from_conn_string(str(tmp_path / "state.db")) as saver:
        state = build_graph(model, saver, runbook_index=BrokenIndex()).invoke(
            {"incident_id": "db-pool-001"}, {"configurable": {"thread_id": "run-retrieval-down"}}
        )

    assert state["retrieval_error"] == "local vector index unavailable"
    assert state["evidence_status"] == "insufficient"
    assert state["proposed_action"] is None
