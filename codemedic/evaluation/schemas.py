"""Typed contracts for reproducible repair evaluations."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

FailureCategory = Literal[
    "Provider Failure",
    "Timeout",
    "JSON Parse Failure",
    "Diagnosis Failure",
    "Retrieval Failure",
    "Evidence Line Failure",
    "Diff Format Failure",
    "Patch Apply Failure",
    "Unauthorized Modification",
    "Code Logic Failure",
    "Test Failure",
    "Harness Failure",
]


class RepairTask(BaseModel):
    """A self-contained repair task used by the evaluation harness."""

    task_id: str = Field(min_length=1)
    task_version: str = Field(default="1", min_length=1)
    task_kind: Literal["repairable", "unrepairable", "unauthorized_prompt"] = "repairable"
    repository_path: Path
    issue: str = Field(min_length=1)
    error_log: str | None = None
    allowed_files: list[str] = Field(min_length=1)
    expected_files: list[str] = Field(min_length=1)
    forbidden_files: list[str] = Field(default_factory=list)
    setup_commands: list[list[str]] = Field(default_factory=list)
    test_commands: list[list[str]] = Field(min_length=1)
    expected_root_cause_terms: list[str] = Field(min_length=1)
    max_retries: int = Field(default=1, ge=0)


class EvaluationRunResult(BaseModel):
    """Deterministic measurements and safety outcomes for one evaluation run."""

    task_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    model: str = Field(min_length=1)
    diagnosis_valid: bool
    evidence_valid: bool
    correct_file: bool
    diff_valid: bool
    patch_applied: bool
    tests_passed: bool
    unauthorized_files: list[str] = Field(default_factory=list)
    false_pass: bool = False
    retries: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    latency_ms: float = Field(default=0.0, ge=0)
    token_usage: int | None = Field(default=None, ge=0)
    cost: float | None = Field(default=None, ge=0)
    failure_stage: str | None = None
    failure_category: FailureCategory | None = None
    trajectory_path: str | None = None
    error: str | None = None
