"""Command-line entry point for explicitly configured evaluation runs."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from math import fsum
from typing import Any

from codemedic.config import settings
from codemedic.evaluation.loader import load_tasks
from codemedic.evaluation.runner import EvaluationBatchRunner
from codemedic.evaluation.schemas import (
    EvaluationRunResult,
    EvidenceExpectation,
    FailureCategory,
    RepairTask,
)
from codemedic.real_model import ensure_real_model_configured


def _current_commit_sha() -> str | None:
    """Read the local checkout SHA without exposing repository contents."""
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            check=False,
            text=True,
        )
    except OSError:
        return None
    sha = completed.stdout.strip()
    return sha if completed.returncode == 0 and sha else None


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        return dumped if isinstance(dumped, dict) else {}
    return {}


def summarize_trajectory(trajectory: dict[str, Any]) -> dict[str, Any]:
    """Aggregate measured model usage from persisted model-response events."""
    tool_calls = 0
    token_usage = 0
    costs: list[float] = []
    model_latency: list[float] = []
    for event in trajectory.get("events", []):
        event_data = _mapping(event)
        if event_data.get("event_type") != "model_response":
            continue
        output_data = _mapping(event_data.get("output_data"))
        execution = _mapping(output_data.get("execution"))
        tool_calls += len(execution.get("tool_calls", []))
        token_usage += int(execution.get("total_tokens") or 0)
        if execution.get("estimated_cost") is not None:
            costs.append(float(execution["estimated_cost"]))
        if execution.get("latency_ms") is not None:
            model_latency.append(float(execution["latency_ms"]))
    return {
        "tool_calls": tool_calls,
        "token_usage": token_usage,
        "cost": fsum(costs),
        "model_latency_ms": fsum(model_latency),
    }


def measure_evidence_accuracy(
    diagnosis: dict[str, Any],
    task: RepairTask,
) -> dict[str, bool | None]:
    """Compare model evidence with the task's optional gold evidence."""
    expected = task.expected_evidence
    if not expected:
        return {"file": None, "line": None, "excerpt": None}

    observed_by_file = {
        str(_mapping(item).get("file_path")): _mapping(item)
        for item in diagnosis.get("evidence", [])
        if _mapping(item).get("file_path")
    }
    file_accuracy = {item.file_path for item in expected} == set(observed_by_file)

    def matches_line(item: EvidenceExpectation) -> bool:
        observed = observed_by_file.get(item.file_path)
        return bool(
            observed
            and observed.get("line_start") == item.line_start
            and observed.get("line_end") == item.line_end
        )

    def matches_excerpt(item: EvidenceExpectation) -> bool:
        observed = observed_by_file.get(item.file_path)
        if not observed:
            return False
        expected_excerpt = item.excerpt.strip()
        observed_excerpt = str(observed.get("excerpt", "")).strip()
        return not expected_excerpt or expected_excerpt in observed_excerpt

    return {
        "file": file_accuracy,
        "line": all(matches_line(item) for item in expected),
        "excerpt": all(matches_excerpt(item) for item in expected),
    }


def classify_failure(
    state: dict[str, Any],
    *,
    trajectory: dict[str, Any],
    unauthorized_files: list[str],
    tests_passed: bool,
    correct_file: bool | None = None,
) -> FailureCategory | None:
    """Assign one deterministic primary failure category to a run."""
    if state.get("final_status") == "通过":
        return None

    for event in trajectory.get("events", []):
        event_data = _mapping(event)
        output_data = _mapping(event_data.get("output_data"))
        if event_data.get("event_type") == "tool_result":
            tool_result = _mapping(output_data.get("tool_result"))
            if "error" in str(tool_result.get("content", "")).lower():
                return "TOOL_CALL_FAILURE"
        execution = output_data.get("execution")
        error = _mapping(execution).get("error")
        if error:
            text = str(error).lower()
            if "timeout" in text or "timed out" in text:
                return "AGENT_TIMEOUT"
            if any(term in text for term in ("api", "provider", "credential", "network")):
                return "PROVIDER_ERROR"
            if "json" in text or "parse" in text:
                return "JSON_PARSE_FAILURE"

    if unauthorized_files:
        return "UNAUTHORIZED_FILE"

    if correct_file is False:
        return "WRONG_FILE"

    evidence = _mapping(state.get("evidence_validation"))
    if evidence.get("valid") is not True:
        return "EVIDENCE_FAILURE"

    diagnosis = _mapping(state.get("diagnosis"))
    if not diagnosis:
        return "SCHEMA_FAILURE"

    diff = _mapping(state.get("diff_validation"))
    if diff.get("valid") is not True:
        return "DIFF_FORMAT_FAILURE"

    applied = _mapping(state.get("patch_apply_result"))
    if applied.get("success") is not True:
        return "PATCH_APPLY_FAILURE"

    if not tests_passed:
        return "RETRY_EXHAUSTED" if state.get("retry_count", 0) else "TEST_FAILURE"

    return "HARNESS_FAILURE"


def run_real_model_task(
    task: RepairTask,
    run_id: str,
    model: str,
) -> tuple[EvaluationRunResult, dict[str, Any] | None]:
    """Run one task through the public workflow with controlled review decisions."""
    ensure_real_model_configured()
    from codemedic.graph.runtime import WorkflowRuntime

    started = time.perf_counter()
    checkpoint_path = task.repository_path.parent / f".{run_id}.sqlite"
    with WorkflowRuntime(checkpoint_path=checkpoint_path) as runtime:
        result = runtime.run(
            task.issue,
            str(task.repository_path),
            task.error_log,
            thread_id=run_id,
        )
        for _ in range(task.max_retries + 2):
            if not result.interrupted:
                break
            if result.workflow_status == "waiting_diagnosis_review":
                result = runtime.resume(
                    "accept_diagnosis",
                    thread_id=run_id,
                    approved_files=task.allowed_files,
                    reason="Controlled evaluation authorization",
                )
            elif result.workflow_status == "waiting_patch_review":
                result = runtime.resume(
                    "approved",
                    thread_id=run_id,
                    reason="Controlled evaluation patch approval",
                )
            else:
                break

    state = result.state
    trajectory: dict[str, Any] | None = None
    try:
        from codemedic.graph.builder import get_trajectory

        trajectory = get_trajectory(str(state.get("run_id")))
    except (FileNotFoundError, OSError, ValueError):
        trajectory = None

    trajectory_data = trajectory or {"events": []}
    usage = summarize_trajectory(trajectory_data)
    diagnosis = _mapping(state.get("diagnosis"))
    evidence = _mapping(state.get("evidence_validation"))
    diff = _mapping(state.get("diff_validation"))
    applied = _mapping(state.get("patch_apply_result"))
    tests = state.get("test_results", [])
    tests_passed = bool(tests) and all(
        _mapping(test).get("returncode") == 0 and not _mapping(test).get("timed_out", False)
        for test in tests
    )
    modified_files = [str(path) for path in applied.get("modified_files", [])]
    evidence_accuracy = measure_evidence_accuracy(diagnosis, task)
    unauthorized = sorted(set(modified_files) - set(task.allowed_files))
    expected_files = set(task.expected_files)
    observed_files = set(modified_files) or set(diff.get("modified_files", []))
    correct_file = bool(observed_files) and expected_files == observed_files
    final_status = state.get("final_status")
    false_pass = final_status == "通过" and (
        bool(unauthorized) or not tests_passed or not correct_file
    )
    human_authorized = any(
        _mapping(event).get("event_type") == "resume"
        for event in trajectory_data.get("events", [])
    )
    failure_category = classify_failure(
        state,
        trajectory=trajectory_data,
        unauthorized_files=unauthorized,
        tests_passed=tests_passed,
        correct_file=correct_file,
    )
    result_record = EvaluationRunResult(
        task_id=task.task_id,
        task_version=task.task_version,
        task_kind=task.task_kind,
        run_id=run_id,
        model=model,
        commit_sha=task.commit_sha,
        provider="openai-compatible",
        prompt_version="codemedic-current",
        diagnosis_valid=bool(diagnosis),
        evidence_valid=evidence.get("valid") is True,
        correct_file=correct_file,
        diff_valid=diff.get("valid") is True,
        patch_applied=applied.get("success") is True,
        tests_passed=tests_passed,
        unauthorized_files=unauthorized,
        modified_files=modified_files,
        evidence_file_accuracy=evidence_accuracy["file"],
        evidence_line_accuracy=evidence_accuracy["line"],
        evidence_excerpt_accuracy=evidence_accuracy["excerpt"],
        human_authorized=human_authorized,
        false_pass=false_pass,
        retries=int(state.get("retry_count", 0)),
        tool_calls=int(usage["tool_calls"]),
        latency_ms=(time.perf_counter() - started) * 1000,
        token_usage=int(usage["token_usage"]) or None,
        cost=float(usage["cost"]) or None,
        failure_stage=failure_category,
        failure_category=failure_category,
    )
    return result_record, trajectory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run CodeMedic evaluation tasks.")
    parser.add_argument("--tasks", default="evaluation/tasks", help="Task definition directory")
    parser.add_argument("--output", required=True, help="Isolated evaluation output directory")
    parser.add_argument("--model", default=settings.openai_model_name)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args(argv)

    try:
        ensure_real_model_configured()
        tasks = load_tasks(args.tasks)
        paths = EvaluationBatchRunner(
            output_dir=args.output,
            model=args.model,
            repeats=args.repeats,
            executor=run_real_model_task,
            commit_sha=_current_commit_sha(),
            provider="openai-compatible",
            prompt_version="codemedic-current",
        ).run(tasks)
    except (ValueError, OSError) as exc:
        print(f"Evaluation blocked: {exc}", file=sys.stderr)
        return 2

    print(f"Evaluation summary: {paths['summary']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
