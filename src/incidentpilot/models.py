from typing import Any, Literal

from pydantic import BaseModel, Field


class Incident(BaseModel):
    id: str
    title: str
    service: str
    symptom: str
    simulated: bool = True
    metrics: dict[str, Any] = Field(default_factory=dict, exclude=True)
    logs: list[str] = Field(default_factory=list, exclude=True)
    deployments: list[dict[str, str]] = Field(default_factory=list, exclude=True)


class ToolResult(BaseModel):
    name: str
    call_id: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    error_code: str | None = None
    duration_ms: float | None = None


class Investigation(BaseModel):
    incident_id: str
    answer: str
    tool_results: list[ToolResult]
    status: Literal["completed", "blocked"] = "completed"
    error_category: str | None = None
    model_invocations: int = 0
    model_duration_ms: float = 0
    tool_call_count: int = 0
    simulated: bool = True
