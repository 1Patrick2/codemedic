"""Explicit Pydantic result contracts for workflow stages.

Each stage produces a typed result that replaces implicit dict/string checks.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from codemedic.schemas.diagnosis import Evidence


class EvidenceValidationResult(BaseModel):
    """Result of evidence validation against the filesystem."""

    valid: bool = Field(description="Whether all evidence passed validation")
    errors: list[str] = Field(default_factory=list, description="Validation error messages")
    validated_evidence: list[Evidence] = Field(
        default_factory=list,
        description="Evidence items that passed validation",
    )

    @model_validator(mode="after")
    def validate_consistency(self) -> "EvidenceValidationResult":
        if self.valid and self.errors:
            raise ValueError("Valid evidence cannot contain errors")
        return self


class DiffValidationResult(BaseModel):
    """Result of Unified Diff security validation."""

    valid: bool = Field(description="Whether the diff passes all security checks")
    errors: list[str] = Field(default_factory=list, description="Validation error messages")
    modified_files: list[str] = Field(
        default_factory=list,
        description="Files actually modified according to diff headers",
    )
    violations: list[str] = Field(
        default_factory=list,
        description="Security violations found",
    )

    @model_validator(mode="after")
    def validate_consistency(self) -> "DiffValidationResult":
        if self.valid and (self.errors or self.violations):
            raise ValueError("Valid diff cannot contain errors or violations")
        return self


class PatchApplyResult(BaseModel):
    """Result of applying a patch in the sandbox."""

    success: bool = Field(description="Whether git apply succeeded")
    returncode: int = Field(description="Exit code from git apply")
    stdout: str = Field(default="", description="stdout from git apply")
    stderr: str = Field(default="", description="stderr from git apply")
    modified_files: list[str] = Field(
        default_factory=list,
        description="Files actually modified in the sandbox",
    )
    sandbox_path: str | None = Field(
        default=None,
        description="Path to the sandbox directory",
    )

    @model_validator(mode="after")
    def validate_success_state(self) -> "PatchApplyResult":
        """Reject contradictory success results."""
        if self.success and self.returncode != 0:
            raise ValueError("Successful patch application must have returncode 0")
        if self.success and self.sandbox_path is None:
            raise ValueError("Successful patch application requires sandbox_path")
        if self.success and not self.modified_files:
            raise ValueError("Successful patch application must report modified_files")
        return self


class TestResult(BaseModel):
    """Result of a single test command execution."""

    __test__ = False  # prevent pytest from collecting this as a test class

    command_id: str = Field(description="Identifier for this test command")
    argv: list[str] = Field(description="Full command argument list")
    returncode: int = Field(description="Exit code from the test process")
    stdout: str = Field(default="", description="Captured stdout (may be truncated)")
    stderr: str = Field(default="", description="Captured stderr (may be truncated)")
    timed_out: bool = Field(default=False, description="Whether the test timed out")
    duration_ms: int = Field(default=0, ge=0, description="Execution duration in milliseconds")
    output_truncated: bool = Field(default=False, description="Whether output was truncated")

    @model_validator(mode="after")
    def validate_timeout_state(self) -> "TestResult":
        if self.timed_out and self.returncode == 0:
            raise ValueError("Timed out test cannot have returncode 0")
        return self


WorkflowStatus = Literal[
    "running",
    "waiting_diagnosis_review",
    "waiting_patch_review",
    "completed",
    "failed",
]


class WorkflowRunResult(BaseModel):
    """Result returned to the caller after a workflow invocation."""

    task_id: str = Field(min_length=1, description="Unique task identifier")
    thread_id: str = Field(min_length=1, description="Thread ID for checkpoint resumption")
    workflow_status: WorkflowStatus = Field(description="Current workflow status")
    interrupted: bool = Field(description="Whether the workflow was interrupted")
    state: dict[str, Any] = Field(default_factory=dict, description="Full workflow state snapshot")

    @model_validator(mode="after")
    def validate_status_state(self) -> "WorkflowRunResult":
        waiting = self.workflow_status in {
            "waiting_diagnosis_review",
            "waiting_patch_review",
        }
        if self.interrupted != waiting:
            raise ValueError(
                "WorkflowRunResult interrupted must match waiting workflow_status"
            )
        return self
