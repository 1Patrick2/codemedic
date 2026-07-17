"""Read-only public APIs for locally persisted Evaluation reports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from codemedic.config import settings
from codemedic.evaluation.schemas import EvaluationRunResult


def _evaluation_root(root_dir: str | Path | None) -> Path:
    root = (
        Path(root_dir)
        if root_dir is not None
        else settings.resolved_runtime_dir / "evaluations"
    )
    return root.resolve()


def _evaluation_directory(evaluation_id: str, root_dir: str | Path | None) -> Path:
    if not evaluation_id or Path(evaluation_id).name != evaluation_id:
        raise ValueError("unsafe evaluation id")

    root = _evaluation_root(root_dir)
    directory = (root / evaluation_id).resolve()
    try:
        directory.relative_to(root)
    except ValueError as exc:
        raise ValueError("evaluation id is outside the evaluation root") from exc
    if not directory.is_dir():
        raise ValueError(f"evaluation not found: {evaluation_id}")
    return directory


def list_evaluations(root_dir: str | Path | None = None) -> list[str]:
    """List persisted evaluation IDs in stable order."""
    root = _evaluation_root(root_dir)
    if not root.is_dir():
        return []
    return sorted(
        directory.name
        for directory in root.iterdir()
        if directory.is_dir() and (directory / "summary.json").is_file()
    )


def get_evaluation_summary(
    evaluation_id: str,
    root_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Read one JSON-safe aggregate evaluation summary."""
    path = _evaluation_directory(evaluation_id, root_dir) / "summary.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid evaluation summary: {evaluation_id}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"evaluation summary must be an object: {evaluation_id}")
    return value


def get_evaluation_runs(
    evaluation_id: str,
    root_dir: str | Path | None = None,
) -> list[EvaluationRunResult]:
    """Read and validate every persisted run in one evaluation."""
    path = _evaluation_directory(evaluation_id, root_dir) / "runs.jsonl"
    if not path.is_file():
        return []

    runs: list[EvaluationRunResult] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"unable to read evaluation runs: {evaluation_id}") from exc

    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            runs.append(EvaluationRunResult.model_validate_json(line))
        except ValueError as exc:
            raise ValueError(
                f"invalid evaluation run at line {line_number}: {evaluation_id}"
            ) from exc
    return runs
