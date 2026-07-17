"""Graph nodes for the CodeMedic LangGraph workflow.

Each node is a function that receives the full RepairState and returns
only the fields it is responsible for updating.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from codemedic.agents import fixer as fixer_agent
from codemedic.agents.execution import AgentExecutionResult
from codemedic.agents.investigator import run_investigator, run_investigator_execution
from codemedic.config import settings
from codemedic.graph.state import RepairState
from codemedic.schemas.diagnosis import DiagnosisResult
from codemedic.schemas.patch import PatchProposal
from codemedic.tools.repository import list_repo_tree, read_file
from codemedic.tracing.local_trace import LocalTracer

_DEFAULT_RUN_INVESTIGATOR = run_investigator
_DEFAULT_RUN_FIXER = fixer_agent.run_fixer


def _trajectory_recorder(state: RepairState):
    """Return the active run recorder, if this node is inside WorkflowRuntime."""
    from codemedic.tracing.recorder import get_recorder

    return get_recorder(state.get("run_id"), state.get("thread_id"))


def _record_agent_execution_events(
    recorder: Any,
    *,
    node: str,
    execution: AgentExecutionResult,
) -> None:
    """Record model tool calls/results without putting them into RepairState."""
    for tool_call in execution.tool_calls:
        recorder.record(
            node=node,
            event_type="tool_call",
            summary="Agent tool call",
            output_data={"tool_call": tool_call},
        )
    for tool_result in execution.tool_results:
        recorder.record(
            node=node,
            event_type="tool_result",
            summary="Agent tool result",
            output_data={"tool_result": tool_result},
        )


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


def _build_retry_feedback(state: RepairState) -> str | None:
    """Collect concrete validation and test feedback for the next Fixer call."""
    from codemedic.schemas.adapters import get_diff_validation, get_test_results

    sections: list[str] = []
    diff = get_diff_validation(state)
    if diff is not None and not diff.valid:
        errors = diff.errors or diff.violations
        if errors:
            sections.append("Patch validation failures:\n- " + "\n- ".join(errors))

    failed_tests = [
        result for result in get_test_results(state)
        if result.returncode != 0 or result.timed_out
    ]
    for result in failed_tests:
        output = "\n".join(part for part in (result.stdout, result.stderr) if part)
        sections.append(
            f"Test {result.command_id} failed (returncode={result.returncode}, "
            f"timed_out={result.timed_out}):\n{output[:4000]}"
        )

    review_reason = state.get("review_reason")
    if state.get("human_decision") == "retry" and review_reason:
        sections.append(f"Human feedback:\n{review_reason}")

    return "\n\n".join(sections) if sections else None


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

    recorder = _trajectory_recorder(state)
    if recorder:
        recorder.record(
            node="investigator_agent",
            event_type="model_request",
            summary="Investigator model request",
            input_data={
                "issue": state["issue"],
                "error_log": state.get("error_log"),
                "retrieved_context": state.get("retrieved_context", []),
            },
        )

    tracer = LocalTracer(task_id=state["task_id"])

    execution: AgentExecutionResult | None = None
    if run_investigator is _DEFAULT_RUN_INVESTIGATOR:
        execution = run_investigator_execution(
            issue=state["issue"],
            repository_path=state["repository_path"],
            error_log=state.get("error_log"),
            trace=tracer,
        )
        diagnosis = DiagnosisResult.model_validate(execution.parsed_result)
    else:
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

    if recorder:
        response_data: dict[str, Any] = {"diagnosis": diagnosis.model_dump()}
        if execution is not None:
            _record_agent_execution_events(
                recorder,
                node="investigator_agent",
                execution=execution,
            )
            response_data["execution"] = execution.model_dump(mode="json")
        recorder.record(
            node="investigator_agent",
            event_type="model_response",
            summary="Investigator diagnosis received",
            output_data=response_data,
        )
        recorder.record(
            node="evidence_validation",
            event_type="validation",
            summary="Evidence validation completed",
            output_data=validation_result.model_dump(),
        )

    return {
        "diagnosis": diagnosis.model_dump(),
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
    fix_attempt_count = state.get("fix_attempt_count", 0) + 1
    from codemedic.schemas.adapters import get_diagnosis

    diagnosis = get_diagnosis(state)
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
    recorder = _trajectory_recorder(state)
    if recorder:
        recorder.record(
            node="fixer_agent",
            event_type="model_request",
            summary="Fixer model request",
            input_data={
                "issue": state["issue"],
                "diagnosis": diagnosis.model_dump(),
                "context": context_str,
                "previous_patch": state.get("previous_patch"),
                "failure_feedback": state.get("failure_feedback"),
                "human_feedback": state.get("human_feedback"),
            },
        )

    try:
        execution: AgentExecutionResult | None = None
        if fixer_agent.run_fixer is _DEFAULT_RUN_FIXER:
            execution = fixer_agent.run_fixer_execution(
                issue=state["issue"],
                diagnosis=diagnosis,
                context=context_str,
                previous_patch=state.get("previous_patch"),
                failure_feedback=state.get("failure_feedback"),
                human_feedback=state.get("human_feedback"),
                verifier_summary=state.get("verifier_summary"),
            )
            if execution.error:
                raise RuntimeError(execution.error)
            patch = PatchProposal.model_validate(execution.parsed_result)
        else:
            patch = fixer_agent.run_fixer(
                issue=state["issue"],
                diagnosis=diagnosis,
                context=context_str,
                previous_patch=state.get("previous_patch"),
                failure_feedback=state.get("failure_feedback"),
                human_feedback=state.get("human_feedback"),
                verifier_summary=state.get("verifier_summary"),
            )

        if recorder:
            response_data = {"patch": patch.model_dump()}
            if execution is not None:
                _record_agent_execution_events(
                    recorder,
                    node="fixer_agent",
                    execution=execution,
                )
                response_data["execution"] = execution.model_dump(mode="json")
            recorder.record(
                node="fixer_agent",
                event_type="model_response",
                summary="Fixer patch proposal received",
                output_data=response_data,
            )
            recorder.write_artifact(
                f"patch_{fix_attempt_count}.diff",
                patch.unified_diff,
            )

        return {
            "patch": patch.model_dump(),
            "fix_attempt_count": fix_attempt_count,
            "workflow_status": "running",
        }
    except Exception as exc:
        if recorder:
            recorder.record(
                node="fixer_agent",
                event_type="model_response",
                summary="Fixer model failed",
                output_data={"error": str(exc)},
            )
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
        "failure_feedback": _build_retry_feedback(state),
        "human_feedback": (
            state.get("review_reason")
            if state.get("human_decision") == "retry"
            else state.get("human_feedback")
        ),
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

    from codemedic.schemas.adapters import get_diagnosis
    from codemedic.tools.context import RepositoryContext
    from codemedic.validation.evidence import validate_approved_files

    diag = get_diagnosis(state)

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
        "approved_files": {
            "description": (
                "Optional repository-relative files to authorize for this patch. "
                "Every file must exist within the repository."
            ),
            "default": [],
        },
    }

    recorder = _trajectory_recorder(state)
    if recorder:
        recorder.record(
            node="diagnosis_review",
            event_type="interrupt",
            summary="Diagnosis Review requested",
            output_data=interrupt_value,
        )

    human_input = interrupt(interrupt_value)

    if isinstance(human_input, dict):
        raw_decision = human_input.get("decision", "reject")
        reason = human_input.get("reason", "")
        approved_input = human_input.get("approved_files", [])
    elif isinstance(human_input, str):
        raw_decision = human_input
        reason = ""
        approved_input = []
    else:
        raw_decision = "reject"
        reason = "Invalid input format"
        approved_input = []

    decision = "accept_diagnosis" if raw_decision == "accept_diagnosis" else "reject"
    if not isinstance(reason, str):
        reason = ""

    approved_files: list[str] = []
    allowed_files = list(state.get("allowed_files", []))
    errors = list(state.get("errors", []))
    if decision == "accept_diagnosis":
        try:
            ctx = RepositoryContext(state["repository_path"])
            approved_files, authorization_errors = validate_approved_files(
                approved_input, ctx
            )
        except (NotADirectoryError, OSError) as exc:
            authorization_errors = [f"Could not validate approved_files: {exc}"]

        if authorization_errors:
            decision = "reject"
            errors.extend(
                f"Invalid approved_files: {error}" for error in authorization_errors
            )
        else:
            allowed_files = sorted(set(allowed_files).union(approved_files))
            if not allowed_files:
                decision = "reject"
                errors.append(
                    "No authorized files after Diagnosis Review; "
                    "Fixer access denied"
                )

    return {
        "human_decision": decision,
        "review_reason": reason,
        "approved_files": approved_files,
        "allowed_files": allowed_files,
        "errors": errors,
    }


def patch_review_node(state: RepairState) -> dict[str, Any]:
    """Human review for patch approval.

    Triggered after Fixer generates a valid patch. Shows diff and
    allows approve/reject/retry decisions.

    Once retry capacity is exhausted, the review remains available but
    no longer offers another retry option.

    Returns:
        human_decision and review_reason.
    """
    patch = state.get("patch")
    from codemedic.schemas.adapters import get_diagnosis

    diag = get_diagnosis(state)

    retry_count = state.get("retry_count", 0)
    max_retries = settings.max_fixer_retries

    from langgraph.types import interrupt

    options = ["approved", "rejected"]
    if retry_count < max_retries:
        options.append("retry")

    interrupt_value = {
        "review_type": "patch",
        "message": "Please review the proposed patch.",
        "issue": state["issue"],
        "root_cause": diag.root_cause if diag else "N/A",
        "patch": patch,
        "options": options,
    }

    recorder = _trajectory_recorder(state)
    if recorder:
        recorder.record(
            node="patch_review",
            event_type="interrupt",
            summary="Patch Review requested",
            output_data=interrupt_value,
        )

    human_input = interrupt(interrupt_value)

    if isinstance(human_input, dict):
        raw_decision = human_input.get("decision", "rejected")
        reason = human_input.get("reason", "")
    elif isinstance(human_input, str):
        raw_decision = human_input
        reason = ""
    else:
        raw_decision = "rejected"
        reason = "Invalid input format"

    decision = (
        raw_decision
        if isinstance(raw_decision, str)
        and raw_decision in {"approved", "rejected"}
        else "rejected"
    )
    if isinstance(raw_decision, str) and raw_decision == "retry" and retry_count < max_retries:
        decision = "retry"
    if not isinstance(reason, str):
        reason = ""

    return {
        "human_decision": decision,
        "review_reason": reason,
        "human_feedback": reason if decision == "retry" else state.get("human_feedback"),
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

    from codemedic.validation.diff import (
        check_declared_files_match_diff,
        validate_diff,
    )

    unified_diff = patch.get("unified_diff", "") if isinstance(patch, dict) else ""

    allowed = state.get("allowed_files")
    raw_result = validate_diff(unified_diff, allowed_files=allowed)
    mismatches = check_declared_files_match_diff(
        patch.get("modified_files", []) if isinstance(patch, dict) else [],
        unified_diff,
    )
    raw_result["errors"].extend(mismatches)
    if mismatches:
        raw_result["valid"] = False

    result = DiffValidationResult.model_validate(raw_result)

    recorder = _trajectory_recorder(state)
    if recorder:
        recorder.record(
            node="patch_validation",
            event_type="validation",
            summary="Diff validation completed",
            output_data=result.model_dump(),
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
    from codemedic.schemas.adapters import get_diagnosis, get_patch_apply_result

    diag = get_diagnosis(state)
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

    recorder = _trajectory_recorder(state)
    if recorder:
        recorder.write_json_artifact("report.json", report | {"final_status": final_status})

    workflow_status = "failed" if any(
        error.startswith("Repository path not found:")
        for error in state.get("errors", [])
    ) else "completed"

    return {
        "final_report": report,
        "final_status": final_status,
        "workflow_status": workflow_status,
        "verifier_summary": state.get("verifier_summary"),
        "sandbox_path": (
            state.get("sandbox_path")
            if not state.get("sandbox_cleaned", False)
            else None
        ),
        "sandbox_cleaned": state.get("sandbox_cleaned", False),
    }


def _record_patch_apply(state: RepairState, result: Any) -> None:
    recorder = _trajectory_recorder(state)
    if recorder:
        recorder.record(
            node="apply_patch",
            event_type="patch_apply",
            summary="Patch application completed",
            output_data=result.model_dump(),
        )


def apply_patch_node(state: RepairState) -> dict[str, Any]:
    """Apply the patch in a sandbox (temp copy of the repo).

    Creates a temporary copy, applies the Unified Diff, and records
    the PatchApplyResult in state.

    Returns:
        patch_apply_result and sandbox_path (on success).
    """
    from codemedic.schemas.results import PatchApplyResult
    from codemedic.tools.sandbox import (
        apply_patch,
        cleanup_sandbox,
        create_temp_copy,
        verify_patch_boundaries,
    )

    patch = state.get("patch")
    if patch is None:
        result = PatchApplyResult(
            success=False, returncode=-1,
            stderr="No patch to apply",
            modified_files=[], sandbox_path=None,
        )
        _record_patch_apply(state, result)
        return {
            "patch_apply_result": result.model_dump(),
            "sandbox_path": None,
            "sandbox_cleaned": True,
        }

    unified_diff = patch.get("unified_diff", "") if isinstance(patch, dict) else ""
    if not unified_diff:
        result = PatchApplyResult(
            success=False, returncode=-1,
            stderr="Patch has no diff content",
            modified_files=[], sandbox_path=None,
        )
        _record_patch_apply(state, result)
        return {
            "patch_apply_result": result.model_dump(),
            "sandbox_path": None,
            "sandbox_cleaned": True,
        }

    # Verify patch boundaries
    allowed = state.get("allowed_files")
    boundary_check = verify_patch_boundaries(unified_diff, allowed)
    if not boundary_check["valid"]:
        result = PatchApplyResult(
            success=False, returncode=-1,
            stderr=f"Patch violates boundaries: {boundary_check['violations']}",
            modified_files=[], sandbox_path=None,
        )
        _record_patch_apply(state, result)
        return {
            "patch_apply_result": result.model_dump(),
            "sandbox_path": None,
            "sandbox_cleaned": True,
        }

    try:
        sandbox_path = create_temp_copy(state["repository_path"])
        raw = apply_patch(sandbox_path, unified_diff)

        if not raw["success"]:
            cleanup_sandbox(sandbox_path)
            result = PatchApplyResult(
                success=False,
                returncode=raw["returncode"],
                stdout=raw.get("stdout", ""),
                stderr=raw.get("stderr", ""),
                modified_files=raw.get("modified_files", []),
                sandbox_path=None,
            )
            _record_patch_apply(state, result)
            return {
                "patch_apply_result": result.model_dump(),
                "sandbox_path": None,
                "sandbox_cleaned": True,
            }

        result = PatchApplyResult(
            success=True,
            returncode=raw["returncode"],
            stdout=raw.get("stdout", ""),
            stderr=raw.get("stderr", ""),
            modified_files=raw.get("modified_files", []),
            sandbox_path=sandbox_path,
        )

        _record_patch_apply(state, result)
        return {
            "patch_apply_result": result.model_dump(),
            "sandbox_path": result.sandbox_path,
            "sandbox_cleaned": False,
        }
    except Exception as exc:
        if "sandbox_path" in locals():
            cleanup_sandbox(sandbox_path)
        result = PatchApplyResult(
            success=False, returncode=-1,
            stderr=str(exc),
            modified_files=[], sandbox_path=None,
        )
        _record_patch_apply(state, result)
        return {
            "patch_apply_result": result.model_dump(),
            "sandbox_path": None,
            "sandbox_cleaned": True,
        }


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
        recorder = _trajectory_recorder(state)
        if recorder:
            recorder.record(
                node="run_tests",
                event_type="test_result",
                summary="Tests could not start",
                output_data={"results": [err_result.model_dump()]},
            )
            recorder.write_json_artifact("tests.json", [err_result.model_dump()])
        return {
            "test_results": [err_result.model_dump()],
            "sandbox_path": None,
            "sandbox_cleaned": True,
        }

    try:
        results = run_tests(
            str(sandbox_path),
            execution_backend=state.get("execution_backend", settings.execution_backend),
        )
        serialized = [r.model_dump() for r in results]
        recorder = _trajectory_recorder(state)
        if recorder:
            recorder.record(
                node="run_tests",
                event_type="test_result",
                summary="Tests completed",
                output_data={"results": serialized},
            )
            recorder.write_json_artifact("tests.json", serialized)
        return {
            "test_results": serialized,
            "sandbox_path": None,
            "sandbox_cleaned": True,
        }
    except Exception as exc:
        from codemedic.schemas.results import TestResult
        err_result = TestResult(
            command_id="error", argv=["error"],
            returncode=-1, stderr=f"Test execution error: {exc}",
        )
        recorder = _trajectory_recorder(state)
        if recorder:
            recorder.record(
                node="run_tests",
                event_type="test_result",
                summary="Test execution failed",
                output_data={"results": [err_result.model_dump()]},
            )
            recorder.write_json_artifact("tests.json", [err_result.model_dump()])
        return {
            "test_results": [err_result.model_dump()],
            "sandbox_path": None,
            "sandbox_cleaned": True,
        }
    finally:
        from codemedic.tools.sandbox import cleanup_sandbox

        cleanup_sandbox(str(sandbox_path))


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

    recorder = _trajectory_recorder(state)
    if recorder:
        recorder.record(
            node="verifier_agent",
            event_type="model_request",
            summary="Verifier model request",
            input_data={
                "issue": issue,
                "patch_summary": patch_summary,
                "test_results": test_results,
            },
        )

    try:
        summary = run_verifier(issue, patch_summary, test_results)
        if recorder:
            recorder.record(
                node="verifier_agent",
                event_type="model_response",
                summary="Verifier summary received",
                output_data={"summary": summary},
            )
        return {"verifier_summary": summary}
    except Exception as exc:
        if recorder:
            recorder.record(
                node="verifier_agent",
                event_type="model_response",
                summary="Verifier model failed",
                output_data={"error": str(exc)},
            )
        return {"verifier_summary": f"Verifier error: {exc}"}

