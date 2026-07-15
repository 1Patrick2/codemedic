"""Typed events emitted by a CodeMedic workflow run."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

EventType = Literal[
    "node_started",
    "node_completed",
    "model_request",
    "model_response",
    "tool_call",
    "tool_result",
    "validation",
    "interrupt",
    "resume",
    "patch_apply",
    "test_result",
    "workflow_completed",
    "workflow_failed",
]

EVENT_TYPES: tuple[EventType, ...] = (
    "node_started",
    "node_completed",
    "model_request",
    "model_response",
    "tool_call",
    "tool_result",
    "validation",
    "interrupt",
    "resume",
    "patch_apply",
    "test_result",
    "workflow_completed",
    "workflow_failed",
)


class WorkflowEvent(BaseModel):
    """One ordered, JSON-serializable event in a workflow trajectory."""

    event_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    timestamp: datetime
    node: str = Field(min_length=1)
    event_type: EventType
    summary: str
    input_data: dict
    output_data: dict
    duration_ms: float | None = Field(default=None, ge=0)
