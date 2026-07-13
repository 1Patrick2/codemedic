"""Tests for Pydantic result schemas and validators."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from codemedic.schemas.results import (
    DiffValidationResult,
    EvidenceValidationResult,
    PatchApplyResult,
    TestResult,
    WorkflowRunResult,
)


class TestEvidenceValidationResult:
    def test_valid_result(self) -> None:
        r = EvidenceValidationResult(valid=True)
        assert r.valid is True
        assert r.errors == []
        assert r.validated_evidence == []

    def test_invalid_result(self) -> None:
        r = EvidenceValidationResult(
            valid=False, errors=["File not found"]
        )
        assert r.valid is False
        assert len(r.errors) == 1


class TestDiffValidationResult:
    def test_valid_result(self) -> None:
        r = DiffValidationResult(valid=True)
        assert r.valid is True

    def test_with_violations(self) -> None:
        r = DiffValidationResult(
            valid=False,
            errors=["File not allowed"],
            violations=["src/outside.py"],
        )
        assert r.valid is False
        assert r.violations == ["src/outside.py"]


class TestPatchApplyResult:
    def test_success(self) -> None:
        r = PatchApplyResult(
            success=True, returncode=0,
            sandbox_path="/tmp/sandbox",
        )
        assert r.success is True
        assert r.sandbox_path == "/tmp/sandbox"

    def test_failure(self) -> None:
        r = PatchApplyResult(
            success=False, returncode=1,
            stderr="git apply failed",
        )
        assert r.success is False
        assert r.sandbox_path is None

    def test_negative_returncode(self) -> None:
        r = PatchApplyResult(success=False, returncode=-1)
        assert r.returncode == -1


class TestTestResult:
    def test_minimal_fields(self) -> None:
        r = TestResult(
            command_id="test_0",
            argv=["python", "-m", "pytest"],
            returncode=0,
        )
        assert r.command_id == "test_0"
        assert r.returncode == 0
        assert r.duration_ms >= 0

    def test_timeout(self) -> None:
        r = TestResult(
            command_id="test_0",
            argv=["python", "-m", "pytest"],
            returncode=-1,
            timed_out=True,
            stderr="Timed out",
        )
        assert r.timed_out is True
        assert r.duration_ms >= 0

    def test_output_truncated(self) -> None:
        r = TestResult(
            command_id="test_0",
            argv=["python", "-m", "pytest"],
            returncode=0,
            output_truncated=True,
        )
        assert r.output_truncated is True

    def test_default_duration_is_zero(self) -> None:
        r = TestResult(
            command_id="test_0",
            argv=["python", "-m", "pytest"],
            returncode=0,
        )
        assert r.duration_ms == 0


class TestWorkflowRunResult:
    def test_minimal_fields(self) -> None:
        r = WorkflowRunResult(
            task_id="task_abc",
            thread_id="thread_xyz",
            workflow_status="running",
            interrupted=False,
        )
        assert r.task_id == "task_abc"
        assert r.thread_id == "thread_xyz"
        assert r.workflow_status == "running"

    def test_rejects_empty_task_id(self) -> None:
        with pytest.raises(ValidationError):
            WorkflowRunResult(
                task_id="",
                thread_id="thread_xyz",
                workflow_status="running",
                interrupted=False,
            )

    def test_rejects_empty_thread_id(self) -> None:
        with pytest.raises(ValidationError):
            WorkflowRunResult(
                task_id="task_abc",
                thread_id="",
                workflow_status="running",
                interrupted=False,
            )

    def test_rejects_invalid_status(self) -> None:
        with pytest.raises(ValidationError):
            WorkflowRunResult(
                task_id="task_abc",
                thread_id="thread_xyz",
                workflow_status="invalid_status",  # type: ignore[arg-type]
                interrupted=False,
            )

class TestSerialization:
    """Test round-trip model_dump -> model_validate."""

    def test_diff_validation_round_trip(self) -> None:
        original = DiffValidationResult(
            valid=False, errors=["error"], modified_files=["f.py"]
        )
        data = original.model_dump()
        restored = DiffValidationResult.model_validate(data)
        assert restored.valid == original.valid
        assert restored.errors == original.errors

    def test_patch_apply_result_round_trip(self) -> None:
        original = PatchApplyResult(
            success=True, returncode=0,
            sandbox_path="/tmp/sbox",
        )
        data = original.model_dump()
        restored = PatchApplyResult.model_validate(data)
        assert restored.success == original.success
        assert restored.sandbox_path == original.sandbox_path

    def test_test_result_round_trip(self) -> None:
        original = TestResult(
            command_id="t0", argv=["pytest"], returncode=0,
            duration_ms=100, output_truncated=True,
        )
        data = original.model_dump()
        restored = TestResult.model_validate(data)
        assert restored.duration_ms == 100
        assert restored.output_truncated is True
