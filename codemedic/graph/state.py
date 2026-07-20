"""RepairState — the shared state schema for the CodeMedic LangGraph workflow."""

from __future__ import annotations

from typing import Any, Literal, TypedDict


class RepairState(TypedDict):
    """Shared state for the repair workflow."""

    # ── Intake ──────────────────────────────────────────────────────
    task_id: str
    run_id: str
    thread_id: str
    workflow_status: str | None
    issue: str
    error_log: str | None
    repository_path: str

    # ── Retrieval ───────────────────────────────────────────────────
    retrieved_context: list[dict[str, Any]]
    """Chunks retrieved from the repository (file path, content, metadata)."""

    # ── Investigation ───────────────────────────────────────────────
    diagnosis: dict[str, Any] | None
    """Serialized DiagnosisResult produced by the Investigator agent."""
    evidence_validation: dict[str, Any] | None
    """Validation result from validate_evidence() (dict for backward compat)."""

    # ── Fixer ────────────────────────────────────────────────────────
    patch: dict[str, Any] | None
    """Patch proposal dict from PatchProposal.model_dump()."""
    diff_validation: dict[str, Any] | None
    """Validation result from validate_diff() (dict for backward compat)."""
    previous_patch: dict[str, Any] | None
    """Previous patch attempt (for retry feedback)."""
    failure_feedback: str | None
    """Test failure feedback to pass to Fixer on retry."""
    human_feedback: str | None
    """Human-provided feedback for retry."""

    # ── Sandbox ──────────────────────────────────────────────────────
    sandbox_path: str | None
    """Path to the sandbox temp copy."""
    sandbox_cleaned: bool
    """Whether no temporary sandbox remains after the last sandbox stage."""
    execution_backend: Literal["temporary", "docker"]
    """Test execution backend selected for this workflow run."""
    patch_apply_result: dict[str, Any] | None
    """Result from apply_patch_node (dict for backward compat)."""
    test_results: list[dict[str, Any]]
    """Test execution results."""
    verifier_summary: str | None
    """Summary from Verifier agent."""

    # ── Limits ──────────────────────────────────────────────────────
    allowed_files: list[str]
    """Files the Fixer is allowed to modify."""
    approved_files: list[str]
    """Files explicitly authorized by a human during Diagnosis Review."""
    investigation_steps: int
    """Number of investigation steps taken so far."""
    retrieval_round: int
    """Number of retrieval rounds so far."""
    retry_count: int
    """Number of fix→verify retries so far."""
    fix_attempt_count: int
    """Total number of fixer invocations (including first attempt)."""

    # ── Human review ────────────────────────────────────────────────
    human_decision: str | None
    """Decision from human review: 'approved', 'rejected', 'retry'."""
    review_reason: str | None
    """Reason provided with the human decision."""
    review_policy: str
    """Whether human review is required: 'manual' or 'controlled_auto'."""

    # ── Final ───────────────────────────────────────────────────────
    final_status: Literal["通过", "人工复核", "拒绝"] | None
    """Final outcome of the workflow."""
    final_report: dict[str, Any] | None
    """Final structured report."""
    errors: list[str]
    """Non-fatal errors encountered during the workflow."""


def create_initial_state(
    issue: str,
    repository_path: str,
    error_log: str | None = None,
    *,
    task_id: str | None = None,
    run_id: str | None = None,
    thread_id: str | None = None,
    execution_backend: Literal["temporary", "docker"] = "temporary",
    review_policy: str = "manual",
) -> RepairState:
    """Create a fresh RepairState with defaults."""
    import uuid

    return RepairState(
        task_id=task_id or f"task_{uuid.uuid4().hex[:12]}",
        run_id=run_id or f"run_{uuid.uuid4().hex[:12]}",
        thread_id=thread_id or "",
        workflow_status="running",
        issue=issue,
        error_log=error_log,
        repository_path=repository_path,
        retrieved_context=[],
        diagnosis=None,
        evidence_validation=None,
        patch=None,
        diff_validation=None,
        previous_patch=None,
        failure_feedback=None,
        human_feedback=None,
        sandbox_path=None,
        sandbox_cleaned=False,
        execution_backend=execution_backend,
        patch_apply_result=None,
        test_results=[],
        verifier_summary=None,
        allowed_files=[],
        approved_files=[],
        investigation_steps=0,
        retrieval_round=0,
        retry_count=0,
        fix_attempt_count=0,
        human_decision=None,
        review_reason=None,
        review_policy=review_policy,
        final_status=None,
        final_report=None,
        errors=[],
    )
