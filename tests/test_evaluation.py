from __future__ import annotations

import json
from pathlib import Path

import pytest

from codemedic.evaluation.loader import load_tasks
from codemedic.evaluation.report import EvaluationReportWriter, summarize_results
from codemedic.evaluation.run import (
    classify_failure,
    measure_evidence_accuracy,
    summarize_trajectory,
)
from codemedic.evaluation.runner import EvaluationBatchRunner
from codemedic.evaluation.schemas import EvaluationRunResult, RepairTask


def _task_payload() -> dict:
    return {
        "task_id": "demo-task",
        "task_version": "2",
        "task_kind": "repairable",
        "repository_path": "demo_repos/sample_project",
        "issue": "The helper raises a NameError.",
        "error_log": "NameError: name 'resut' is not defined",
        "allowed_files": ["src/utils/math_helpers.py"],
        "expected_files": ["src/utils/math_helpers.py"],
        "forbidden_files": ["tests/test_math_helpers.py"],
        "setup_commands": [],
        "test_commands": [["python", "-m", "pytest", "-q"]],
        "expected_root_cause_terms": ["resut", "result"],
        "expected_evidence": [
            {
                "file_path": "src/utils/math_helpers.py",
                "line_start": 40,
                "line_end": 40,
                "excerpt": "resut = 1",
            }
        ],
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
                "task_version: '2'",
                "task_kind: repairable",
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
                "expected_evidence:",
                "  - file_path: src/utils/math_helpers.py",
                "    line_start: 40",
                "    line_end: 40",
                "    excerpt: 'resut = 1'",
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

    assert len(tasks) >= 5
    assert len({task.task_id for task in tasks}) == len(tasks)
    assert sum(task.task_kind == "repairable" for task in tasks) == 5
    assert all(task.repository_path.is_dir() for task in tasks)
    assert all(set(task.expected_files) <= set(task.allowed_files) for task in tasks)


def test_builtin_catalog_contains_safety_task_kinds() -> None:
    tasks = load_tasks(Path("evaluation/tasks"))

    kinds = {task.task_kind for task in tasks}
    assert "unrepairable" in kinds
    assert "unauthorized_prompt" in kinds


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
    run_record = json.loads(paths["runs"].read_text(encoding="utf-8"))
    assert run_record["trajectory_path"] == "trajectories/run-1.json"
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


def test_summary_includes_usage_and_failure_categories() -> None:
    summary = summarize_results(
        [
            _run_result(token_usage=10, cost=0.1, failure_category="TEST_FAILURE"),
            _run_result(
                run_id="run-2",
                token_usage=30,
                cost=0.3,
                failure_category="TEST_FAILURE",
            ),
        ]
    )

    assert summary["average_token_usage"] == 20.0
    assert summary["average_cost"] == 0.2
    assert summary["failure_categories"] == {"TEST_FAILURE": 2}


def test_summary_does_not_treat_unknown_cost_as_zero() -> None:
    summary = summarize_results([_run_result(cost=None)])

    assert summary["average_cost"] is None


def test_measure_evidence_accuracy_checks_file_line_and_excerpt() -> None:
    task = RepairTask.model_validate(_task_payload())
    diagnosis = {
        "evidence": [
            {
                "file_path": "src/utils/math_helpers.py",
                "line_start": 40,
                "line_end": 40,
                "excerpt": "resut = 1",
            }
        ]
    }

    assert measure_evidence_accuracy(diagnosis, task) == {
        "file": True,
        "line": True,
        "excerpt": True,
    }

    mismatch = diagnosis | {
        "evidence": [
            {
                "file_path": "src/utils/math_helpers.py",
                "line_start": 41,
                "line_end": 41,
                "excerpt": "return result",
            }
        ]
    }
    assert measure_evidence_accuracy(mismatch, task) == {
        "file": True,
        "line": False,
        "excerpt": False,
    }


def test_unrepairable_task_may_have_no_expected_files() -> None:
    task = RepairTask.model_validate(
        _task_payload()
        | {
            "task_kind": "unrepairable",
            "expected_files": [],
            "expected_evidence": [],
        }
    )
    assert task.expected_files == []

    with pytest.raises(ValueError, match="expected_files"):
        RepairTask.model_validate(_task_payload() | {"expected_files": []})


def test_summary_includes_accuracy_and_authorization_metrics() -> None:
    summary = summarize_results(
        [
            _run_result(
                evidence_file_accuracy=True,
                evidence_line_accuracy=True,
                evidence_excerpt_accuracy=False,
                human_authorized=True,
            ),
            _run_result(
                run_id="run-2",
                evidence_file_accuracy=False,
                evidence_line_accuracy=False,
                evidence_excerpt_accuracy=False,
                human_authorized=False,
            ),
        ]
    )

    assert summary["evidence_file_accuracy_rate"] == 0.5
    assert summary["evidence_line_accuracy_rate"] == 0.5
    assert summary["evidence_excerpt_accuracy_rate"] == 0.0
    assert summary["human_authorization_rate"] == 0.5


def test_summarize_trajectory_captures_model_usage_and_tool_calls() -> None:
    trajectory = {
        "events": [
            {
                "event_type": "model_response",
                "output_data": {
                    "execution": {
                        "tool_calls": [{"name": "read_file"}],
                        "total_tokens": 18,
                        "estimated_cost": 0.01,
                        "latency_ms": 125.0,
                    }
                },
            },
            {
                "event_type": "model_response",
                "output_data": {
                    "execution": {
                        "tool_calls": [
                            {"name": "search_code"},
                            {"name": "read_file"},
                        ],
                        "total_tokens": 12,
                        "estimated_cost": 0.02,
                        "latency_ms": 80.0,
                    }
                },
            },
        ]
    }

    assert summarize_trajectory(trajectory) == {
        "tool_calls": 3,
        "token_usage": 30,
        "cost": 0.03,
        "model_latency_ms": 205.0,
    }


@pytest.mark.parametrize(
    ("state", "unauthorized_files", "tests_passed", "expected"),
    [
        (
            {"final_status": "人工复核", "diagnosis": {}, "evidence_validation": {"valid": False}},
            [],
            False,
            "EVIDENCE_FAILURE",
        ),
        (
            {"final_status": "人工复核", "diagnosis": {}, "evidence_validation": {"valid": True}},
            ["README.md"],
            True,
            "UNAUTHORIZED_FILE",
        ),
        (
            {"final_status": "人工复核", "diagnosis": {}, "evidence_validation": {"valid": True}},
            [],
            False,
            "SCHEMA_FAILURE",
        ),
    ],
)
def test_classify_failure_returns_one_deterministic_category(
    state: dict[str, object],
    unauthorized_files: list[str],
    tests_passed: bool,
    expected: str,
) -> None:
    assert (
        classify_failure(
            state,
            trajectory={"events": []},
            unauthorized_files=unauthorized_files,
            tests_passed=tests_passed,
        )
        == expected
    )


def test_classify_failure_uses_machine_codes_for_provider_tool_wrong_file_and_retry() -> None:
    state = {
        "final_status": "waiting",
        "diagnosis": {"root_cause": "broken"},
        "evidence_validation": {"valid": True},
        "diff_validation": {"valid": True},
        "patch_apply_result": {"success": True},
    }

    assert (
        classify_failure(
            state,
            trajectory={
                "events": [
                    {
                        "event_type": "model_response",
                        "output_data": {"execution": {"error": "provider timeout"}},
                    }
                ]
            },
            unauthorized_files=[],
            tests_passed=True,
            correct_file=True,
        )
        == "AGENT_TIMEOUT"
    )
    assert (
        classify_failure(
            state,
            trajectory={
                "events": [
                    {
                        "event_type": "tool_result",
                        "output_data": {"tool_result": {"content": "ERROR: read failed"}},
                    }
                ]
            },
            unauthorized_files=[],
            tests_passed=True,
            correct_file=True,
        )
        == "TOOL_CALL_FAILURE"
    )
    assert (
        classify_failure(
            state,
            trajectory={"events": []},
            unauthorized_files=[],
            tests_passed=False,
            correct_file=False,
        )
        == "WRONG_FILE"
    )
    assert (
        classify_failure(
            state | {"retry_count": 1},
            trajectory={"events": []},
            unauthorized_files=[],
            tests_passed=False,
            correct_file=True,
        )
        == "RETRY_EXHAUSTED"
    )


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
    run_records = [
        json.loads(line)
        for line in paths["runs"].read_text(encoding="utf-8").splitlines()
    ]
    assert all(record["trajectory_path"].startswith("trajectories/") for record in run_records)


def test_batch_runner_resets_previous_output_before_reusing_directory(tmp_path) -> None:
    source_repo = tmp_path / "source-repo"
    source_repo.mkdir()
    task = RepairTask.model_validate(_task_payload() | {"repository_path": source_repo})

    def executor(
        isolated_task: RepairTask,
        run_id: str,
        model: str,
    ) -> tuple[EvaluationRunResult, dict]:
        return (
            _run_result(
                task_id=isolated_task.task_id,
                run_id=run_id,
                model=model,
            ),
            {"run_id": run_id, "events": []},
        )

    runner = EvaluationBatchRunner(
        output_dir=tmp_path / "results",
        model="test-model",
        repeats=1,
        executor=executor,
    )
    runner.run([task])
    paths = runner.run([task])

    assert len(paths["runs"].read_text(encoding="utf-8").splitlines()) == 1
    assert len(list((tmp_path / "results" / "trajectories").glob("*.json"))) == 1


def test_batch_runner_persists_task_metadata_and_output_directories(tmp_path) -> None:
    source_repo = tmp_path / "source-repo"
    source_repo.mkdir()
    task = RepairTask.model_validate(
        _task_payload()
        | {
            "repository_path": source_repo,
            "task_version": "7",
            "task_kind": "repairable",
        }
    )

    def executor(
        isolated_task: RepairTask,
        run_id: str,
        model: str,
    ) -> EvaluationRunResult:
        return _run_result(
            task_id=isolated_task.task_id,
            run_id=run_id,
            model=model,
        )

    paths = EvaluationBatchRunner(
        output_dir=tmp_path / "results",
        model="test-model",
        executor=executor,
        commit_sha="abc123",
        provider="test-provider",
        prompt_version="prompt-7",
    ).run([task])

    record = json.loads(paths["runs"].read_text(encoding="utf-8"))
    assert record["task_version"] == "7"
    assert record["task_kind"] == "repairable"
    assert record["commit_sha"] == "abc123"
    assert record["provider"] == "test-provider"
    assert record["prompt_version"] == "prompt-7"
    assert (tmp_path / "results" / "workspaces").is_dir()
    assert (tmp_path / "results" / "trajectories").is_dir()


def test_batch_runner_records_executor_failure_as_failed_result(tmp_path) -> None:
    task = RepairTask.model_validate(_task_payload() | {"repository_path": tmp_path})

    def executor(_task: RepairTask, _run_id: str, _model: str) -> EvaluationRunResult:
        raise RuntimeError("controlled executor failure")

    paths = EvaluationBatchRunner(
        output_dir=tmp_path / "results",
        model="test-model",
        executor=executor,
        commit_sha="abc123",
        provider="test-provider",
        prompt_version="prompt-7",
    ).run([task])

    result = json.loads(paths["runs"].read_text(encoding="utf-8"))
    assert result["failure_stage"] == "HARNESS_FAILURE"
    assert result["task_version"] == "2"
    assert result["commit_sha"] == "abc123"
    assert result["provider"] == "test-provider"
    assert "controlled executor failure" in result["error"]
