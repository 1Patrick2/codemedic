from __future__ import annotations

import json
from pathlib import Path

import pytest

from codemedic.evaluation.loader import load_tasks
from codemedic.evaluation.report import EvaluationReportWriter, summarize_results
from codemedic.evaluation.runner import EvaluationBatchRunner
from codemedic.evaluation.schemas import EvaluationRunResult, RepairTask


def _task_payload() -> dict:
    return {
        "task_id": "demo-task",
        "repository_path": "demo_repos/sample_project",
        "issue": "The helper raises a NameError.",
        "error_log": "NameError: name 'resut' is not defined",
        "allowed_files": ["src/utils/math_helpers.py"],
        "expected_files": ["src/utils/math_helpers.py"],
        "forbidden_files": ["tests/test_math_helpers.py"],
        "setup_commands": [],
        "test_commands": [["python", "-m", "pytest", "-q"]],
        "expected_root_cause_terms": ["resut", "result"],
        "max_retries": 1,
    }


def _run_result(**overrides: object) -> EvaluationRunResult:
    payload: dict[str, object] = {
        "task_id": "demo-task",
        "run_id": "run-1",
        "model": "test-model",
        "diagnosis_valid": True,
        "evidence_valid": True,
        "correct_file": True,
        "diff_valid": True,
        "patch_applied": True,
        "tests_passed": True,
        "unauthorized_files": [],
        "false_pass": False,
        "retries": 0,
        "tool_calls": 2,
        "latency_ms": 125.0,
        "token_usage": 42,
        "cost": 0.01,
        "failure_stage": None,
    }
    payload.update(overrides)
    return EvaluationRunResult.model_validate(payload)


def test_evaluation_schemas_round_trip_json() -> None:
    task = RepairTask.model_validate(_task_payload())
    result = _run_result()

    assert RepairTask.model_validate_json(task.model_dump_json()) == task
    assert EvaluationRunResult.model_validate_json(result.model_dump_json()) == result


def test_evaluation_schema_rejects_empty_task_boundaries() -> None:
    payload = _task_payload()
    payload["allowed_files"] = []

    with pytest.raises(ValueError):
        RepairTask.model_validate(payload)


def test_loader_reads_yaml_tasks_and_rejects_duplicate_ids(tmp_path) -> None:
    (tmp_path / "one.yaml").write_text(
        "\n".join(
            [
                "task_id: demo-task",
                "repository_path: demo_repos/sample_project",
                "issue: The helper raises a NameError.",
                "error_log: 'NameError: name ''resut'' is not defined'",
                "allowed_files: [src/utils/math_helpers.py]",
                "expected_files: [src/utils/math_helpers.py]",
                "forbidden_files: [tests/test_math_helpers.py]",
                "setup_commands: []",
                "test_commands:",
                "  - [python, -m, pytest, -q]",
                "expected_root_cause_terms: [resut, result]",
                "max_retries: 1",
            ]
        ),
        encoding="utf-8",
    )

    tasks = load_tasks(tmp_path)

    assert tasks == [RepairTask.model_validate(_task_payload())]

    (tmp_path / "two.json").write_text(
        json.dumps(_task_payload()),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate task_id"):
        load_tasks(tmp_path)


def test_builtin_evaluation_catalog_contains_five_isolated_tasks() -> None:
    tasks = load_tasks(Path("evaluation/tasks"))

    assert len(tasks) == 5
    assert len({task.task_id for task in tasks}) == 5
    assert all(task.repository_path.is_dir() for task in tasks)
    assert all(set(task.expected_files) <= set(task.allowed_files) for task in tasks)


def test_report_writer_persists_runs_summary_jsonl_report_and_trajectory(tmp_path) -> None:
    writer = EvaluationReportWriter(tmp_path / "batch-1")
    result = _run_result()

    writer.write_run(result, trajectory={"events": [{"sequence": 1}]})
    paths = writer.finalize()

    assert paths["summary"].is_file()
    assert paths["runs"].is_file()
    assert paths["report"].is_file()
    assert (tmp_path / "batch-1" / "trajectories" / "run-1.json").is_file()
    assert json.loads(paths["summary"].read_text(encoding="utf-8"))["total_runs"] == 1
    assert len(paths["runs"].read_text(encoding="utf-8").splitlines()) == 1
    assert "demo-task" in paths["report"].read_text(encoding="utf-8")


def test_summary_marks_unsafe_results_as_failure() -> None:
    summary = summarize_results(
        [
            _run_result(unauthorized_files=["README.md"]),
            _run_result(run_id="run-2", false_pass=True, tests_passed=False),
        ]
    )

    assert summary["total_runs"] == 2
    assert summary["unauthorized_modification_rate"] == 0.5
    assert summary["false_pass_rate"] == 0.5
    assert summary["end_to_end_pass_rate"] == 0.0


def test_batch_runner_isolates_each_repeat_and_persists_trajectory(tmp_path) -> None:
    source_repo = tmp_path / "source-repo"
    source_repo.mkdir()
    (source_repo / "marker.txt").write_text("baseline", encoding="utf-8")
    task = RepairTask.model_validate(_task_payload() | {"repository_path": source_repo})
    seen: list[tuple[str, str, str]] = []

    def executor(
        isolated_task: RepairTask,
        run_id: str,
        model: str,
    ) -> tuple[EvaluationRunResult, dict]:
        seen.append((str(isolated_task.repository_path), run_id, model))
        assert isolated_task.repository_path != source_repo
        assert (
            (isolated_task.repository_path / "marker.txt").read_text(encoding="utf-8")
            == "baseline"
        )
        return (
            _run_result(task_id=isolated_task.task_id, run_id=run_id, model=model),
            {"run_id": run_id, "events": []},
        )

    paths = EvaluationBatchRunner(
        output_dir=tmp_path / "results",
        model="test-model",
        repeats=2,
        executor=executor,
    ).run([task])

    assert len(seen) == 2
    assert seen[0][1] != seen[1][1]
    assert all(item[2] == "test-model" for item in seen)
    assert paths["summary"].is_file()
    assert len(list((tmp_path / "results" / "trajectories").glob("*.json"))) == 2


def test_batch_runner_records_executor_failure_as_failed_result(tmp_path) -> None:
    task = RepairTask.model_validate(_task_payload() | {"repository_path": tmp_path})

    def executor(_task: RepairTask, _run_id: str, _model: str) -> EvaluationRunResult:
        raise RuntimeError("controlled executor failure")

    paths = EvaluationBatchRunner(
        output_dir=tmp_path / "results",
        model="test-model",
        executor=executor,
    ).run([task])

    result = json.loads(paths["runs"].read_text(encoding="utf-8"))
    assert result["failure_stage"] == "harness"
    assert "controlled executor failure" in result["error"]
