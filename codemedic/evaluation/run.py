"""Command-line entry point for explicitly configured evaluation runs."""

from __future__ import annotations

import argparse
import sys
import time
from math import fsum
from typing import Any

from codemedic.config import settings
from codemedic.evaluation.loader import load_tasks
from codemedic.evaluation.runner import EvaluationBatchRunner
from codemedic.evaluation.schemas import (
    EvaluationRunResult,
    FailureCategory,
    RepairTask,
)
from codemedic.real_model import ensure_real_model_configured


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


def classify_failure(
    state: dict[str, Any],
    *,
    trajectory: dict[str, Any],
    unauthorized_files: list[str],
    tests_passed: bool,
) -> FailureCategory | None:
    """Assign one deterministic primary failure category to a run."""
    if state.get("final_status") == "通过":
        return None

    for event in trajectory.get("events", []):
        execution = _mapping(_mapping(event).get("output_data")).get("execution")
        error = _mapping(execution).get("error")
        if error:
            text = str(error).lower()
            if "timeout" in text or "timed out" in text:
                return "Timeout"
            if any(term in text for term in ("api", "provider", "credential", "network")):
                return "Provider Failure"
            if "json" in text or "parse" in text:
                return "JSON Parse Failure"

    if unauthorized_files:
        return "Unauthorized Modification"

    evidence = _mapping(state.get("evidence_validation"))
    if evidence.get("valid") is not True:
        return "Evidence Line Failure"

    diagnosis = _mapping(state.get("diagnosis"))
    if not diagnosis:
        return "Diagnosis Failure"

    diff = _mapping(state.get("diff_validation"))
    if diff.get("valid") is not True:
        return "Diff Format Failure"

    applied = _mapping(state.get("patch_apply_result"))
    if applied.get("success") is not True:
        return "Patch Apply Failure"

    if not tests_passed:
        return "Test Failure"

    return "Harness Failure"


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
    unauthorized = sorted(set(modified_files) - set(task.allowed_files))
    expected_files = set(task.expected_files)
    observed_files = set(modified_files) or set(diff.get("modified_files", []))
    correct_file = bool(observed_files) and expected_files == observed_files
    final_status = state.get("final_status")
    false_pass = final_status == "通过" and (
        bool(unauthorized) or not tests_passed or not correct_file
    )
    failure_category = classify_failure(
        state,
        trajectory=trajectory_data,
        unauthorized_files=unauthorized,
        tests_passed=tests_passed,
    )
    result_record = EvaluationRunResult(
        task_id=task.task_id,
        run_id=run_id,
        model=model,
        diagnosis_valid=bool(diagnosis),
        evidence_valid=evidence.get("valid") is True,
        correct_file=correct_file,
        diff_valid=diff.get("valid") is True,
        patch_applied=applied.get("success") is True,
        tests_passed=tests_passed,
        unauthorized_files=unauthorized,
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
        ).run(tasks)
    except (ValueError, OSError) as exc:
        print(f"Evaluation blocked: {exc}", file=sys.stderr)
        return 2

    print(f"Evaluation summary: {paths['summary']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
