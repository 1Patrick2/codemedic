"""Owned SQLite runtime for resumable workflow executions."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from types import TracebackType
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from codemedic.config import settings
from codemedic.graph.state import create_initial_state
from codemedic.schemas.results import WorkflowRunResult
from codemedic.tools.docker_runtime import ExecutionBackend
from codemedic.tracing.events import EventType
from codemedic.tracing.recorder import (
    TrajectoryRecorder,
    register_recorder,
    unregister_recorder,
)


class WorkflowRuntime:
    """Manage a compiled workflow and the SQLite connection it owns."""

    def __init__(
        self,
        checkpoint_path: str | Path | None = None,
        *,
        retrieval_node: Any | None = None,
        execution_backend: str | ExecutionBackend | None = None,
    ) -> None:
        self._closed = False
        self._execution_backend = ExecutionBackend(
            execution_backend or settings.execution_backend,
        )
        self._checkpoint_path = Path(checkpoint_path or self._default_checkpoint_path())
        self._checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(
            str(self._checkpoint_path),
            detect_types=sqlite3.PARSE_DECLTYPES,
            check_same_thread=False,
        )

        try:
            self._checkpointer = SqliteSaver(self._connection)
            self._checkpointer.setup()
            from codemedic.graph import builder

            self._graph = builder.compile_workflow(
                checkpointer=self._checkpointer,
                retrieval_node=retrieval_node,
            )
        except Exception:
            self._connection.close()
            raise

    @staticmethod
    def _default_checkpoint_path() -> Path:
        return Path.cwd() / "runtime" / "checkpoints" / "codemedic.db"

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("WorkflowRuntime is closed")

    def run(
        self,
        issue: str,
        repository_path: str,
        error_log: str | None = None,
        *,
        thread_id: str | None = None,
        execution_backend: str | ExecutionBackend | None = None,
        review_policy: str | None = None,
        model: str | None = None,
        test_commands: list[list[str]] | None = None,
        max_retries: int | None = None,
    ) -> WorkflowRunResult:
        """Invoke a new workflow and return its structured snapshot."""
        self._ensure_open()
        tid = thread_id or str(uuid.uuid4())
        run_id = f"run_{uuid.uuid4().hex}"

        # Per-run config overrides — applied temporarily to settings
        _original_model = None
        if model is not None and model != settings.openai_model_name:
            _original_model = settings.openai_model_name
            settings.openai_model_name = model
        _original_retries = None
        if max_retries is not None and max_retries != settings.max_fixer_retries:
            _original_retries = settings.max_fixer_retries
            settings.max_fixer_retries = max_retries

        initial = create_initial_state(
            issue=issue,
            repository_path=repository_path,
            error_log=error_log,
            run_id=run_id,
            thread_id=tid,
            execution_backend=ExecutionBackend(
                execution_backend or self._execution_backend,
            ).value,
            review_policy=review_policy or "manual",
            test_commands=test_commands,
        )
        recorder = TrajectoryRecorder(run_id, tid)
        register_recorder(recorder)
        try:
            result = self._graph.invoke(initial, self._config(tid))
            return self._record_invocation_result(recorder, result, tid)
        except Exception as exc:
            recorder.record(
                node="workflow",
                event_type="workflow_failed",
                summary="Workflow run failed",
                output_data={"error": str(exc)},
            )
            recorder.finish("failed")
            raise
        finally:
            unregister_recorder(run_id, tid)
            if _original_model is not None:
                settings.openai_model_name = _original_model
            if _original_retries is not None:
                settings.max_fixer_retries = _original_retries
            recorder.close()

    def resume(
        self,
        decision: str,
        *,
        thread_id: str,
        reason: str = "",
        approved_files: list[str] | None = None,
    ) -> WorkflowRunResult:
        """Resume a thread only when its latest checkpoint is interruptible."""
        self._ensure_open()
        config = self._config(thread_id)
        get_state = getattr(self._graph, "get_state", None)
        if get_state is not None:
            snapshot = get_state(config)
            if not getattr(snapshot, "next", ()):
                raise ValueError(f"No resumable checkpoint for thread_id: {thread_id}")

        state = snapshot.values if get_state is not None else {}
        run_id = str(state.get("run_id") or f"run_{thread_id}")
        recorder = TrajectoryRecorder(run_id, thread_id)
        register_recorder(recorder)
        recorder.record(
            node="workflow",
            event_type="resume",
            summary="Workflow resumed from human decision",
            input_data={
                "decision": decision,
                "reason": reason,
                "approved_files": approved_files or [],
            },
        )
        resume_value: dict[str, Any] = {"decision": decision, "reason": reason}
        if approved_files is not None:
            resume_value["approved_files"] = approved_files
        command: Command = Command(resume=resume_value)
        try:
            result = self._graph.invoke(command, config)
            return self._record_invocation_result(recorder, result, thread_id)
        except Exception as exc:
            recorder.record(
                node="workflow",
                event_type="workflow_failed",
                summary="Workflow resume failed",
                output_data={"error": str(exc)},
            )
            recorder.finish("failed")
            raise
        finally:
            unregister_recorder(run_id, thread_id)
            recorder.close()

    def get_state(self, thread_id: str) -> WorkflowRunResult:
        """Read the latest state for a thread without changing the workflow."""
        self._ensure_open()
        snapshot = self._graph.get_state(self._config(thread_id))
        state = dict(snapshot.values) if isinstance(snapshot.values, dict) else {}
        if not state:
            raise ValueError(f"No workflow state for thread_id: {thread_id}")
        from codemedic.graph import builder

        return builder._make_workflow_result_from_snapshot(snapshot, thread_id)

    @staticmethod
    def _record_invocation_result(
        recorder: TrajectoryRecorder,
        state: dict[str, Any],
        thread_id: str,
    ) -> WorkflowRunResult:
        workflow_result = WorkflowRuntime._make_result(state, thread_id)
        if workflow_result.interrupted:
            recorder.record(
                node="workflow",
                event_type="interrupt",
                summary="Workflow paused for human review",
                output_data={"workflow_status": workflow_result.workflow_status},
            )
        else:
            event_type: EventType = (
                "workflow_failed"
                if workflow_result.workflow_status == "failed"
                else "workflow_completed"
            )
            recorder.record(
                node="workflow",
                event_type=event_type,
                summary=f"Workflow {workflow_result.workflow_status}",
                output_data={
                    "workflow_status": workflow_result.workflow_status,
                    "final_status": state.get("final_status"),
                },
            )
        recorder.finish(workflow_result.workflow_status)
        return workflow_result

    @staticmethod
    def _config(thread_id: str) -> dict[str, dict[str, str]]:
        return {"configurable": {"thread_id": thread_id}}

    @staticmethod
    def _make_result(state: dict[str, Any], thread_id: str) -> WorkflowRunResult:
        from codemedic.graph import builder

        return builder._make_workflow_result(state, thread_id)

    def close(self) -> None:
        """Close the owned SQLite connection; safe to call repeatedly."""
        if not self._closed:
            self._connection.close()
            self._closed = True

    def __enter__(self) -> "WorkflowRuntime":
        self._ensure_open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
