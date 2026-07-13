"""Build and compile the CodeMedic LangGraph workflow.

B3+B4: Split diagnosis_review and patch_review, add patch validation gate.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command

from codemedic.graph.nodes import (
    apply_patch_node,
    diagnosis_review_node,
    final_report_node,
    fixer_node,
    hybrid_retrieve,
    intake,
    investigator_node,
    patch_review_node,
    patch_validation_node,
    run_tests_node,
    verifier_node,
)
from codemedic.graph.routers import (
    diagnosis_review_router,
    evidence_gate_router,
    patch_review_router,
    patch_validation_router,
    verify_router,
)
from codemedic.graph.state import RepairState


def _get_checkpointer():
    """Create a SQLite checkpointer in the runtime directory."""
    runtime_dir = Path.cwd() / "runtime" / "checkpoints"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    db_path = runtime_dir / "codemedic.db"
    conn = sqlite3.connect(str(db_path), detect_types=sqlite3.PARSE_DECLTYPES)
    return SqliteSaver(conn)


def build_workflow() -> StateGraph:
    """Build and return the repair workflow graph."""
    graph = StateGraph(RepairState)

    # ── Register nodes ──────────────────────────────────────────────
    graph.add_node("intake", intake)
    graph.add_node("hybrid_retrieve", hybrid_retrieve)
    graph.add_node("investigator_agent", investigator_node)
    graph.add_node("evidence_gate", lambda s: {})  # router-only node
    graph.add_node("diagnosis_review", diagnosis_review_node)
    graph.add_node("fixer_agent", fixer_node)
    graph.add_node("patch_validation", patch_validation_node)
    graph.add_node("patch_review", patch_review_node)
    graph.add_node("apply_patch", apply_patch_node)
    graph.add_node("run_tests", run_tests_node)
    graph.add_node("verifier_agent", verifier_node)
    graph.add_node("final_report", final_report_node)

    # ── Edges ───────────────────────────────────────────────────────
    graph.set_entry_point("intake")

    graph.add_edge("intake", "hybrid_retrieve")
    graph.add_edge("hybrid_retrieve", "investigator_agent")

    # Evidence gate → diagnosis_review or fixer or retry
    graph.add_conditional_edges(
        "investigator_agent",
        evidence_gate_router,
        {
            "sufficient": "fixer_agent",
            "insufficient": "hybrid_retrieve",
            "uncertain": "diagnosis_review",
        },
    )

    # Diagnosis review → accept (fixer) or reject (end)
    graph.add_conditional_edges(
        "diagnosis_review",
        diagnosis_review_router,
        {
            "accept_diagnosis": "fixer_agent",
            "reject": "final_report",
        },
    )

    graph.add_edge("fixer_agent", "patch_validation")

    # Patch validation gate → valid (review), invalid+retry (fixer), invalid+final
    graph.add_conditional_edges(
        "patch_validation",
        patch_validation_router,
        {
            "valid": "patch_review",
            "invalid_retry": "fixer_agent",
            "invalid_final": "diagnosis_review",
        },
    )

    # Patch review → approve (apply), reject (end), retry (fixer)
    graph.add_conditional_edges(
        "patch_review",
        patch_review_router,
        {
            "approved": "apply_patch",
            "rejected": "final_report",
            "retry": "fixer_agent",
        },
    )

    # Sandbox → Tests → Verifier
    graph.add_edge("apply_patch", "run_tests")
    graph.add_edge("run_tests", "verifier_agent")

    # Verify router
    graph.add_conditional_edges(
        "verifier_agent",
        verify_router,
        {
            "sufficient": "final_report",
            "insufficient": "fixer_agent",
            "uncertain": "final_report",
        },
    )

    graph.add_edge("final_report", END)

    return graph


def compile_workflow(*, checkpointer=None):
    """Build and compile the workflow graph."""
    graph = build_workflow()
    cptr = checkpointer or _get_checkpointer()
    return graph.compile(checkpointer=cptr)


def run_workflow(
    issue: str,
    repository_path: str,
    error_log: str | None = None,
    *,
    thread_id: str | None = None,
) -> dict:
    """Convenience function to run the full workflow.

    NOTE: Will pause at patch_review or diagnosis_review due to interrupt.
    Use resume_workflow() to continue with a decision.

    Args:
        issue: Issue description.
        repository_path: Path to the repository root.
        error_log: Optional error log content.
        thread_id: Optional thread ID for resumption.

    Returns:
        The final state dict after workflow completion (or interrupt state).
    """
    import uuid

    from codemedic.graph.state import create_initial_state

    agent = compile_workflow()
    initial = create_initial_state(
        issue=issue,
        repository_path=repository_path,
        error_log=error_log,
    )
    tid = thread_id or str(uuid.uuid4())
    config = {"configurable": {"thread_id": tid}}

    result = agent.invoke(initial, config)
    return dict(result)


def resume_workflow(
    decision: str,
    reason: str = "",
    *,
    thread_id: str,
) -> dict:
    """Resume a paused workflow with a human review decision.

    Args:
        decision: 'approved', 'rejected', or 'retry'.
        reason: Optional reason for the decision.
        thread_id: Thread ID of the paused workflow.

    Returns:
        The final state dict after resumption.
    """
    agent = compile_workflow()
    config = {"configurable": {"thread_id": thread_id}}

    command: Command = Command(resume={"decision": decision, "reason": reason})
    result = agent.invoke(command, config)
    return dict(result)
