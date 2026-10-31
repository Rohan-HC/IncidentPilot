from typing import Any

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
    result: dict[str, Any] | None = None
    error: str | None = None


class Investigation(BaseModel):
    incident_id: str
    answer: str
    tool_results: list[ToolResult]
    simulated: bool = True
