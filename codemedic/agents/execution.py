"""Serializable execution metadata for real Investigator/Fixer runs."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, Field


class AgentExecutionResult(BaseModel):
    """Parsed agent output plus the execution data needed for evaluation."""

    parsed_result: dict[str, Any]
    raw_text: str | None = None
    messages: list[dict[str, Any]] = Field(default_factory=list)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    tool_results: list[dict[str, Any]] = Field(default_factory=list)
    model: str
    provider: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    estimated_cost: float | None = None
    latency_ms: float = Field(ge=0)
    error: str | None = None


def _get(value: object, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _json_safe(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _content_text(content: Any) -> str | None:
    if isinstance(content, str):
        return content
    if content is None:
        return None
    try:
        return json.dumps(_json_safe(content), ensure_ascii=False)
    except (TypeError, ValueError):
        return str(content)


def extract_agent_execution(
    result: Mapping[str, Any],
    *,
    parsed_result: BaseModel | Mapping[str, Any],
    model: str,
    provider: str | None,
    latency_ms: float,
    error: str | None = None,
) -> AgentExecutionResult:
    """Extract stable metadata from a LangChain ``create_agent`` result."""
    messages = list(result.get("messages", []))
    serialized_messages: list[dict[str, Any]] = []
    tool_calls: list[dict[str, Any]] = []
    tool_results: list[dict[str, Any]] = []
    raw_text: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None

    for message in messages:
        message_type = _get(message, "type", _get(message, "role", "unknown"))
        content = _get(message, "content")
        message_data: dict[str, Any] = {
            "type": str(message_type),
            "content": _json_safe(content),
        }
        name = _get(message, "name")
        if name is not None:
            message_data["name"] = str(name)

        current_tool_calls = _get(message, "tool_calls", []) or []
        if current_tool_calls:
            safe_calls = [_json_safe(call) for call in current_tool_calls]
            message_data["tool_calls"] = safe_calls
            tool_calls.extend(
                call for call in safe_calls if isinstance(call, dict)
            )

        if str(message_type) in {"tool", "tool_result"}:
            tool_result: dict[str, Any] = {
                "tool_call_id": _get(message, "tool_call_id"),
                "name": name,
                "content": _json_safe(content),
            }
            tool_results.append(tool_result)

        usage = _get(message, "usage_metadata", {}) or {}
        if isinstance(usage, Mapping):
            prompt_tokens = usage.get("input_tokens", usage.get("prompt_tokens", prompt_tokens))
            completion_tokens = usage.get(
                "output_tokens", usage.get("completion_tokens", completion_tokens)
            )
            total_tokens = usage.get("total_tokens", total_tokens)

        if str(message_type) == "ai" and content:
            raw_text = _content_text(content)
        serialized_messages.append(message_data)

    if isinstance(parsed_result, BaseModel):
        parsed = parsed_result.model_dump(mode="json")
    else:
        parsed = dict(parsed_result)

    return AgentExecutionResult(
        parsed_result=parsed,
        raw_text=raw_text,
        messages=serialized_messages,
        tool_calls=tool_calls,
        tool_results=tool_results,
        model=model,
        provider=provider,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        latency_ms=latency_ms,
        error=error,
    )
