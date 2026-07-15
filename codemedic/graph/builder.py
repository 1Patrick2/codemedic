"""Build and compile the CodeMedic LangGraph workflow.

B3+B4: Split diagnosis_review and patch_review, add patch validation gate.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, StateGraph

from codemedic.graph.nodes import (
    apply_patch_node,
    diagnosis_review_node,
    final_report_node,
    fixer_node,
    hybrid_retrieve,
    intake,
    investigator_node,
    mark_diagnosis_review_waiting,
    mark_patch_review_waiting,
    patch_review_node,
    patch_validation_node,
    prepare_fix_retry,
    run_tests_node,
    verifier_node,
)
from codemedic.graph.routers import (
    diagnosis_review_router,
    evidence_gate_router,
    intake_router,
    patch_apply_router,
    patch_review_router,
    patch_validation_router,
    verify_router,
)
from codemedic.graph.state import RepairState
from codemedic.schemas.results import WorkflowRunResult


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
    graph.add_node("mark_diagnosis_review_waiting", mark_diagnosis_review_waiting)
    graph.add_node("mark_patch_review_waiting", mark_patch_review_waiting)
    graph.add_node("hybrid_retrieve", hybrid_retrieve)
    graph.add_node("investigator_agent", investigator_node)
    graph.add_node("evidence_gate", lambda s: {})  # router-only node
    graph.add_node("diagnosis_review", diagnosis_review_node)
    graph.add_node("fixer_agent", fixer_node)
    graph.add_node("patch_validation", patch_validation_node)
    graph.add_node("patch_review", patch_review_node)
    graph.add_node("prepare_fix_retry", prepare_fix_retry)
    graph.add_node("apply_patch", apply_patch_node)
    graph.add_node("run_tests", run_tests_node)
    graph.add_node("verifier_agent", verifier_node)
    graph.add_node("final_report", final_report_node)

    # ── Edges ───────────────────────────────────────────────────────
    graph.set_entry_point("intake")

    graph.add_conditional_edges(
        "intake",
        intake_router,
        {"valid": "hybrid_retrieve", "invalid": "final_report"},
    )
    graph.add_edge("hybrid_retrieve", "investigator_agent")

    # Evidence gate → diagnosis_review or fixer or retry
    graph.add_conditional_edges(
        "investigator_agent",
        evidence_gate_router,
        {
            "sufficient": "fixer_agent",
            "insufficient": "hybrid_retrieve",
            "uncertain": "mark_diagnosis_review_waiting",
        },
    )

    graph.add_edge("mark_diagnosis_review_waiting", "diagnosis_review")

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

    # Patch validation gate → valid (review), invalid+retry (prepare), invalid+final
    graph.add_conditional_edges(
        "patch_validation",
        patch_validation_router,
        {
            "valid": "mark_patch_review_waiting",
            "invalid_retry": "prepare_fix_retry",
            "invalid_final": "final_report",
        },
    )

    graph.add_edge("mark_patch_review_waiting", "patch_review")

    # Prepare fix retry → fixer
    graph.add_edge("prepare_fix_retry", "fixer_agent")

    # Patch review → approve (apply), reject (end), retry (prepare)
    graph.add_conditional_edges(
        "patch_review",
        patch_review_router,
        {
            "approved": "apply_patch",
            "rejected": "final_report",
            "retry": "prepare_fix_retry",
        },
    )

    # Patch apply → success (tests), failed (end)
    graph.add_conditional_edges(
        "apply_patch",
        patch_apply_router,
        {
            "success": "run_tests",
            "failed": "final_report",
        },
    )
    graph.add_edge("run_tests", "verifier_agent")

    # Verify router
    graph.add_conditional_edges(
        "verifier_agent",
        verify_router,
        {
            "sufficient": "final_report",
            "insufficient": "prepare_fix_retry",
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
) -> "WorkflowRunResult":
    """Run the full workflow and return a structured result.

    NOTE: Will pause at patch_review or diagnosis_review due to interrupt.
    Use resume_workflow() to continue.

    Args:
        issue: Issue description.
        repository_path: Path to the repository root.
        error_log: Optional error log content.
        thread_id: Optional thread ID for resumption.

    Returns:
        WorkflowRunResult with thread_id, status, and state snapshot.
    """
    from codemedic.graph.runtime import WorkflowRuntime

    # Generate thread_id BEFORE creating state or config — ensures consistency
    with WorkflowRuntime() as runtime:
        return runtime.run(
            issue,
            repository_path,
            error_log,
            thread_id=thread_id,
        )


def resume_workflow(
    decision: str,
    reason: str = "",
    *,
    thread_id: str,
) -> "WorkflowRunResult":
    """Resume a paused workflow with a human review decision.

    Args:
        decision: 'approved', 'rejected', 'retry', or 'accept_diagnosis'.
        reason: Optional reason for the decision.
        thread_id: Thread ID of the paused workflow.

    Returns:
        WorkflowRunResult with thread_id, status, and state snapshot.
    """
    from codemedic.graph.runtime import WorkflowRuntime

    with WorkflowRuntime() as runtime:
        return runtime.resume(decision, thread_id=thread_id, reason=reason)


def _make_workflow_result(state: dict, tid: str) -> "WorkflowRunResult":
    """Build a WorkflowRunResult from workflow state dict and thread_id."""
    interrupted = "__interrupt__" in state

    # Determine workflow status from state
    if interrupted:
        interrupt_payload = state["__interrupt__"]
        if interrupt_payload and len(interrupt_payload) > 0:
            review_type = interrupt_payload[0].value.get("review_type", "unknown")
            if review_type == "diagnosis":
                workflow_status = "waiting_diagnosis_review"
            elif review_type == "patch":
                workflow_status = "waiting_patch_review"
            else:
                workflow_status = "failed"
        else:
            workflow_status = "failed"
    else:
        # Check if there was a fatal error
        final_status = state.get("final_status")
        if state.get("workflow_status") == "failed":
            workflow_status = "failed"
        elif final_status is not None:
            workflow_status = "completed"
        else:
            workflow_status = "failed"

    snapshot = dict(state)
    snapshot["thread_id"] = tid
    snapshot["workflow_status"] = workflow_status

    return WorkflowRunResult(
        task_id=state.get("task_id", "unknown"),
        thread_id=tid,
        workflow_status=workflow_status,  # type: ignore[arg-type]
        interrupted=interrupted,
        state=snapshot,
    )
