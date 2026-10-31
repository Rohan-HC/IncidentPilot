import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from incidentpilot.models import Incident, Investigation, ToolResult
from incidentpilot.tools import MODEL_TOOLS, dispatch_tool


_TOOL_NAMES = {
    "GetMetrics": "get_metrics",
    "GetLogs": "get_logs",
    "GetRecentDeployments": "get_recent_deployments",
    "SearchRunbooks": "search_runbooks",
    "get_metrics": "get_metrics",
    "get_logs": "get_logs",
    "get_recent_deployments": "get_recent_deployments",
    "search_runbooks": "search_runbooks",
}


def make_model() -> Any:
    from langchain_ollama import ChatOllama

    return ChatOllama(model="qwen3:4b-instruct", temperature=0, reasoning=False, num_predict=512)


def investigate(incident: Incident, model: Any | None = None, runbook_context: list[dict[str, Any]] | None = None) -> Investigation:
    """Let the model select observations; execute them through an allowlisted dispatcher."""
    bound = (model if model is not None else make_model()).bind_tools(MODEL_TOOLS)
    messages = [
        SystemMessage(content=(
            "You are investigating a SIMULATED incident. Use read-only tools to obtain evidence. "
            "Never claim production access. Do not issue corrective commands. "
            "If evidence is thin, state that it is insufficient."
        )),
        HumanMessage(content=(
            f"Investigate incident {incident.id}: {incident.title}. Symptom: {incident.symptom}. "
            + ("Retrieved runbooks (synthetic guidance, cite source IDs): " + json.dumps(runbook_context)
               if runbook_context else "No relevant runbook was retrieved.")
        )),
    ]
    results: list[ToolResult] = []
    for _ in range(4):
        reply = bound.invoke(messages)
        messages.append(reply)
        if not reply.tool_calls:
            return Investigation(incident_id=incident.id, answer=str(reply.content), tool_results=results)
        for call in reply.tool_calls:
            name = _TOOL_NAMES.get(call["name"])
            args = call["args"]
            if name is None:
                result = ToolResult(name=call["name"], error="Unknown tool")
            elif name != "search_runbooks" and args.get("incident_id") != incident.id:
                result = ToolResult(name=name, error="incident_id does not match this investigation")
            else:
                try:
                    result = ToolResult(name=name, result=dispatch_tool(name, args))
                except (KeyError, ValueError) as exc:
                    result = ToolResult(name=name, error=str(exc))
            results.append(result)
            messages.append(ToolMessage(
                content=json.dumps(result.result if result.error is None else {"error": result.error}),
                tool_call_id=call["id"],
            ))
    return Investigation(
        incident_id=incident.id,
        answer="Investigation stopped after the tool-call limit; evidence needs human review.",
        tool_results=results,
    )
