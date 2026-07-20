"""Build and compile the CodeMedic LangGraph workflow.

B3+B4: Split diagnosis_review and patch_review, add patch validation gate.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from functools import wraps
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.errors import GraphInterrupt
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


def _instrument_node(
    name: str,
    node: Any,
) -> Any:
    """Record node input/output when a public runtime has an active recorder."""
    from codemedic.tracing.recorder import get_recorder

    @wraps(node)
    def wrapped(state: RepairState) -> dict[str, Any]:
        recorder = get_recorder(state.get("run_id"), state.get("thread_id"))
        if recorder is None:
            return node(state)

        started = time.perf_counter()
        recorder.record(
            node=name,
            event_type="node_started",
            summary=f"{name} started",
            input_data=dict(state),
        )
        try:
            output = node(state)
        except GraphInterrupt as exc:
            recorder.record(
                node=name,
                event_type="interrupt",
                summary=f"{name} interrupted",
                output_data={"error": str(exc)},
                duration_ms=(time.perf_counter() - started) * 1000,
            )
            raise
        except Exception as exc:
            recorder.record(
                node=name,
                event_type="node_completed",
                summary=f"{name} failed",
                output_data={"error": str(exc)},
                duration_ms=(time.perf_counter() - started) * 1000,
            )
            raise

        recorder.record(
            node=name,
            event_type="node_completed",
            summary=f"{name} completed",
            output_data=output,
            duration_ms=(time.perf_counter() - started) * 1000,
        )
        return output

    return wrapped


def build_workflow(retrieval_node: Any | None = None) -> StateGraph:
    """Build and return the repair workflow graph."""
    graph = StateGraph(RepairState)

    # ── Register nodes ──────────────────────────────────────────────
    nodes: dict[str, Any] = {
        "intake": intake,
        "mark_diagnosis_review_waiting": mark_diagnosis_review_waiting,
        "mark_patch_review_waiting": mark_patch_review_waiting,
        "hybrid_retrieve": retrieval_node or hybrid_retrieve,
        "investigator_agent": investigator_node,
        "evidence_gate": lambda _state: {},
        "diagnosis_review": diagnosis_review_node,
        "fixer_agent": fixer_node,
        "patch_validation": patch_validation_node,
        "patch_review": patch_review_node,
        "prepare_fix_retry": prepare_fix_retry,
        "apply_patch": apply_patch_node,
        "run_tests": run_tests_node,
        "verifier_agent": verifier_node,
        "final_report": final_report_node,
    }
    for name, node in nodes.items():
        graph.add_node(name, _instrument_node(name, node))

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
            "manual_review": "mark_diagnosis_review_waiting",
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


def compile_workflow(*, checkpointer=None, retrieval_node=None):
    """Build and compile the workflow graph."""
    graph = build_workflow(retrieval_node=retrieval_node)
    cptr = checkpointer or MemorySaver()
    return graph.compile(checkpointer=cptr)


def run_workflow(
    issue: str,
    repository_path: str,
    error_log: str | None = None,
    *,
    thread_id: str | None = None,
    review_policy: str | None = None,
) -> "WorkflowRunResult":
    """Run the full workflow and return a structured result.

    NOTE: Will pause at patch_review or diagnosis_review due to interrupt.
    Use resume_workflow() to continue.

    Args:
        issue: Issue description.
        repository_path: Path to the repository root.
        error_log: Optional error log content.
        thread_id: Optional thread ID for resumption.
        review_policy: 'manual' (default) or 'controlled_auto'.

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
            review_policy=review_policy,
        )


def resume_workflow(
    decision: str,
    reason: str = "",
    *,
    thread_id: str,
    approved_files: list[str] | None = None,
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
        return runtime.resume(
            decision,
            thread_id=thread_id,
            reason=reason,
            approved_files=approved_files,
        )


def get_run_state(thread_id: str) -> WorkflowRunResult:
    """Return the latest persisted workflow state for a thread."""
    from codemedic.graph.runtime import WorkflowRuntime

    with WorkflowRuntime() as runtime:
        return runtime.get_state(thread_id)


def list_runs(root_dir: str | None = None) -> list[str]:
    """List locally persisted trajectory run IDs."""
    from codemedic.tracing.reader import TrajectoryReader

    return TrajectoryReader(root_dir).list_runs()


def get_trajectory(run_id: str, root_dir: str | None = None) -> dict[str, Any]:
    """Read one locally persisted trajectory without invoking the workflow."""
    from codemedic.tracing.reader import TrajectoryReader

    return TrajectoryReader(root_dir).read_trajectory(run_id)


def _make_workflow_result(
    state: dict,
    tid: str,
    *,
    workflow_status: str | None = None,
) -> "WorkflowRunResult":
    """Build a WorkflowRunResult from workflow state dict and thread_id."""
    interrupted = workflow_status in {
        "waiting_diagnosis_review",
        "waiting_patch_review",
    } if workflow_status is not None else "__interrupt__" in state

    # Determine workflow status from state
    if workflow_status is not None:
        resolved_status = workflow_status
    elif interrupted:
        interrupt_payload = state["__interrupt__"]
        if interrupt_payload and len(interrupt_payload) > 0:
            review_type = _interrupt_review_type(interrupt_payload[0])
            if review_type == "diagnosis":
                resolved_status = "waiting_diagnosis_review"
            elif review_type == "patch":
                resolved_status = "waiting_patch_review"
            else:
                resolved_status = "failed"
        else:
            resolved_status = "failed"
    else:
        # Check if there was a fatal error
        final_status = state.get("final_status")
        if state.get("workflow_status") == "failed":
            resolved_status = "failed"
        elif final_status is not None:
            resolved_status = "completed"
        else:
            resolved_status = "failed"

    snapshot = dict(state)
    snapshot["thread_id"] = tid
    snapshot["workflow_status"] = resolved_status

    return WorkflowRunResult(
        task_id=state.get("task_id", "unknown"),
        thread_id=tid,
        workflow_status=resolved_status,  # type: ignore[arg-type]
        interrupted=interrupted,
        state=snapshot,
    )


def _interrupt_review_type(interrupt: Any) -> str:
    value = getattr(interrupt, "value", interrupt)
    if isinstance(value, Mapping):
        review_type = value.get("review_type")
        return review_type if isinstance(review_type, str) else "unknown"
    return "unknown"


def _snapshot_interrupts(snapshot: Any) -> tuple[Any, ...]:
    interrupts: list[Any] = []
    for task in getattr(snapshot, "tasks", ()) or ():
        interrupts.extend(getattr(task, "interrupts", ()) or ())
    if not interrupts:
        interrupts.extend(getattr(snapshot, "interrupts", ()) or ())
    return tuple(interrupts)


def _make_workflow_result_from_snapshot(snapshot: Any, tid: str) -> "WorkflowRunResult":
    """Reconstruct a public result from a persisted LangGraph snapshot."""
    values = getattr(snapshot, "values", {})
    state = dict(values) if isinstance(values, Mapping) else {}
    interrupts = _snapshot_interrupts(snapshot)
    if interrupts:
        state["__interrupt__"] = interrupts

    review_type = next(
        (
            candidate
            for candidate in (_interrupt_review_type(item) for item in interrupts)
            if candidate in {"diagnosis", "patch"}
        ),
        None,
    )
    next_nodes = set(getattr(snapshot, "next", ()) or ())
    if review_type == "diagnosis" or {
        "diagnosis_review",
        "mark_diagnosis_review_waiting",
    } & next_nodes:
        status = "waiting_diagnosis_review"
    elif review_type == "patch" or {
        "patch_review",
        "mark_patch_review_waiting",
    } & next_nodes:
        status = "waiting_patch_review"
    else:
        status = None

    return _make_workflow_result(state, tid, workflow_status=status)
