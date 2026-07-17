"""Isolated, dependency-injected batch execution for evaluations."""

from __future__ import annotations

import shutil
import tempfile
import uuid
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from codemedic.evaluation.report import EvaluationReportWriter
from codemedic.evaluation.schemas import EvaluationRunResult, RepairTask

EvaluationExecutor = Callable[
    [RepairTask, str, str],
    EvaluationRunResult | tuple[EvaluationRunResult, dict[str, Any] | None],
]
EvaluationProgressCallback = Callable[
    [str, int, int, RepairTask, str, EvaluationRunResult | None], None
]


class EvaluationBatchRunner:
    """Run repeated tasks in disposable repository copies and persist every run."""

    def __init__(
        self,
        *,
        output_dir: str | Path,
        model: str,
        executor: EvaluationExecutor,
        repeats: int = 1,
        workspace_root: str | Path | None = None,
        commit_sha: str | None = None,
        provider: str | None = None,
        prompt_version: str | None = None,
        progress_callback: EvaluationProgressCallback | None = None,
    ) -> None:
        if not model.strip():
            raise ValueError("model must not be empty")
        if repeats < 1:
            raise ValueError("repeats must be at least one")
        self.output_dir = Path(output_dir)
        self.model = model
        self.executor = executor
        self.repeats = repeats
        self.commit_sha = commit_sha
        self.provider = provider
        self.prompt_version = prompt_version
        self.progress_callback = progress_callback
        self.workspace_root = (
            Path(workspace_root) if workspace_root else self.output_dir / "workspaces"
        )

    def run(self, tasks: Iterable[RepairTask]) -> dict[str, Path]:
        """Execute all tasks, recording an explicit result even on harness errors."""
        self._reset_output()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "workspaces").mkdir(exist_ok=True)
        (self.output_dir / "trajectories").mkdir(exist_ok=True)
        writer = EvaluationReportWriter(self.output_dir)
        task_list = [RepairTask.model_validate(task) for task in tasks]
        total = len(task_list) * self.repeats
        index = 0
        for task in task_list:
            for _ in range(self.repeats):
                index += 1
                run_id = f"{task.task_id}-{uuid.uuid4().hex}"
                if self.progress_callback:
                    self.progress_callback("started", index, total, task, run_id, None)
                result, trajectory = self._run_one(task, run_id)
                writer.write_run(result, trajectory=trajectory)
                if self.progress_callback:
                    self.progress_callback(
                        "completed", index, total, task, run_id, result
                    )
        return writer.finalize()

    def _reset_output(self) -> None:
        """Remove only prior artifacts owned by this evaluation directory."""
        if not self.output_dir.exists():
            return

        for filename in ("runs.jsonl", "summary.json", "report.md"):
            path = self.output_dir / filename
            if path.is_file():
                path.unlink()

        for directory_name in ("trajectories", "workspaces"):
            directory = self.output_dir / directory_name
            if not directory.is_dir():
                continue
            for child in directory.iterdir():
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()

    def _run_one(
        self,
        task: RepairTask,
        run_id: str,
    ) -> tuple[EvaluationRunResult, dict[str, Any] | None]:
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            dir=self.workspace_root,
            prefix="codemedic-eval-",
        ) as temp_dir:
            isolated_path = Path(temp_dir) / task.task_id
            source_path = task.repository_path.resolve()
            if not source_path.is_dir():
                return self._failure(task, run_id, f"repository not found: {source_path}")
            output_path = self.output_dir.resolve()

            def ignore_output(path: str, names: list[str]) -> list[str]:
                ignored: list[str] = []
                current = Path(path)
                for name in names:
                    candidate = (current / name).resolve()
                    if candidate == output_path:
                        ignored.append(name)
                return ignored

            shutil.copytree(source_path, isolated_path, ignore=ignore_output)
            isolated_task = task.model_copy(update={"repository_path": isolated_path})
            try:
                execution = self.executor(isolated_task, run_id, self.model)
                if isinstance(execution, tuple):
                    result, trajectory = execution
                else:
                    result, trajectory = execution, None
                result = EvaluationRunResult.model_validate(result)
                if result.task_id != task.task_id:
                    raise ValueError("executor returned a result for a different task_id")
                if result.run_id != run_id:
                    raise ValueError("executor returned a result for a different run_id")
                if result.model != self.model:
                    raise ValueError("executor returned a result for a different model")
                metadata = {
                    "task_version": task.task_version,
                    "task_kind": task.task_kind,
                }
                for field, value in (
                    ("commit_sha", self.commit_sha),
                    ("provider", self.provider),
                    ("prompt_version", self.prompt_version),
                ):
                    if getattr(result, field) is None and value is not None:
                        metadata[field] = value
                result = result.model_copy(update=metadata)
                if trajectory is not None and result.trajectory_path is None:
                    result = result.model_copy(
                        update={"trajectory_path": f"trajectories/{run_id}.json"}
                    )
                return result, trajectory
            except Exception as exc:
                return self._failure(task, run_id, str(exc))

    def _failure(
        self,
        task: RepairTask,
        run_id: str,
        error: str,
    ) -> tuple[EvaluationRunResult, None]:
        return (
            EvaluationRunResult(
                task_id=task.task_id,
                task_version=task.task_version,
                task_kind=task.task_kind,
                run_id=run_id,
                model=self.model,
                commit_sha=self.commit_sha,
                provider=self.provider,
                prompt_version=self.prompt_version,
                diagnosis_valid=False,
                evidence_valid=False,
                correct_file=False,
                diff_valid=False,
                patch_applied=False,
                tests_passed=False,
                failure_stage="HARNESS_FAILURE",
                failure_category="HARNESS_FAILURE",
                error=error,
            ),
            None,
        )
