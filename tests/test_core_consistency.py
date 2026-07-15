"""Regression tests for Core Consistency Fix behavior."""

from __future__ import annotations

import tempfile
import uuid
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
from codemedic.graph.nodes import (
    apply_patch_node,
    diagnosis_review_node,
    fixer_node,
    intake,
    patch_review_node,
    patch_validation_node,
    prepare_fix_retry,
    run_tests_node,
)
from codemedic.graph.routers import (
    INSUFFICIENT,
    UNCERTAIN,
    diagnosis_review_router,
    intake_router,
    patch_apply_router,
    patch_review_router,
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
        PatchApplyResult(
            success=True, returncode=0, sandbox_path="sandbox",
        )


def test_patch_apply_router_validates_state_result() -> None:
    state = create_initial_state("issue", DEMO_REPO)
    state["patch_apply_result"] = {"success": True}

    with pytest.raises(ValidationError):
        patch_apply_router(state)


def test_patch_apply_failure_routes_to_final_report_without_tests() -> None:
    from tests.test_real_sandbox_e2e import FIX_FILES, FIX_PATCH

    state = create_initial_state("issue", DEMO_REPO)
    state["allowed_files"] = FIX_FILES
    state["patch"] = {
        "modified_files": FIX_FILES,
        "unified_diff": FIX_PATCH,
    }

    with (
        patch("codemedic.tools.sandbox.create_temp_copy", return_value="sandbox"),
        patch(
            "codemedic.tools.sandbox.apply_patch",
            return_value={
                "success": False,
                "returncode": 1,
                "stdout": "",
                "stderr": "git apply failed",
                "modified_files": [],
            },
        ),
        patch("codemedic.tools.sandbox.cleanup_sandbox") as cleanup,
    ):
        result = apply_patch_node(state)

    assert result["patch_apply_result"]["success"] is False
    assert result["sandbox_path"] is None
    assert result["sandbox_cleaned"] is True
    assert patch_apply_router({**state, **result}) == "failed"
    cleanup.assert_called_once_with("sandbox")


def test_patch_validation_missing_result_fails_closed() -> None:
    state = create_initial_state("issue", DEMO_REPO)

    assert patch_validation_router(state) == "invalid_final"


def test_intake_router_fails_closed_for_missing_repository() -> None:
    state = create_initial_state("issue", "/nonexistent/path")
    state.update(intake(state))

    assert intake_router(state) == "invalid"


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
        "modified_files": ["src/utils/math_helpers.py"],
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
        "modified_files": ["src/utils/math_helpers.py"],
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


def test_fixer_receives_retry_feedback() -> None:
    state = create_initial_state("issue", DEMO_REPO)
    state["diagnosis"] = DiagnosisResult(
        suspected_files=["src/utils/math_helpers.py"],
        root_cause="known test cause",
        evidence=[],
        confidence=0.8,
        missing_information=[],
    )
    state["previous_patch"] = {"unified_diff": "old patch"}
    state["failure_feedback"] = "test_factorial failed"
    state["human_feedback"] = "Please fix the loop update."
    state["verifier_summary"] = "The factorial test still fails."

    from codemedic.schemas.patch import PatchProposal

    with patch(
        "codemedic.agents.fixer.run_fixer",
        return_value=PatchProposal(
            modified_files=[], unified_diff="", rationale="", risks=[],
            test_suggestions=[],
        ),
    ) as mock_fixer:
        fixer_node(state)

    call = mock_fixer.call_args.kwargs
    assert call["previous_patch"] == {"unified_diff": "old patch"}
    assert call["failure_feedback"] == "test_factorial failed"
    assert call["human_feedback"] == "Please fix the loop update."
    assert call["verifier_summary"] == "The factorial test still fails."


def test_patch_review_allows_approval_at_retry_limit() -> None:
    state = create_initial_state("issue", DEMO_REPO)
    state["retry_count"] = 1
    state["patch"] = {"unified_diff": "valid"}

    with patch(
        "langgraph.types.interrupt",
        return_value={"decision": "approved", "reason": "looks good"},
    ) as mock_interrupt:
        result = patch_review_node(state)

    options = mock_interrupt.call_args.args[0]["options"]
    assert options == ["approved", "rejected"]
    assert result["human_decision"] == "approved"


def test_patch_review_rejects_direct_retry_at_retry_limit() -> None:
    state = create_initial_state("issue", DEMO_REPO)
    state["retry_count"] = 1
    state["human_decision"] = "retry"

    assert patch_review_router(state) == "rejected"


def test_initial_state_starts_without_approved_files() -> None:
    state = create_initial_state("issue", DEMO_REPO)

    assert state["approved_files"] == []


def test_diagnosis_review_merges_valid_approved_files() -> None:
    state = create_initial_state("issue", DEMO_REPO)
    state["allowed_files"] = ["src/utils/math_helpers.py"]

    with patch(
        "langgraph.types.interrupt",
        return_value={
            "decision": "accept_diagnosis",
            "approved_files": ["src/services/data_service.py"],
            "reason": "Authorize the second Demo file",
        },
    ):
        result = diagnosis_review_node(state)

    assert result["human_decision"] == "accept_diagnosis"
    assert result["approved_files"] == ["src/services/data_service.py"]
    assert result["allowed_files"] == [
        "src/services/data_service.py",
        "src/utils/math_helpers.py",
    ]
    assert diagnosis_review_router({**state, **result}) == "accept_diagnosis"


def test_diagnosis_review_rejects_invalid_approved_files_without_partial_merge() -> None:
    state = create_initial_state("issue", DEMO_REPO)
    state["allowed_files"] = ["src/utils/math_helpers.py"]

    with patch(
        "langgraph.types.interrupt",
        return_value={
            "decision": "accept_diagnosis",
            "approved_files": ["src/services/data_service.py", "../outside.py"],
        },
    ):
        result = diagnosis_review_node(state)

    assert result["human_decision"] == "reject"
    assert result["approved_files"] == []
    assert result["allowed_files"] == ["src/utils/math_helpers.py"]
    assert diagnosis_review_router({**state, **result}) == "reject"


def test_diagnosis_review_rejects_empty_authorization_without_allowed_files() -> None:
    state = create_initial_state("issue", DEMO_REPO)

    with patch(
        "langgraph.types.interrupt",
        return_value={"decision": "accept_diagnosis", "approved_files": []},
    ):
        result = diagnosis_review_node(state)

    assert result["human_decision"] == "reject"
    assert result["approved_files"] == []
    assert result["allowed_files"] == []
    assert any("no authorized files" in error.lower() for error in result["errors"])


def test_review_nodes_normalize_unknown_decisions() -> None:
    state = create_initial_state("issue", DEMO_REPO)

    with patch("langgraph.types.interrupt", return_value={"decision": "unknown"}):
        diagnosis_result = diagnosis_review_node(state)
        patch_result = patch_review_node({**state, "patch": {"unified_diff": "valid"}})

    assert diagnosis_result["human_decision"] == "reject"
    assert patch_result["human_decision"] == "rejected"
    assert diagnosis_review_router({**state, **diagnosis_result}) == "reject"
    assert patch_review_router({**state, **patch_result}) == "rejected"


def test_patch_review_normalizes_non_string_decision() -> None:
    state = create_initial_state("issue", DEMO_REPO)

    with patch("langgraph.types.interrupt", return_value={"decision": []}):
        result = patch_review_node({**state, "patch": {"unified_diff": "valid"}})

    assert result["human_decision"] == "rejected"


def test_run_tests_clears_sandbox_path_after_cleanup() -> None:
    sandbox = Path(tempfile.gettempdir()) / f"codemedic_sandbox_{uuid.uuid4().hex}"
    sandbox.mkdir()
    state = create_initial_state("issue", DEMO_REPO)
    state["sandbox_path"] = str(sandbox)

    with patch(
        "codemedic.tools.test_runner.run_tests",
        return_value=[TestResult(
            command_id="cmd_0",
            argv=["python", "-m", "pytest"],
            returncode=0,
        )],
    ):
        result = run_tests_node(state)

    assert result["sandbox_path"] is None
    assert result["sandbox_cleaned"] is True
    assert not sandbox.exists()


def test_patch_validation_checks_declared_files_once() -> None:
    state = create_initial_state("issue", DEMO_REPO)
    state["allowed_files"] = ["src/utils/math_helpers.py"]
    state["patch"] = {
        "modified_files": ["src/other.py"],
        "unified_diff": "\n".join([
            "--- a/src/utils/math_helpers.py",
            "+++ b/src/utils/math_helpers.py",
            "@@ -1 +1 @@",
            "-old",
            "+new",
        ]),
    }

    result = patch_validation_node(state)

    assert result["diff_validation"]["valid"] is False
    assert any("Declared but not in diff" in e for e in result["diff_validation"]["errors"])


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
    state["diff_validation"] = {
        "valid": True,
        "modified_files": ["src/utils/math_helpers.py"],
    }
    state["patch_apply_result"] = {
        "success": True,
        "returncode": 0,
        "modified_files": ["src/utils/math_helpers.py"],
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
