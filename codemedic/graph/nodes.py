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
        "thread_id": state.get("thread_id", ""),
        "workflow_status": "running",
        "investigation_steps": 0,
        "retrieval_round": 0,
        "retry_count": 0,
        "allowed_files": [],
        "errors": errors,
    }


def mark_diagnosis_review_waiting(state: RepairState) -> dict[str, Any]:
    """Persist the diagnosis-review status before interrupting."""
    return {"workflow_status": "waiting_diagnosis_review"}


def mark_patch_review_waiting(state: RepairState) -> dict[str, Any]:
    """Persist the patch-review status before interrupting."""
    return {"workflow_status": "waiting_patch_review"}


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

    Uses the existing run_investigator function, then validates
    the evidence against the actual filesystem.

    Returns:
        Updated diagnosis, evidence_validation, and incremented
        investigation_steps.
    """
    from codemedic.tools.context import RepositoryContext
    from codemedic.validation.evidence import validate_evidence

    tracer = LocalTracer(task_id=state["task_id"])

    diagnosis = run_investigator(
        issue=state["issue"],
        repository_path=state["repository_path"],
        error_log=state.get("error_log"),
        trace=tracer,
    )

    # Validate evidence against the actual filesystem
    from codemedic.schemas.results import EvidenceValidationResult

    try:
        ctx = RepositoryContext(Path(state["repository_path"]))
        validation_result = EvidenceValidationResult.model_validate(
            validate_evidence(diagnosis, ctx)
        )
    except Exception as exc:
        validation_result = EvidenceValidationResult(
            valid=False,
            errors=[f"Validation error: {exc}"],
            validated_evidence=[],
        )

    # Populate allowed_files from validated evidence
    allowed_files = sorted({ev.file_path for ev in validation_result.validated_evidence})

    return {
        "diagnosis": diagnosis,
        "evidence_validation": validation_result.model_dump(),
        "allowed_files": allowed_files,
        "investigation_steps": state.get("investigation_steps", 0) + 1,
    }


def fixer_node(state: RepairState) -> dict[str, Any]:
    """Run the Fixer agent to generate a patch proposal.

    Reads the diagnosis and retrieved context, then calls the Fixer
    agent to generate a Unified Diff patch.

    Only increments fix_attempt_count, NOT retry_count.
    retry_count is only incremented by prepare_fix_retry.

    Returns:
        Patch proposal and incremented fix_attempt_count.
    """
    from codemedic.agents.fixer import run_fixer

    fix_attempt_count = state.get("fix_attempt_count", 0) + 1
    diagnosis = state.get("diagnosis")
    if diagnosis is None:
        return {
            "patch": None,
            "fix_attempt_count": fix_attempt_count,
            "workflow_status": "running",
            "errors": state.get("errors", []) + ["No diagnosis available for Fixer"],
        }

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

        from codemedic.validation.diff import (
            check_declared_files_match_diff,
            validate_diff,
        )

        patch_dict = patch.model_dump()
        allowed = state.get("allowed_files")
        diff_result = validate_diff(
            patch_dict.get("unified_diff", ""),
            allowed_files=allowed,
        )

        mismatches = check_declared_files_match_diff(
            patch_dict.get("modified_files", []),
            patch_dict.get("unified_diff", ""),
        )

        errors: list[str] = []
        if not diff_result["valid"]:
            errors.extend(diff_result["errors"])
        if mismatches:
            errors.extend(mismatches)

        if errors:
            return {
                "patch": patch_dict,
                "fix_attempt_count": fix_attempt_count,
                "workflow_status": "running",
                "errors": state.get("errors", []) + errors,
            }

        return {
            "patch": patch_dict,
            "fix_attempt_count": fix_attempt_count,
            "workflow_status": "running",
        }
    except Exception as exc:
        return {
            "patch": None,
            "fix_attempt_count": fix_attempt_count,
            "workflow_status": "running",
            "errors": state.get("errors", []) + [f"Fixer agent error: {exc}"],
        }


def prepare_fix_retry(state: RepairState) -> dict[str, Any]:
    """Prepare state for a fixer retry attempt.

    Preserves the previous patch for feedback, increments retry_count,
    and clears the current patch/diff for regeneration.
    """
    return {
        "previous_patch": state.get("patch"),
        "retry_count": state.get("retry_count", 0) + 1,
        "patch": None,
        "diff_validation": None,
        "workflow_status": "running",
    }


def diagnosis_review_node(state: RepairState) -> dict[str, Any]:
    """Human review for diagnosis evaluation.

    Triggered when evidence is insufficient, uncertain, or when
    Investigator encounters errors. Displays diagnosis and allows
    accept/reject decisions.

    Returns:
        human_decision and review_reason.
    """
    from langgraph.types import interrupt

    diag = state.get("diagnosis")

    interrupt_value = {
        "review_type": "diagnosis",
        "message": "Please review the diagnosis.",
        "issue": state["issue"],
        "diagnosis": {
            "root_cause": diag.root_cause if diag else "N/A",
            "confidence": diag.confidence if diag else 0.0,
            "suspected_files": diag.suspected_files if diag else [],
            "evidence_count": len(diag.evidence) if diag else 0,
        },
        "options": ["accept_diagnosis", "reject"],
    }

    human_input = interrupt(interrupt_value)

    if isinstance(human_input, dict):
        decision = human_input.get("decision", "reject")
        reason = human_input.get("reason", "")
    elif isinstance(human_input, str):
        decision = human_input
        reason = ""
    else:
        decision = "reject"
        reason = "Invalid input format"

    return {
        "human_decision": decision,
        "review_reason": reason,
    }


def patch_review_node(state: RepairState) -> dict[str, Any]:
    """Human review for patch approval.

    Triggered after Fixer generates a valid patch. Shows diff and
    allows approve/reject/retry decisions.

    If retry count exceeds the maximum, skips the interrupt and
    automatically routes to rejected.

    Returns:
        human_decision and review_reason.
    """
    patch = state.get("patch")
    diag = state.get("diagnosis")

    # Check retry limit — skip interrupt if exceeded
    retry_count = state.get("retry_count", 0)
    max_retries = settings.max_fixer_retries
    if retry_count >= max_retries:
        return {
            "human_decision": "rejected",
            "review_reason": f"Retry limit ({max_retries}) exceeded",
        }

    from langgraph.types import interrupt

    interrupt_value = {
        "review_type": "patch",
        "message": "Please review the proposed patch.",
        "issue": state["issue"],
        "root_cause": diag.root_cause if diag else "N/A",
        "patch": patch,
        "options": ["approved", "rejected", "retry"],
    }

    human_input = interrupt(interrupt_value)

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


def patch_validation_node(state: RepairState) -> dict[str, Any]:
    """Validate the patch against security and structural rules.

    Runs after Fixer produces a patch but before human review.
    Stores diff_validation result in state for routing.

    Returns:
        diff_validation result.
    """
    patch = state.get("patch")
    from codemedic.schemas.results import DiffValidationResult

    if patch is None:
        return {
            "diff_validation": DiffValidationResult(
                valid=False,
                errors=["No patch to validate"],
            ).model_dump(),
        }

    from codemedic.validation.diff import validate_diff

    unified_diff = patch.get("unified_diff", "") if isinstance(patch, dict) else ""

    allowed = state.get("allowed_files")
    result = DiffValidationResult.model_validate(
        validate_diff(unified_diff, allowed_files=allowed)
    )

    return {"diff_validation": result.model_dump()}


def final_report_node(state: RepairState) -> dict[str, Any]:
    """Build the final report from the completed workflow.

    Uses derive_final_status() for single-source-of-truth status.
    Uses typed adapters for all result access.

    Returns:
        final_report dict and final_status.
    """
    from codemedic.graph.status import derive_final_status
    from codemedic.schemas.adapters import get_patch_apply_result

    diag = state.get("diagnosis")
    from codemedic.schemas.adapters import get_test_results

    test_results = get_test_results(state)

    apply_result = get_patch_apply_result(state)
    patch_applied = apply_result is not None and apply_result.success

    report: dict[str, Any] = {
        "task_id": state["task_id"],
        "issue": state["issue"],
        "root_cause": diag.root_cause if diag else "No diagnosis produced",
        "confidence": diag.confidence if diag else 0.0,
        "suspected_files": diag.suspected_files if diag else [],
        "evidence_count": len(diag.evidence) if diag else 0,
        "investigation_steps": state.get("investigation_steps", 0),
        "retrieval_rounds": state.get("retrieval_round", 0),
        "patch_applied": patch_applied,
        "test_count": len(test_results),
    }

    final_status = derive_final_status(state)

    return {
        "final_report": report,
        "final_status": final_status,
        "workflow_status": "completed",
        "verifier_summary": state.get("verifier_summary"),
        "sandbox_path": state.get("sandbox_path"),
    }


def apply_patch_node(state: RepairState) -> dict[str, Any]:
    """Apply the patch in a sandbox (temp copy of the repo).

    Creates a temporary copy, applies the Unified Diff, and records
    the PatchApplyResult in state.

    Returns:
        patch_apply_result and sandbox_path (on success).
    """
    from codemedic.schemas.results import PatchApplyResult
    from codemedic.tools.sandbox import apply_patch, create_temp_copy, verify_patch_boundaries

    patch = state.get("patch")
    if patch is None:
        result = PatchApplyResult(
            success=False, returncode=-1,
            stderr="No patch to apply",
            modified_files=[], sandbox_path=None,
        )
        return {"patch_apply_result": result.model_dump()}

    unified_diff = patch.get("unified_diff", "") if isinstance(patch, dict) else ""
    if not unified_diff:
        result = PatchApplyResult(
            success=False, returncode=-1,
            stderr="Patch has no diff content",
            modified_files=[], sandbox_path=None,
        )
        return {"patch_apply_result": result.model_dump()}

    # Verify patch boundaries
    allowed = state.get("allowed_files")
    boundary_check = verify_patch_boundaries(unified_diff, allowed)
    if not boundary_check["valid"]:
        result = PatchApplyResult(
            success=False, returncode=-1,
            stderr=f"Patch violates boundaries: {boundary_check['violations']}",
            modified_files=[], sandbox_path=None,
        )
        return {"patch_apply_result": result.model_dump()}

    try:
        sandbox_path = create_temp_copy(state["repository_path"])
        raw = apply_patch(sandbox_path, unified_diff)

        result = PatchApplyResult(
            success=raw["success"],
            returncode=raw["returncode"],
            stdout=raw.get("stdout", ""),
            stderr=raw.get("stderr", ""),
            modified_files=raw.get("modified_files", []),
            sandbox_path=sandbox_path if raw["success"] else None,
        )

        return {
            "patch_apply_result": result.model_dump(),
            "sandbox_path": result.sandbox_path,
        }
    except Exception as exc:
        result = PatchApplyResult(
            success=False, returncode=-1,
            stderr=str(exc),
            modified_files=[], sandbox_path=None,
        )
        return {"patch_apply_result": result.model_dump()}


def run_tests_node(state: RepairState) -> dict[str, Any]:
    """Run test commands in the sandbox.

    Returns:
        test_results with TestResult objects serialized to dicts.
    """
    from codemedic.tools.test_runner import run_tests

    sandbox_path = state.get("sandbox_path")
    if not sandbox_path:
        from codemedic.schemas.results import TestResult
        err_result = TestResult(
            command_id="init", argv=["error"],
            returncode=-1, stderr="No sandbox path for tests",
        )
        return {"test_results": [err_result.model_dump()]}

    try:
        results = run_tests(str(sandbox_path))
        return {"test_results": [r.model_dump() for r in results]}
    except Exception as exc:
        from codemedic.schemas.results import TestResult
        err_result = TestResult(
            command_id="error", argv=["error"],
            returncode=-1, stderr=f"Test execution error: {exc}",
        )
        return {"test_results": [err_result.model_dump()]}


def verifier_node(state: RepairState) -> dict[str, Any]:
    """Run the Verifier agent to summarize test results.

    Returns:
        verifier_summary text.
    """
    from codemedic.agents.verifier import run_verifier

    issue = state["issue"]
    patch = state.get("patch")
    from codemedic.schemas.adapters import get_test_results

    test_results = [result.model_dump() for result in get_test_results(state)]

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

