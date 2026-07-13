"""RepairState — the shared state schema for the CodeMedic LangGraph workflow."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from codemedic.schemas.diagnosis import DiagnosisResult


class RepairState(TypedDict):
    """Shared state for the repair workflow."""

    # ── Intake ──────────────────────────────────────────────────────
    task_id: str
    issue: str
    error_log: str | None
    repository_path: str

    # ── Retrieval ───────────────────────────────────────────────────
    retrieved_context: list[dict[str, Any]]
    """Chunks retrieved from the repository (file path, content, metadata)."""

    # ── Investigation ───────────────────────────────────────────────
    diagnosis: DiagnosisResult | None
    """Structured diagnosis produced by the Investigator agent."""
    evidence_validation: dict[str, Any] | None
    """Validation result from validate_evidence()."""

    # ── Fixer (stub in Stage 2) ─────────────────────────────────────
    patch: dict[str, Any] | None
    """Patch proposal (placeholder for Stage 3)."""
    test_results: list[dict[str, Any]]
    """Test execution results (placeholder for Stage 4)."""

    # ── Limits ──────────────────────────────────────────────────────
    allowed_files: list[str]
    """Files the Fixer is allowed to modify."""
    investigation_steps: int
    """Number of investigation steps taken so far."""
    retrieval_round: int
    """Number of retrieval rounds so far."""
    retry_count: int
    """Number of fix→verify retries so far."""

    # ── Human review ────────────────────────────────────────────────
    human_decision: str | None
    """Decision from human review: 'approved', 'rejected', 'retry'."""
    review_reason: str | None
    """Reason provided with the human decision."""

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
) -> RepairState:
    """Create a fresh RepairState with defaults."""
    import uuid

    return RepairState(
        task_id=task_id or f"task_{uuid.uuid4().hex[:12]}",
        issue=issue,
        error_log=error_log,
        repository_path=repository_path,
        retrieved_context=[],
        diagnosis=None,
        evidence_validation=None,
        patch=None,
        test_results=[],
        allowed_files=[],
        investigation_steps=0,
        retrieval_round=0,
        retry_count=0,
        human_decision=None,
        review_reason=None,
        final_status=None,
        final_report=None,
        errors=[],
    )
