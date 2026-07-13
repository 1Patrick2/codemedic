"""Router functions for the CodeMedic LangGraph workflow.

All routers are PURE functions — they do NOT call LLMs, write files,
or make network requests.
"""

from __future__ import annotations

from typing import Literal

from codemedic.graph.state import RepairState

# Route labels — typed as Literal so mypy can verify return types
SUFFICIENT: Literal["sufficient"] = "sufficient"
INSUFFICIENT: Literal["insufficient"] = "insufficient"
UNCERTAIN: Literal["uncertain"] = "uncertain"

RouteLabel = Literal["sufficient", "insufficient", "uncertain"]


def evidence_gate_router(state: RepairState) -> RouteLabel:
    """Route based on evidence sufficiency.

    Rules (in order):
      1. No diagnosis → INSUFFICIENT (need investigation)
      2. Confidence < 0.6 and rounds left → INSUFFICIENT (retry)
      3. No evidence items → INSUFFICIENT (need more data)
      4. Missing information reported → UNCERTAIN (manual review)
      5. All checks pass → SUFFICIENT (proceed to fixer)

    All values are taken directly from state — no LLM calls.
    """
    diagnosis = state.get("diagnosis")
    if diagnosis is None:
        return INSUFFICIENT

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


def human_review_router(state: RepairState) -> HumanReviewRoute:
    """Route based on human review decision.

    Rules:
      - 'approved' → proceed to next step (sandbox in Stage 4, final now)
      - 'rejected' → end the workflow with final report
      - 'retry' → go back to the fixer agent
      - None / unknown → rejected (safe default)
    """
    decision = state.get("human_decision")
    if decision == "approved":
        return "approved"
    if decision == "retry":
        return "retry"
    return "rejected"


def verify_router(state: RepairState) -> RouteLabel:
    """Route based on verification results.

    Stage 2 stub: always returns SUFFICIENT.
    Full implementation in Stage 4+.
    """
    return SUFFICIENT
