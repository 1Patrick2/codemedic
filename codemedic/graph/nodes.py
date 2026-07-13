"""Graph nodes for the CodeMedic LangGraph workflow.

Each node is a function that receives the full RepairState and returns
only the fields it is responsible for updating.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from codemedic.agents.investigator import run_investigator
from codemedic.config import settings
from codemedic.graph.state import RepairState
from codemedic.tools.repository import list_repo_tree, read_file
from codemedic.tracing.local_trace import LocalTracer


def intake(state: RepairState) -> dict[str, Any]:
    """Validate and pre-process inputs.

    Creates a task_id, resolves the repository path, and initialises
    the counters.

    Returns:
        Initialised state fields for task_id, investigation_steps,
        retrieval_round, retry_count, allowed_files, and errors.
    """
    repo = Path(state["repository_path"]).resolve()
    errors: list[str] = []
    if not repo.is_dir():
        errors.append(f"Repository path not found: {state['repository_path']}")

    return {
        "task_id": state.get("task_id") or f"task_{uuid.uuid4().hex[:12]}",
        "investigation_steps": 0,
        "retrieval_round": 0,
        "retry_count": 0,
        "allowed_files": [],
        "errors": errors,
    }


def hybrid_retrieve(state: RepairState) -> dict[str, Any]:
    """Retrieve context from the repository.

    Stage 2: simple file tree + reading key files.
      - Lists the repo tree
      - Collects Python file paths up to a limit

    Returns:
        Updated retrieved_context and incremented retrieval_round.
    """
    repo_path = state["repository_path"]
    context: list[dict[str, Any]] = list(state.get("retrieved_context", []))

    # Get the file tree
    tree = list_repo_tree(repo_path, max_depth=4)

    # Collect Python file paths
    py_files = []
    base = Path(repo_path).resolve()
    for fpath in sorted(base.rglob("*.py")):
        if any(part.startswith(".") or part == "__pycache__" for part in fpath.parts):
            continue
        if fpath.stat().st_size > settings.max_file_size_bytes:
            continue
        rel = fpath.relative_to(base)
        py_files.append(str(rel))
        if len(py_files) >= 30:
            break

    context.append({
        "type": "tree",
        "content": tree,
        "metadata": {"source": "list_repo_tree"},
    })

    for pyf in py_files:
        content = read_file(repo_path, pyf)
        context.append({
            "type": "file",
            "path": pyf,
            "content": content,
            "metadata": {"source": "read_file"},
        })

    return {
        "retrieved_context": context,
        "retrieval_round": state.get("retrieval_round", 0) + 1,
    }


def investigator_node(state: RepairState) -> dict[str, Any]:
    """Run the Investigator agent to diagnose the issue.

    Uses the existing run_investigator function.

    Returns:
        Updated diagnosis and incremented investigation_steps.
    """
    tracer = LocalTracer(task_id=state["task_id"])

    diagnosis = run_investigator(
        issue=state["issue"],
        repository_path=state["repository_path"],
        error_log=state.get("error_log"),
        trace=tracer,
    )

    return {
        "diagnosis": diagnosis,
        "investigation_steps": state.get("investigation_steps", 0) + 1,
    }


def fixer_node(state: RepairState) -> dict[str, Any]:
    """Run the Fixer agent to generate a patch proposal.

    Reads the diagnosis and retrieved context, then calls the Fixer
    agent to generate a Unified Diff patch.

    Returns:
        Patch proposal and incremented retry_count (if this is a retry).
    """
    from codemedic.agents.fixer import run_fixer

    diagnosis = state.get("diagnosis")
    if diagnosis is None:
        return {
            "patch": None,
            "errors": state.get("errors", []) + ["No diagnosis available for Fixer"],
        }

    # Build context string from retrieved context
    context_parts = []
    for ctx in state.get("retrieved_context", []):
        if ctx.get("type") == "file":
            ctx_path = ctx.get("path", "unknown")
            ctx_content = ctx.get("content", "")[:3000]
            context_parts.append(f"\n--- {ctx_path} ---\n{ctx_content}")

    context_str = "\n".join(context_parts)

    try:
        patch = run_fixer(
            issue=state["issue"],
            diagnosis=diagnosis,
            context=context_str,
        )

        return {
            "patch": patch.model_dump(),
            "retry_count": state.get("retry_count", 0) + 1,
        }
    except Exception as exc:
        return {
            "patch": None,
            "errors": state.get("errors", []) + [f"Fixer agent error: {exc}"],
        }


def human_review_node(state: RepairState) -> dict[str, Any]:
    """Human-in-the-loop review node using LangGraph interrupt.

    Calls interrupt() to pause the graph and surface the patch proposal
    for human review. When resumed, reads the human decision from
    Command.resume.

    Returns:
        human_decision and review_reason from the interrupt response.
    """
    patch = state.get("patch")
    diag = state.get("diagnosis")

    interrupt_value = {
        "message": "Please review the proposed patch.",
        "issue": state["issue"],
        "root_cause": diag.root_cause if diag else "N/A",
        "patch": patch,
        "options": ["approved", "rejected", "retry"],
    }

    # Pause execution and wait for human input
    from langgraph.types import interrupt

    human_input = interrupt(interrupt_value)

    # Parse the human decision
    if isinstance(human_input, dict):
        decision = human_input.get("decision", "rejected")
        reason = human_input.get("reason", "")
    elif isinstance(human_input, str):
        decision = human_input
        reason = ""
    else:
        decision = "rejected"
        reason = "Invalid input format"

    return {
        "human_decision": decision,
        "review_reason": reason,
    }


def final_report_node(state: RepairState) -> dict[str, Any]:
    """Build the final report from the completed workflow.

    Returns:
        final_report dict and final_status.
    """
    diag = state.get("diagnosis")
    patch = state.get("patch")
    test_results = state.get("test_results", [])

    report: dict[str, Any] = {
        "task_id": state["task_id"],
        "issue": state["issue"],
        "root_cause": diag.root_cause if diag else "No diagnosis produced",
        "confidence": diag.confidence if diag else 0.0,
        "suspected_files": diag.suspected_files if diag else [],
        "evidence_count": len(diag.evidence) if diag else 0,
        "investigation_steps": state.get("investigation_steps", 0),
        "retrieval_rounds": state.get("retrieval_round", 0),
        "patch_applied": patch is not None,
        "test_count": len(test_results),
    }

    final_status: str | None = "通过"

    # Check if tests failed
    any_test_failed = any(
        r.get("returncode", 0) != 0 for r in test_results
    )
    if state.get("errors"):
        final_status = "人工复核"
    elif test_results and any_test_failed:
        final_status = "人工复核"

    return {
        "final_report": report,
        "final_status": final_status,
        "verifier_summary": state.get("verifier_summary"),
        "sandbox_path": state.get("sandbox_path"),
    }


def apply_patch_node(state: RepairState) -> dict[str, Any]:
    """Apply the patch in a sandbox (temp copy of the repo).

    Creates a temporary copy, applies the Unified Diff, and records
    the sandbox path for subsequent test execution.

    Returns:
        sandbox_path and any errors.
    """
    from codemedic.tools.sandbox import apply_patch, create_temp_copy, verify_patch_boundaries

    patch = state.get("patch")
    if patch is None:
        return {"errors": state.get("errors", []) + ["No patch to apply"]}

    unified_diff = patch.get("unified_diff", "") if isinstance(patch, dict) else ""
    if not unified_diff:
        return {"errors": state.get("errors", []) + ["Patch has no diff content"]}

    # Verify patch boundaries
    allowed = state.get("allowed_files")
    boundary_check = verify_patch_boundaries(unified_diff, allowed)
    if not boundary_check["valid"]:
        return {
            "errors": state.get("errors", []) + [
                f"Patch violates boundaries: {boundary_check['violations']}"
            ],
        }

    try:
        sandbox_path = create_temp_copy(state["repository_path"])
        result = apply_patch(sandbox_path, unified_diff)

        if not result["success"]:
            from codemedic.tools.sandbox import cleanup_sandbox
            cleanup_sandbox(sandbox_path)
            return {
                "errors": state.get("errors", []) + [
                    f"Patch apply failed: {result['stderr'][:200]}"
                ],
            }

        return {
            "sandbox_path": sandbox_path,
        }
    except Exception as exc:
        return {
            "errors": state.get("errors", []) + [f"Sandbox error: {exc}"],
        }


def run_tests_node(state: RepairState) -> dict[str, Any]:
    """Run test commands in the sandbox.

    Returns:
        test_results with command outputs.
    """
    from codemedic.tools.test_runner import run_tests

    sandbox_path = state.get("sandbox_path")
    if not sandbox_path:
        return {"errors": state.get("errors", []) + ["No sandbox path for tests"]}

    try:
        results = run_tests(str(sandbox_path))
        return {"test_results": results}
    except Exception as exc:
        return {
            "test_results": [],
            "errors": state.get("errors", []) + [f"Test execution error: {exc}"],
        }


def verifier_node(state: RepairState) -> dict[str, Any]:
    """Run the Verifier agent to summarize test results.

    Returns:
        verifier_summary text.
    """
    from codemedic.agents.verifier import run_verifier

    issue = state["issue"]
    patch = state.get("patch")
    test_results = state.get("test_results", [])

    patch_summary = ""
    if patch:
        if isinstance(patch, dict):
            patch_summary = patch.get("rationale", str(patch)[:200])
        else:
            patch_summary = str(patch)[:200]

    try:
        summary = run_verifier(issue, patch_summary, test_results)
        return {"verifier_summary": summary}
    except Exception as exc:
        return {"verifier_summary": f"Verifier error: {exc}"}

