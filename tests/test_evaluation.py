import json

from langchain_core.messages import AIMessage
from test_agent import ScriptedModel

from incidentpilot.evaluation import (
    EvaluationDataset,
    evaluate_dataset,
    load_dataset,
    run_live_local_evaluation,
    run_offline_evaluation,
    write_results,
)


def test_versioned_dataset_covers_required_failure_classes():
    dataset = load_dataset("evals/datasets/v1.json")

    assert dataset.version == "1.0.0"
    assert {case.category for case in dataset.cases} >= {
        "database_pool",
        "faulty_deployment",
        "insufficient_evidence",
        "missing_metrics",
        "contradictory_logs",
        "misleading_runbook",
        "irrelevant_retrieval",
        "tool_protocol_failure",
        "model_connection_failure",
        "incorrect_incident_id",
        "unauthorized_operation",
        "prompt_injection",
        "duplicate_tool_call",
        "incorrect_recommendation",
    }


def test_fixture_metrics_use_explicit_denominators_and_surface_failures():
    result = evaluate_dataset(load_dataset("evals/datasets/v1.json"))

    assert result.execution_mode == "synthetic_fixture"
    assert result.metrics["tool_selection_accuracy"].denominator == 14
    assert result.metrics["argument_validity"].denominator > 0
    assert result.metrics["unsafe_action_prevention"].denominator > 0
    assert result.metrics["cross_incident_isolation"].denominator == 1
    assert (
        result.metrics["recommendation_correctness"].numerator
        < result.metrics["recommendation_correctness"].denominator
    )
    assert result.workflow_counts["blocked"] > 0
    assert "Synthetic fixture scores do not measure live-model reliability." in result.limitations
    assert any(outcome.failure_category == "incorrect_recommendation" for outcome in result.cases)


def test_offline_evaluation_executes_real_workflow_and_tool_boundary():
    result = run_offline_evaluation(load_dataset("evals/datasets/v1.json"))

    assert result.execution_mode == "offline_deterministic"
    assert len(result.cases) == 14
    assert result.metrics["argument_validity"].denominator > 0
    assert result.metrics["cross_incident_isolation"].value == 1.0
    assert result.metrics["unsafe_action_prevention"].value == 1.0
    assert result.failure_categories["model_unavailable"] == 1
    protocol = next(case for case in result.cases if case.case_id == "duplicate-tool-call")
    assert protocol.workflow_status == "blocked"


def test_undefined_metrics_are_null_with_zero_denominator():
    dataset = EvaluationDataset.model_validate(
        {
            "version": "test",
            "model_name": "none",
            "configuration": {},
            "cases": [
                {
                    "id": "no-tools",
                    "category": "insufficient_evidence",
                    "expected_tools": [],
                    "selected_tools": [],
                    "valid_tool_calls": 0,
                    "total_tool_calls": 0,
                    "expected_evidence_status": "insufficient",
                    "observed_evidence_status": "insufficient",
                    "expected_action": None,
                    "observed_action": None,
                    "workflow_status": "insufficient_evidence",
                    "latency_ms": {"total": 1.0},
                }
            ],
        }
    )

    result = evaluate_dataset(dataset)

    assert result.metrics["argument_validity"].value is None
    assert result.metrics["argument_validity"].denominator == 0
    assert result.metrics["cross_incident_isolation"].value is None


def test_results_are_machine_readable_and_include_definitions(tmp_path):
    result = evaluate_dataset(load_dataset("evals/datasets/v1.json"))
    output = tmp_path / "evaluation.json"

    write_results(result, output)
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert payload["dataset_version"] == "1.0.0"
    assert payload["execution_mode"] == "synthetic_fixture"
    assert payload["metric_definitions"]["tool_efficiency"]
    assert payload["cases"][0]["case_id"]
    assert payload["latency_ms"]["total"]["count"] == 14


def test_live_local_mode_is_distinct_from_synthetic_fixture_scoring():
    model = ScriptedModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "get_metrics", "args": {"incident_id": "db-pool-001"}, "id": "db-m"},
                    {"name": "get_logs", "args": {"incident_id": "db-pool-001"}, "id": "db-l"},
                ],
            ),
            AIMessage(content="Database evidence collected."),
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "get_metrics", "args": {"incident_id": "deploy-001"}, "id": "dep-m"},
                    {"name": "get_logs", "args": {"incident_id": "deploy-001"}, "id": "dep-l"},
                    {
                        "name": "get_recent_deployments",
                        "args": {"incident_id": "deploy-001"},
                        "id": "dep-d",
                    },
                ],
            ),
            AIMessage(content="Deployment evidence collected."),
            AIMessage(content="No evidence available."),
        ]
    )

    result = run_live_local_evaluation(model=model, model_name="scripted-live-test")

    assert result.execution_mode == "live_local"
    assert result.model_name == "scripted-live-test"
    assert result.dataset_version == "1.0.0-live"
    assert len(result.cases) == 3
    assert "Live local results are specific to the configured model and host." in result.limitations
