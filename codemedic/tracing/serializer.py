"""Serialization helpers for trajectory events and JSON artifacts."""

from __future__ import annotations

import json
from typing import Any

from codemedic.tracing.events import WorkflowEvent


def event_to_json(event: WorkflowEvent) -> str:
    """Serialize an event as one JSON object without a trailing newline."""
    return event.model_dump_json()


def event_from_json(payload: str | bytes | dict[str, Any]) -> WorkflowEvent:
    """Deserialize one JSON event object and revalidate its schema."""
    if isinstance(payload, dict):
        return WorkflowEvent.model_validate(payload)
    return WorkflowEvent.model_validate_json(payload)


def json_text(value: Any) -> str:
    """Serialize an already-sanitized value as readable UTF-8 JSON."""
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"
