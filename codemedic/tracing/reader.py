"""Read persisted workflow trajectories without depending on LangGraph."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from codemedic.tracing.recorder import _safe_component
from codemedic.tracing.serializer import event_from_json


class TrajectoryReader:
    """Read run metadata, ordered events, and artifacts from a runs directory."""

    def __init__(self, root_dir: str | Path | None = None) -> None:
        self.root_dir = Path(root_dir) if root_dir else Path.cwd() / "runtime" / "runs"

    def _run_dir(self, run_id: str) -> Path:
        return self.root_dir / _safe_component(run_id, "run_id")

    def read_run(self, run_id: str) -> dict[str, Any]:
        """Read the run.json metadata object."""
        path = self._run_dir(run_id) / "run.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def read_events(self, run_id: str) -> list:
        """Read and validate all events in persisted sequence order."""
        path = self._run_dir(run_id) / "events.jsonl"
        events = [
            event_from_json(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line
        ]
        return sorted(events, key=lambda event: event.sequence)

    def read_artifact(self, run_id: str, name: str) -> str:
        """Read one safe artifact from a run directory."""
        return (self._run_dir(run_id) / _safe_component(name, "artifact name")).read_text(
            encoding="utf-8"
        )

    def list_runs(self) -> list[str]:
        """List run IDs with a persisted run.json file."""
        if not self.root_dir.exists():
            return []
        return sorted(
            path.name
            for path in self.root_dir.iterdir()
            if path.is_dir() and (path / "run.json").is_file()
        )

    def read_trajectory(self, run_id: str) -> dict[str, Any]:
        """Return JSON-safe metadata and ordered events for one run."""
        run_dir = self._run_dir(run_id)
        artifacts = sorted(
            path.name
            for path in run_dir.iterdir()
            if path.is_file() and path.name not in {"run.json", "events.jsonl"}
        )
        return {
            "run": self.read_run(run_id),
            "events": [event.model_dump(mode="json") for event in self.read_events(run_id)],
            "artifacts": artifacts,
        }
