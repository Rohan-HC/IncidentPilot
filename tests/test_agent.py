import json

from langchain_core.messages import AIMessage, ToolMessage

from incidentpilot.agent import investigate, make_model
from incidentpilot.simulator import get_incident


class ScriptedModel:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.seen = []
        self.bound_tools = None

    def bind_tools(self, tools):
        self.bound_tools = tools
        return self

    def invoke(self, messages):
        self.seen.append(list(messages))
        return next(self.responses)


def test_default_model_disables_lengthy_thinking_mode():
    assert make_model().model == "qwen3:4b-instruct"
    assert make_model().reasoning is False
    assert make_model().num_predict == 256


def test_model_requests_tools_and_receives_actual_results():
    model = ScriptedModel([
        AIMessage(content="", tool_calls=[
            {"name": "get_metrics", "args": {"incident_id": "db-pool-001"}, "id": "call-1"},
            {"name": "get_logs", "args": {"incident_id": "db-pool-001"}, "id": "call-2"},
        ]),
        AIMessage(content="Pool is saturated; inspect client leaks before action."),
    ])

    result = investigate(get_incident("db-pool-001"), model)

    assert result.answer.startswith("Pool is saturated")
    assert {item.name for item in result.tool_results} == {"get_metrics", "get_logs"}
    assert {tool.__name__ for tool in model.bound_tools} == {
        "GetMetrics", "GetLogs", "GetRecentDeployments", "SearchRunbooks"
    }
    tool_messages = [message for message in model.seen[1] if isinstance(message, ToolMessage)]
    assert len(tool_messages) == 2
    assert json.loads(tool_messages[0].content)["active_connections"] == 100


def test_model_cannot_switch_to_another_incident():
    model = ScriptedModel([
        AIMessage(content="", tool_calls=[
            {"name": "get_metrics", "args": {"incident_id": "deploy-001"}, "id": "call-1"}
        ]),
        AIMessage(content="I cannot inspect another incident."),
    ])

    result = investigate(get_incident("db-pool-001"), model)

    assert result.tool_results[0].error == "incident_id does not match this investigation"
    assert "active_connections" not in model.seen[1][-1].content
