"""Persistent trajectory recording for one workflow run."""

from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from pydantic import BaseModel

from codemedic.tracing.events import EventType, WorkflowEvent
from codemedic.tracing.serializer import event_from_json, event_to_json, json_text

_SENSITIVE_KEY = re.compile(
    r"(?:api[_-]?key|access[_-]?token|auth(?:orization)?|credential|password|secret|token)",
    re.IGNORECASE,
)
_SENSITIVE_VALUE = re.compile(
    r"(?i)(\bBearer\s+)[^\s,;]+|\bsk-[A-Za-z0-9_-]+"
)
_REDACTED = "[REDACTED]"

_registry_lock = threading.RLock()
_registry: dict[tuple[str, str], "TrajectoryRecorder"] = {}


def sanitize_data(value: Any, *, key: str | None = None) -> Any:
    """Convert values to JSON-safe data and redact common credential forms."""
    if key and _SENSITIVE_KEY.search(key):
        return _REDACTED
    if isinstance(value, BaseModel):
        return sanitize_data(value.model_dump(mode="json"), key=key)
    if isinstance(value, Mapping):
        return {str(k): sanitize_data(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [sanitize_data(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        return _SENSITIVE_VALUE.sub(
            lambda match: (
                f"{match.group(1)}{_REDACTED}" if match.group(1) else _REDACTED
            ),
            value,
        )
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


def _safe_component(value: str, label: str) -> str:
    """Accept one filesystem component and reject traversal or nesting."""
    path = Path(value)
    if not value or path.name != value or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Invalid {label}: {value!r}")
    return value


class TrajectoryRecorder:
    """Append ordered events and artifacts under ``runtime/runs/<run_id>``."""

    def __init__(
        self,
        run_id: str,
        thread_id: str,
        *,
        root_dir: str | Path | None = None,
    ) -> None:
        self.run_id = _safe_component(run_id, "run_id")
        self.thread_id = _safe_component(thread_id, "thread_id")
        self.root_dir = Path(root_dir) if root_dir else Path.cwd() / "runtime" / "runs"
        self.run_dir = self.root_dir / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._events_path = self.run_dir / "events.jsonl"
        self._file = open(self._events_path, "a", encoding="utf-8")  # noqa: SIM115
        self._lock = threading.RLock()
        self._closed = False
        self._sequence = self._read_last_sequence()
        self._metadata = self._load_metadata()
        self._metadata.update(
            {
                "run_id": self.run_id,
                "thread_id": self.thread_id,
                "status": "running",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        self._write_metadata()

    @property
    def path(self) -> Path:
        """Directory containing this run's trajectory files."""
        return self.run_dir

    def _read_last_sequence(self) -> int:
        if not self._events_path.exists():
            return 0
        last = 0
        for line in self._events_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                last = event_from_json(line).sequence
        return last

    def _load_metadata(self) -> dict[str, Any]:
        metadata_path = self.run_dir / "run.json"
        if not metadata_path.exists():
            return {"started_at": datetime.now(timezone.utc).isoformat()}
        raw = json.loads(metadata_path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}

    def _write_metadata(self) -> None:
        metadata_path = self.run_dir / "run.json"
        metadata_path.write_text(json_text(sanitize_data(self._metadata)), encoding="utf-8")

    def record(
        self,
        *,
        node: str,
        event_type: EventType,
        summary: str,
        input_data: Mapping[str, Any] | None = None,
        output_data: Mapping[str, Any] | None = None,
        duration_ms: float | None = None,
    ) -> WorkflowEvent:
        """Validate, sanitize, append, and return one workflow event."""
        with self._lock:
            if self._closed:
                raise RuntimeError("TrajectoryRecorder is closed")
            self._sequence += 1
            event = WorkflowEvent(
                event_id=str(uuid.uuid4()),
                run_id=self.run_id,
                thread_id=self.thread_id,
                sequence=self._sequence,
                timestamp=datetime.now(timezone.utc),
                node=node,
                event_type=event_type,
                summary=summary,
                input_data=sanitize_data(dict(input_data or {})),
                output_data=sanitize_data(dict(output_data or {})),
                duration_ms=duration_ms,
            )
            self._file.write(event_to_json(event) + "\n")
            self._file.flush()
            self._metadata["updated_at"] = event.timestamp.isoformat()
            self._write_metadata()
            return event

    def write_artifact(self, name: str, content: str) -> Path:
        """Write a text artifact at the run root using a safe filename."""
        artifact = self.run_dir / _safe_component(name, "artifact name")
        artifact.write_text(content, encoding="utf-8")
        return artifact

    def write_json_artifact(self, name: str, value: Any) -> Path:
        """Write a sanitized JSON artifact at the run root."""
        return self.write_artifact(name, json_text(sanitize_data(value)))

    def finish(self, status: str) -> None:
        """Persist the terminal or waiting status for this run."""
        with self._lock:
            self._metadata.update(
                {
                    "status": status,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            self._write_metadata()

    def close(self) -> None:
        """Flush and close the JSONL file; safe to call repeatedly."""
        with self._lock:
            if not self._closed:
                self._file.close()
                self._closed = True

    def __enter__(self) -> "TrajectoryRecorder":
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()


def register_recorder(recorder: TrajectoryRecorder) -> None:
    """Make a recorder available to instrumented graph nodes."""
    with _registry_lock:
        _registry[(recorder.run_id, recorder.thread_id)] = recorder


def unregister_recorder(run_id: str, thread_id: str) -> None:
    """Remove a recorder from the active node registry."""
    with _registry_lock:
        _registry.pop((run_id, thread_id), None)


def get_recorder(run_id: str | None, thread_id: str | None) -> TrajectoryRecorder | None:
    """Return the active recorder for a serializable state identity."""
    if not run_id or not thread_id:
        return None
    with _registry_lock:
        return _registry.get((run_id, thread_id))
