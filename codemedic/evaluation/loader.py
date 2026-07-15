"""Load and validate evaluation task definitions."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from codemedic.evaluation.schemas import RepairTask


def _read_payload(path: Path) -> Mapping[str, Any]:
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
    else:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"task file must contain an object: {path}")
    return payload


def load_task(path: str | Path) -> RepairTask:
    """Read one YAML or JSON task and validate its public contract."""
    task_path = Path(path)
    if task_path.suffix.lower() not in {".yaml", ".yml", ".json"}:
        raise ValueError(f"unsupported task format: {task_path.suffix}")
    return RepairTask.model_validate(_read_payload(task_path))


def load_tasks(directory: str | Path) -> list[RepairTask]:
    """Load all task files in stable order and reject duplicate identifiers."""
    task_dir = Path(directory)
    if not task_dir.is_dir():
        raise ValueError(f"task directory does not exist: {task_dir}")

    paths = sorted(
        path
        for path in task_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".yaml", ".yml", ".json"}
    )
    tasks = [load_task(path) for path in paths]
    seen: set[str] = set()
    for task in tasks:
        if task.task_id in seen:
            raise ValueError(f"duplicate task_id: {task.task_id}")
        seen.add(task.task_id)
    return tasks
