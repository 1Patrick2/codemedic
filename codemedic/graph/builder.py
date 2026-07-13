"""Build and compile the CodeMedic LangGraph workflow.

Stage 4: Complete workflow with Fixer, Human Review (interrupt),
Sandbox, Verifier, and SQLite Checkpoint.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command

from codemedic.graph.nodes import (
    apply_patch_node,
    final_report_node,
    fixer_node,
    human_review_node,
    hybrid_retrieve,
    intake,
    investigator_node,
    run_tests_node,
    verifier_node,
)
from codemedic.graph.routers import (
    evidence_gate_router,
    human_review_router,
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
    """Build and return the repair workflow graph.

    Returns:
        An uncompiled StateGraph.
    """
    graph = StateGraph(RepairState)

    # ── Register nodes ──────────────────────────────────────────────
    graph.add_node("intake", intake)
    graph.add_node("hybrid_retrieve", hybrid_retrieve)
    graph.add_node("investigator_agent", investigator_node)
    graph.add_node("evidence_gate", lambda s: {})  # router-only node
    graph.add_node("fixer_agent", fixer_node)
    graph.add_node("human_review", human_review_node)
    graph.add_node("apply_patch", apply_patch_node)
    graph.add_node("run_tests", run_tests_node)
    graph.add_node("verifier_agent", verifier_node)
    graph.add_node("final_report", final_report_node)

    # ── Edges ───────────────────────────────────────────────────────
    graph.set_entry_point("intake")

    graph.add_edge("intake", "hybrid_retrieve")
    graph.add_edge("hybrid_retrieve", "investigator_agent")

    # Evidence gate: conditional routing
    graph.add_conditional_edges(
        "investigator_agent",
        evidence_gate_router,
        {
            "sufficient": "fixer_agent",
            "insufficient": "hybrid_retrieve",
            "uncertain": "human_review",
        },
    )

    graph.add_edge("fixer_agent", "human_review")

    # Human review: conditional routing
    graph.add_conditional_edges(
        "human_review",
        human_review_router,
        {
            "approved": "apply_patch",
            "rejected": "final_report",
            "retry": "fixer_agent",
        },
    )

    # Sandbox → Tests → Verifier
    graph.add_edge("apply_patch", "run_tests")
    graph.add_edge("run_tests", "verifier_agent")

    # Verify router: conditional routing
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
    """Build and compile the workflow graph.

    Args:
        checkpointer: Optional checkpointer. If None (default), creates
            a SQLite checkpointer. Pass an InMemorySaver for testing.

    Returns:
        A CompiledStateGraph.
    """
    graph = build_workflow()
    cptr = checkpointer or _get_checkpointer()
    # NOTE: No interrupt_before — the human_review node calls
    # interrupt() dynamically inside the node. Adding interrupt_before
    # would create a redundant second interrupt.
    return graph.compile(checkpointer=cptr)


def run_workflow(
    issue: str,
    repository_path: str,
    error_log: str | None = None,
    *,
    thread_id: str | None = None,
) -> dict:
    """Convenience function to run the full workflow.

    NOTE: Will pause at human_review due to interrupt.
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
