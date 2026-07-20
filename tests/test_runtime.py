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
        first = runtime_a.run("Fix the Demo", DEMO_REPO, review_policy="controlled_auto")
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
        first = runtime.run("Fix the Demo", DEMO_REPO, review_policy="controlled_auto")
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
        first = runtime.run("Fix the Demo", DEMO_REPO, review_policy="controlled_auto")
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
        first = runtime.run(
            "Fix the Demo", DEMO_REPO, thread_id="thread-a",
            review_policy="controlled_auto",
        )
        second = runtime.run(
            "Fix the Demo", DEMO_REPO, thread_id="thread-b",
            review_policy="controlled_auto",
        )

        assert first.thread_id == "thread-a"
        assert second.thread_id == "thread-b"
        assert first.interrupted is True
        assert second.interrupted is True

        completed_a = runtime.resume("approved", thread_id="thread-a")
        completed_b = runtime.resume("approved", thread_id="thread-b")

    assert completed_a.workflow_status == "completed"
    assert completed_b.workflow_status == "completed"


def test_public_resume_accepts_approved_files_and_completes_override(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from codemedic.graph.builder import resume_workflow, run_workflow
    from codemedic.schemas.diagnosis import DiagnosisResult
    from codemedic.schemas.patch import PatchProposal

    monkeypatch.chdir(tmp_path)
    diagnosis = DiagnosisResult(
        suspected_files=[],
        root_cause="Needs explicit human authorization",
        evidence=[],
        confidence=0.3,
        missing_information=["Need review"],
    )
    proposal = PatchProposal(
        modified_files=FIX_FILES,
        unified_diff=FIX_PATCH,
        rationale="Fix the Demo bugs",
        risks=[],
        test_suggestions=[],
    )

    with (
        patch("codemedic.graph.nodes.run_investigator", return_value=diagnosis),
        patch("codemedic.agents.fixer.run_fixer", return_value=proposal),
    ):
        first = run_workflow("Fix the Demo", DEMO_REPO)
        assert first.workflow_status == "waiting_diagnosis_review"
        assert first.state["__interrupt__"][0].value["review_type"] == "diagnosis"

        second = resume_workflow(
            "accept_diagnosis",
            thread_id=first.thread_id,
            reason="Authorize the Demo files",
            approved_files=FIX_FILES,
        )
        assert second.workflow_status == "waiting_patch_review"
        assert second.state["__interrupt__"][0].value["review_type"] == "patch"
        assert second.state["approved_files"] == sorted(FIX_FILES)
        assert second.state["allowed_files"] == sorted(FIX_FILES)

        completed = resume_workflow("approved", thread_id=first.thread_id)

    assert completed.thread_id == first.thread_id
    assert completed.workflow_status == "completed"
    assert completed.state["final_status"] == "通过"


def test_compile_workflow_defaults_to_memory_without_sqlite_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from codemedic.graph.builder import compile_workflow

    monkeypatch.chdir(tmp_path)

    compile_workflow()

    assert not (tmp_path / "runtime" / "checkpoints" / "codemedic.db").exists()


def test_runtime_get_state_recovers_diagnosis_review_after_reopen(tmp_path: Path) -> None:
    from codemedic.graph.runtime import WorkflowRuntime
    from codemedic.schemas.diagnosis import DiagnosisResult

    diagnosis = DiagnosisResult(
        suspected_files=[],
        root_cause="Needs human diagnosis review",
        evidence=[],
        confidence=0.2,
        missing_information=["More evidence required"],
    )
    _, patch_proposal = _mock_agent_outputs()
    db_path = tmp_path / "checkpoint.db"

    with (
        patch("codemedic.graph.nodes.run_investigator", return_value=diagnosis),
        patch("codemedic.agents.fixer.run_fixer", return_value=patch_proposal),
        WorkflowRuntime(db_path) as runtime_a,
    ):
        first = runtime_a.run("Fix the Demo", DEMO_REPO, review_policy="manual")

    with (
        patch("codemedic.graph.nodes.run_investigator", return_value=diagnosis),
        patch("codemedic.agents.fixer.run_fixer", return_value=patch_proposal),
        WorkflowRuntime(db_path) as runtime_b,
    ):
        recovered = runtime_b.get_state(first.thread_id)
        assert recovered.interrupted is True
        assert recovered.workflow_status == "waiting_diagnosis_review"
        assert recovered.state["__interrupt__"][0].value["review_type"] == "diagnosis"

        resumed = runtime_b.resume(
            "accept_diagnosis",
            thread_id=first.thread_id,
            approved_files=FIX_FILES,
        )

    assert resumed.workflow_status == "waiting_patch_review"


def test_runtime_get_state_recovers_patch_review_after_reopen(tmp_path: Path) -> None:
    from codemedic.graph.runtime import WorkflowRuntime

    diagnosis, patch_proposal = _mock_agent_outputs()
    db_path = tmp_path / "checkpoint.db"

    with (
        patch("codemedic.graph.nodes.run_investigator", return_value=diagnosis),
        patch("codemedic.agents.fixer.run_fixer", return_value=patch_proposal),
        WorkflowRuntime(db_path) as runtime_a,
    ):
        first = runtime_a.run("Fix the Demo", DEMO_REPO, review_policy="controlled_auto")

    with WorkflowRuntime(db_path) as runtime_b:
        recovered = runtime_b.get_state(first.thread_id)

    assert recovered.interrupted is True
    assert recovered.workflow_status == "waiting_patch_review"
    assert recovered.state["__interrupt__"][0].value["review_type"] == "patch"
