"""Local JSONL tracing for agent execution.

Records each agent step as a JSONL line for debugging and evaluation.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any


def _default_trace_dir() -> Path:
    """Default to <project_root>/runtime/traces."""
    return Path.cwd() / "runtime" / "traces"


class LocalTracer:
    """Lightweight JSONL tracer for a single agent run.

    Usage:
        tracer = LocalTracer()
        tracer.record_step(node_name="investigator", ...)
        tracer.close()
    """

    def __init__(self, task_id: str | None = None, trace_dir: str | Path | None = None) -> None:
        self.task_id = task_id or f"task_{uuid.uuid4().hex[:12]}"
        self.trace_dir = Path(trace_dir) if trace_dir else _default_trace_dir()
        self.trace_dir.mkdir(parents=True, exist_ok=True)

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        self._path = self.trace_dir / f"{timestamp}_{self.task_id}.jsonl"
        self._file = open(self._path, "a", encoding="utf-8")  # noqa: SIM115
        self._closed = False

    @property
    def path(self) -> Path:
        """Path to the trace file on disk."""
        return self._path

    def record_step(
        self,
        *,
        node_name: str,
        agent_name: str,
        model: str | None = None,
        prompt_version: str | None = None,
        tool_name: str | None = None,
        tool_args_summary: str | None = None,
        tool_result_summary: str | None = None,
        latency_ms: int | None = None,
        token_usage: dict[str, Any] | None = None,
        state_transition: str | None = None,
        error: str | None = None,
        retry_count: int = 0,
        final_status: str | None = None,
    ) -> None:
        """Record one execution step to the JSONL trace."""
        if self._closed:
            return

        entry: dict[str, Any] = {
            "task_id": self.task_id,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "node_name": node_name,
            "agent_name": agent_name,
            "model": model,
            "prompt_version": prompt_version,
            "tool_name": tool_name,
            "tool_args_summary": tool_args_summary,
            "tool_result_summary": tool_result_summary,
            "latency_ms": latency_ms,
            "token_usage": token_usage,
            "state_transition": state_transition,
            "error": error,
            "retry_count": retry_count,
            "final_status": final_status,
        }
        # Remove None values to keep traces compact
        entry = {k: v for k, v in entry.items() if v is not None}

        self._file.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._file.flush()

    def close(self) -> None:
        """Close the trace file."""
        if not self._closed:
            self._file.close()
            self._closed = True
