"""Router functions for the CodeMedic LangGraph workflow.

All routers are PURE functions — they do NOT call LLMs, write files,
or make network requests.
"""

from __future__ import annotations

from typing import Literal

from codemedic.config import settings
from codemedic.graph.state import RepairState

# Route labels — typed as Literal so mypy can verify return types
SUFFICIENT: Literal["sufficient"] = "sufficient"
INSUFFICIENT: Literal["insufficient"] = "insufficient"
UNCERTAIN: Literal["uncertain"] = "uncertain"

RouteLabel = Literal["sufficient", "insufficient", "uncertain"]


def evidence_gate_router(state: RepairState) -> RouteLabel:
    """Route based on evidence sufficiency and validation.

    Rules (in order):
      1. No diagnosis → INSUFFICIENT (need investigation)
      2. Evidence validation failed → UNCERTAIN (manual review)
      3. Confidence < 0.4 and rounds left → INSUFFICIENT (retry)
      4. No evidence items → INSUFFICIENT (need more data)
      5. Missing information reported → UNCERTAIN (manual review)
      6. Low confidence with rounds left → INSUFFICIENT (retry)
      7. Low confidence no rounds → UNCERTAIN
      8. All checks pass → SUFFICIENT (proceed to fixer)

    All values are taken directly from state — no LLM calls.
    """
    diagnosis = state.get("diagnosis")
    if diagnosis is None:
        return INSUFFICIENT

    # Evidence validation failed → route to manual review
    validation = state.get("evidence_validation")
    if validation and not validation.get("valid", True):
        return UNCERTAIN

    # Very low confidence — insufficient evidence
    if diagnosis.confidence < 0.4:
        if state.get("retrieval_round", 0) < 2:
            return INSUFFICIENT
        return UNCERTAIN

    # No evidence at all
    if not diagnosis.evidence:
        if state.get("retrieval_round", 0) < 2:
            return INSUFFICIENT
        return UNCERTAIN

    # Has missing information → flag for review
    if diagnosis.missing_information:
        return UNCERTAIN

    # Low confidence with rounds left → try again
    if diagnosis.confidence < 0.6 and state.get("retrieval_round", 0) < 2:
        return INSUFFICIENT

    # Good enough — proceed
    if diagnosis.confidence < 0.6:
        return UNCERTAIN

    return SUFFICIENT


REJECTED: Literal["rejected"] = "rejected"
RETRY: Literal["retry"] = "retry"

HumanReviewRoute = Literal["approved", "rejected", "retry"]

DiagnosisReviewRoute = Literal["accept_diagnosis", "reject"]
PatchValidationRoute = Literal["valid", "invalid_retry", "invalid_final"]

PatchApplyRoute = Literal["success", "failed"]


def patch_apply_router(state: RepairState) -> PatchApplyRoute:
    """Route based on patch apply result.

    Rules:
      - PatchApplyResult exists and success → run tests
      - Otherwise → final report
    """
    apply_result = state.get("patch_apply_result")
    if apply_result and apply_result.get("success"):
        return "success"
    return "failed"


def diagnosis_review_router(state: RepairState) -> DiagnosisReviewRoute:
    """Route based on diagnosis review decision.

    Rules:
      - 'accept_diagnosis' → proceed to fixer
      - 'reject' → final report
      - None / unknown → reject (safe default)
    """
    decision = state.get("human_decision")
    if decision == "accept_diagnosis":
        return "accept_diagnosis"
    return "reject"


def patch_review_router(state: RepairState) -> HumanReviewRoute:
    """Route based on patch review decision.

    Rules:
      - 'approved' → proceed to sandbox
      - 'rejected' → final report
      - 'retry' → go back to fixer
      - None / unknown → rejected (safe default)
    """
    decision = state.get("human_decision")
    if decision == "approved":
        return "approved"
    if decision == "retry":
        return "retry"
    return "rejected"


def patch_validation_router(state: RepairState) -> PatchValidationRoute:
    """Route based on diff validation result.

    Rules:
      - No diff_validation → invalid_final (fail closed)
      - Diff valid → patch_review
      - Invalid with retry left → fixer_agent
      - Invalid no retry → final_report (fail closed)

    All values from state — no LLM calls.
    """
    diff_val = state.get("diff_validation")

    # No validation result must fail closed
    if not diff_val:
        return "invalid_final"

    if diff_val.get("valid") is True:
        return "valid"

    retry_count = state.get("retry_count", 0)
    if retry_count < settings.max_fixer_retries:
        return "invalid_retry"

    return "invalid_final"


def verify_router(state: RepairState) -> RouteLabel:
    """Route based on verification results.

    Rules:
      - Patch apply failed → UNCERTAIN (manual review)
      - No test results → UNCERTAIN
      - Any test timed out → UNCERTAIN
      - All tests passed (exit code 0) → SUFFICIENT
      - Some tests failed and retry available → INSUFFICIENT (retry fixer)
      - Some tests failed no retries → UNCERTAIN

    All values are taken directly from state — no LLM calls.
    """
    # Check if patch apply failed (errors from sandbox)
    errors = state.get("errors", [])
    if any("Patch apply failed" in e for e in errors):
        return UNCERTAIN

    test_results = state.get("test_results", [])
    if not test_results:
        return UNCERTAIN

    # Check for timeouts
    if any(r.get("timed_out", False) for r in test_results):
        return UNCERTAIN

    # All passed
    if all(r.get("returncode", 1) == 0 for r in test_results):
        return SUFFICIENT

    # Some failed — check retry
    retry_count = state.get("retry_count", 0)
    if retry_count < settings.max_fixer_retries:
        return INSUFFICIENT

    return UNCERTAIN
