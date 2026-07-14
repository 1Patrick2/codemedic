"""Regression tests for Core Consistency Fix behavior."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from codemedic.agents.verifier import _format_test_results
from codemedic.graph.builder import (
    _make_workflow_result,
    resume_workflow,
    run_workflow,
)
from codemedic.graph.nodes import fixer_node, prepare_fix_retry
from codemedic.graph.routers import (
    INSUFFICIENT,
    UNCERTAIN,
    patch_apply_router,
    patch_validation_router,
    verify_router,
)
from codemedic.graph.state import RepairState, create_initial_state
from codemedic.graph.status import derive_final_status
from codemedic.schemas.diagnosis import DiagnosisResult
from codemedic.schemas.results import PatchApplyResult, TestResult

DEMO_REPO = str(Path(__file__).resolve().parent.parent / "demo_repos" / "sample_project")


def test_patch_apply_result_rejects_inconsistent_success() -> None:
    with pytest.raises(ValidationError):
        PatchApplyResult(success=True, returncode=1)


def test_patch_apply_result_requires_sandbox_on_success() -> None:
    with pytest.raises(ValidationError):
        PatchApplyResult(success=True, returncode=0)


def test_patch_apply_router_validates_state_result() -> None:
    state = create_initial_state("issue", DEMO_REPO)
    state["patch_apply_result"] = {"success": True}

    with pytest.raises(ValidationError):
        patch_apply_router(state)


def test_patch_validation_missing_result_fails_closed() -> None:
    state = create_initial_state("issue", DEMO_REPO)

    assert patch_validation_router(state) == "invalid_final"


def test_patch_validation_invalid_result_retries_only_with_budget() -> None:
    state = create_initial_state("issue", DEMO_REPO)
    state["diff_validation"] = {"valid": False}

    assert patch_validation_router(state) == "invalid_retry"

    state["retry_count"] = 1
    assert patch_validation_router(state) == "invalid_final"


def test_verify_router_validates_test_result_schema() -> None:
    state = create_initial_state("issue", DEMO_REPO)
    state["patch_apply_result"] = {
        "success": True,
        "returncode": 0,
        "sandbox_path": "sandbox",
    }
    state["test_results"] = [{"returncode": 0}]

    with pytest.raises(ValidationError):
        verify_router(state)


def test_verify_router_stops_after_retry_budget() -> None:
    state = create_initial_state("issue", DEMO_REPO)
    state["patch_apply_result"] = {
        "success": True,
        "returncode": 0,
        "sandbox_path": "sandbox",
    }
    state["test_results"] = [
        TestResult(
            command_id="cmd_0",
            argv=["python", "-m", "pytest"],
            returncode=1,
        ).model_dump()
    ]

    assert verify_router(state) == INSUFFICIENT

    state["retry_count"] = 1
    assert verify_router(state) == UNCERTAIN


def test_fixer_failure_still_counts_as_an_attempt() -> None:
    state = create_initial_state("issue", DEMO_REPO)
    state["diagnosis"] = DiagnosisResult(
        suspected_files=["src/utils/math_helpers.py"],
        root_cause="known test cause",
        evidence=[],
        confidence=0.8,
        missing_information=[],
    )

    with patch(
        "codemedic.agents.fixer.run_fixer",
        side_effect=RuntimeError("provider failed"),
    ):
        result = fixer_node(state)

    assert result["fix_attempt_count"] == 1


def test_prepare_fix_retry_is_the_only_retry_counter_transition() -> None:
    state = create_initial_state("issue", DEMO_REPO)
    state["patch"] = {"unified_diff": "old"}

    result = prepare_fix_retry(state)

    assert result["previous_patch"] == {"unified_diff": "old"}
    assert result["retry_count"] == 1
    assert result["patch"] is None
    assert result["diff_validation"] is None


def test_run_workflow_returns_consistent_public_result() -> None:
    class FakeAgent:
        def invoke(self, initial: RepairState, config: dict) -> dict:
            assert initial["thread_id"] == config["configurable"]["thread_id"]
            return {**initial, "final_status": "人工复核"}

    with patch("codemedic.graph.builder.compile_workflow", return_value=FakeAgent()):
        result = run_workflow("issue", DEMO_REPO)

    assert result.thread_id
    assert result.state["thread_id"] == result.thread_id
    assert result.state["workflow_status"] == "completed"


def test_resume_workflow_preserves_public_thread_id() -> None:
    class FakeAgent:
        def invoke(self, command: object, config: dict) -> dict:
            assert config["configurable"]["thread_id"] == "thread_1"
            return {"task_id": "task_1", "final_status": "人工复核"}

    with patch("codemedic.graph.builder.compile_workflow", return_value=FakeAgent()):
        result = resume_workflow("approved", thread_id="thread_1")

    assert result.thread_id == "thread_1"
    assert result.state["thread_id"] == "thread_1"


def test_workflow_result_snapshot_contains_thread_and_status() -> None:
    result = _make_workflow_result(
        {"task_id": "task_1", "final_status": "人工复核"},
        "thread_1",
    )

    assert result.thread_id == "thread_1"
    assert result.workflow_status == "completed"
    assert result.state["thread_id"] == "thread_1"
    assert result.state["workflow_status"] == "completed"


def test_verifier_formats_serialized_test_result_fields() -> None:
    report = _format_test_results([
        {
            "command_id": "cmd_0",
            "argv": ["python", "-m", "pytest", "-q"],
            "returncode": 0,
            "timed_out": False,
        }
    ])

    assert "cmd_0" in report
    assert "python -m pytest -q" in report
    assert "Return code: 0" in report


def test_final_status_requires_all_success_conditions() -> None:
    state = create_initial_state("issue", DEMO_REPO)
    state["diff_validation"] = {"valid": True}
    state["patch_apply_result"] = {
        "success": True,
        "returncode": 0,
        "sandbox_path": "sandbox",
    }
    state["test_results"] = [
        TestResult(
            command_id="cmd_0",
            argv=["python", "-m", "pytest"],
            returncode=0,
        ).model_dump()
    ]

    assert derive_final_status(state) == "通过"

    state["test_results"] = []
    assert derive_final_status(state) == "人工复核"


def test_final_status_prioritizes_human_rejection() -> None:
    state = create_initial_state("issue", DEMO_REPO)
    state["human_decision"] = "rejected"

    assert derive_final_status(state) == "拒绝"
