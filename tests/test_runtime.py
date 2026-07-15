"""Tests for SQLite checkpoint lifecycle and cross-runtime recovery."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from codemedic.schemas.patch import PatchProposal
from tests.factories import build_demo_diagnosis
from tests.test_real_sandbox_e2e import FIX_FILES, FIX_PATCH

DEMO_REPO = str(Path(__file__).resolve().parent.parent / "demo_repos" / "sample_project")


def _mock_agent_outputs() -> tuple[object, PatchProposal]:
    return (
        build_demo_diagnosis(),
        PatchProposal(
            modified_files=FIX_FILES,
            unified_diff=FIX_PATCH,
            rationale="Fix all Demo failures",
            risks=[],
            test_suggestions=[],
        ),
    )


def test_sqlite_checkpoint_resume_after_runtime_reopen(tmp_path: Path) -> None:
    from codemedic.graph.runtime import WorkflowRuntime

    diagnosis, patch_proposal = _mock_agent_outputs()
    db_path = tmp_path / "checkpoint.db"

    with (
        patch("codemedic.graph.nodes.run_investigator", return_value=diagnosis),
        patch("codemedic.agents.fixer.run_fixer", return_value=patch_proposal),
        WorkflowRuntime(db_path) as runtime_a,
    ):
        first = runtime_a.run("Fix the Demo", DEMO_REPO)
        assert first.interrupted is True
        assert first.workflow_status == "waiting_patch_review"
        thread_id = first.thread_id

    with (
        patch("codemedic.graph.nodes.run_investigator", return_value=diagnosis),
        patch("codemedic.agents.fixer.run_fixer", return_value=patch_proposal),
        WorkflowRuntime(db_path) as runtime_b,
    ):
        completed = runtime_b.resume("approved", thread_id=thread_id)

    assert completed.thread_id == thread_id
    assert completed.workflow_status == "completed"
    assert completed.state["final_status"] == "通过"


def test_runtime_rejects_unknown_thread_id(tmp_path: Path) -> None:
    from codemedic.graph.runtime import WorkflowRuntime

    with WorkflowRuntime(tmp_path / "checkpoint.db") as runtime:
        with pytest.raises(ValueError, match="No resumable checkpoint"):
            runtime.resume("approved", thread_id="missing-thread")


def test_runtime_rejects_corrupt_checkpoint_database(tmp_path: Path) -> None:
    from codemedic.graph.runtime import WorkflowRuntime

    db_path = tmp_path / "checkpoint.db"
    db_path.write_bytes(b"not a sqlite database")

    with pytest.raises(sqlite3.DatabaseError):
        WorkflowRuntime(db_path)


def test_runtime_unknown_review_decision_is_fail_closed(tmp_path: Path) -> None:
    from codemedic.graph.runtime import WorkflowRuntime

    diagnosis, patch_proposal = _mock_agent_outputs()
    db_path = tmp_path / "checkpoint.db"

    with (
        patch("codemedic.graph.nodes.run_investigator", return_value=diagnosis),
        patch("codemedic.agents.fixer.run_fixer", return_value=patch_proposal),
        WorkflowRuntime(db_path) as runtime,
    ):
        first = runtime.run("Fix the Demo", DEMO_REPO)
        result = runtime.resume("banana", thread_id=first.thread_id)

    assert result.state["final_status"] == "拒绝"


def test_runtime_rejects_resume_after_completion(tmp_path: Path) -> None:
    from codemedic.graph.runtime import WorkflowRuntime

    diagnosis, patch_proposal = _mock_agent_outputs()
    db_path = tmp_path / "checkpoint.db"

    with (
        patch("codemedic.graph.nodes.run_investigator", return_value=diagnosis),
        patch("codemedic.agents.fixer.run_fixer", return_value=patch_proposal),
        WorkflowRuntime(db_path) as runtime,
    ):
        first = runtime.run("Fix the Demo", DEMO_REPO)
        runtime.resume("approved", thread_id=first.thread_id)

        with pytest.raises(ValueError, match="No resumable checkpoint"):
            runtime.resume("approved", thread_id=first.thread_id)


def test_runtime_keeps_two_threads_independent(tmp_path: Path) -> None:
    from codemedic.graph.runtime import WorkflowRuntime

    diagnosis, patch_proposal = _mock_agent_outputs()
    db_path = tmp_path / "checkpoint.db"

    with (
        patch("codemedic.graph.nodes.run_investigator", return_value=diagnosis),
        patch("codemedic.agents.fixer.run_fixer", return_value=patch_proposal),
        WorkflowRuntime(db_path) as runtime,
    ):
        first = runtime.run("Fix the Demo", DEMO_REPO, thread_id="thread-a")
        second = runtime.run("Fix the Demo", DEMO_REPO, thread_id="thread-b")

        assert first.thread_id == "thread-a"
        assert second.thread_id == "thread-b"
        assert first.interrupted is True
        assert second.interrupted is True

        completed_a = runtime.resume("approved", thread_id="thread-a")
        completed_b = runtime.resume("approved", thread_id="thread-b")

    assert completed_a.workflow_status == "completed"
    assert completed_b.workflow_status == "completed"
