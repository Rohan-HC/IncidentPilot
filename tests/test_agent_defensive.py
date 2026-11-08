import time

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from incidentpilot.agent import AgentLimits, investigate
from incidentpilot.simulator import get_incident


class RawReply:
    def __init__(self, *, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class ScriptedModel:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.seen = []

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        self.seen.append(list(messages))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.mark.parametrize(
    ("call", "error_code"),
    [
        ({"name": "delete_database", "args": {"incident_id": "db-pool-001"}, "id": "x"}, "unknown_tool"),
        ({"name": "get_metrics", "args": {}, "id": "x"}, "invalid_arguments"),
        ({"name": "get_metrics", "args": {"incident_id": 7}, "id": "x"}, "invalid_arguments"),
        ({"name": "get_metrics", "args": {"incident_id": "deploy-001"}, "id": "x"}, "incident_mismatch"),
        ({"name": "get_metrics", "args": None, "id": "x"}, "invalid_arguments"),
        ({"name": "get_metrics", "args": {"incident_id": "db-pool-001", "command": "rm -rf /"}, "id": "x"}, "invalid_arguments"),
    ],
)
def test_invalid_tool_calls_block_investigation(call, error_code):
    first = RawReply(tool_calls=[call]) if call.get("args") is None else AIMessage(content="", tool_calls=[call])
    model = ScriptedModel([first, AIMessage(content="Tool request rejected.")])

    result = investigate(get_incident("db-pool-001"), model)

    assert result.status == "blocked"
    assert result.error_category == "tool_protocol"
    assert result.tool_results[0].error_code == error_code


def test_missing_tool_call_id_blocks_without_inventing_protocol_identifier():
    model = ScriptedModel([RawReply(tool_calls=[{
        "name": "get_metrics", "args": {"incident_id": "db-pool-001"}
    }])])

    result = investigate(get_incident("db-pool-001"), model)

    assert result.status == "blocked"
    assert result.error_category == "tool_protocol"
    assert result.tool_results == []
    assert len(model.seen) == 1


def test_duplicate_tool_call_ids_block_before_any_tool_executes():
    calls = [
        {"name": "get_metrics", "args": {"incident_id": "db-pool-001"}, "id": "same"},
        {"name": "get_logs", "args": {"incident_id": "db-pool-001"}, "id": "same"},
    ]
    model = ScriptedModel([RawReply(tool_calls=calls)])

    result = investigate(get_incident("db-pool-001"), model)

    assert result.status == "blocked"
    assert result.tool_results == []


@pytest.mark.parametrize("reply", [None, {"content": "not a message"}, RawReply(tool_calls="bad")])
def test_unexpected_model_response_shapes_block(reply):
    result = investigate(get_incident("db-pool-001"), ScriptedModel([reply]))

    assert result.status == "blocked"
    assert result.error_category == "model_response"


def test_tool_call_id_is_preserved_on_result_and_tool_message():
    model = ScriptedModel([
        AIMessage(content="", tool_calls=[{
            "name": "get_metrics", "args": {"incident_id": "db-pool-001"}, "id": "call-42"
        }]),
        AIMessage(content="Sufficient observations collected."),
    ])

    result = investigate(get_incident("db-pool-001"), model)

    assert result.status == "completed"
    assert result.tool_results[0].call_id == "call-42"
    messages = [item for item in model.seen[1] if isinstance(item, ToolMessage)]
    assert messages[0].tool_call_id == "call-42"


def test_total_tool_call_limit_blocks_before_excess_call():
    calls = [
        {"name": "get_metrics", "args": {"incident_id": "db-pool-001"}, "id": "one"},
        {"name": "get_logs", "args": {"incident_id": "db-pool-001"}, "id": "two"},
    ]
    result = investigate(
        get_incident("db-pool-001"),
        ScriptedModel([AIMessage(content="", tool_calls=calls)]),
        limits=AgentLimits(max_tool_calls=1),
    )

    assert result.status == "blocked"
    assert result.error_category == "limit_exceeded"
    assert result.tool_results == []


def test_tool_argument_size_limit_blocks_oversized_input():
    call = {"name": "search_runbooks", "args": {"query": "x" * 100}, "id": "large"}

    result = investigate(
        get_incident("db-pool-001"),
        ScriptedModel([AIMessage(content="", tool_calls=[call])]),
        limits=AgentLimits(max_argument_bytes=32),
    )

    assert result.status == "blocked"
    assert result.error_category == "limit_exceeded"


def test_model_invocation_timeout_is_structured():
    class SlowModel(ScriptedModel):
        def invoke(self, messages):
            time.sleep(0.05)
            return AIMessage(content="too late")

    result = investigate(
        get_incident("db-pool-001"),
        SlowModel([]),
        limits=AgentLimits(model_timeout_seconds=0.001, investigation_timeout_seconds=1),
    )

    assert result.status == "blocked"
    assert result.error_category == "model_timeout"


def test_round_limit_blocks_incomplete_protocol():
    call = {"name": "get_metrics", "args": {"incident_id": "db-pool-001"}, "id": "one"}
    result = investigate(
        get_incident("db-pool-001"),
        ScriptedModel([AIMessage(content="", tool_calls=[call])]),
        limits=AgentLimits(max_rounds=1),
    )

    assert result.status == "blocked"
    assert result.error_category == "limit_exceeded"

