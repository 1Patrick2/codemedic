from __future__ import annotations

import json

import pytest

from codemedic.evaluation.schemas import EvaluationRunResult


def _result(run_id: str = "run-1") -> EvaluationRunResult:
    return EvaluationRunResult(
        task_id="demo-task",
        run_id=run_id,
        model="test-model",
        diagnosis_valid=True,
        evidence_valid=True,
        correct_file=True,
        diff_valid=True,
        patch_applied=True,
        tests_passed=True,
    )


def _write_evaluation(root, evaluation_id: str = "eval-1"):
    directory = root / evaluation_id
    directory.mkdir(parents=True)
    (directory / "summary.json").write_text(
        json.dumps({"total_runs": 1, "final_test_pass_rate": 1.0}),
        encoding="utf-8",
    )
    (directory / "runs.jsonl").write_text(
        _result().model_dump_json() + "\n",
        encoding="utf-8",
    )
    return directory


def test_evaluation_api_lists_and_reads_json_reports(tmp_path) -> None:
    from codemedic.evaluation.api import (
        get_evaluation_runs,
        get_evaluation_summary,
        list_evaluations,
    )

    root = tmp_path / "evaluations"
    _write_evaluation(root)

    assert list_evaluations(root) == ["eval-1"]
    assert get_evaluation_summary("eval-1", root) == {
        "total_runs": 1,
        "final_test_pass_rate": 1.0,
    }
    assert get_evaluation_runs("eval-1", root) == [_result()]


def test_evaluation_api_returns_empty_list_for_missing_root(tmp_path) -> None:
    from codemedic.evaluation.api import list_evaluations

    assert list_evaluations(tmp_path / "missing") == []


@pytest.mark.parametrize("evaluation_id", ["../eval-1", "nested/eval-1", ""])
def test_evaluation_api_rejects_unsafe_evaluation_ids(tmp_path, evaluation_id: str) -> None:
    from codemedic.evaluation.api import get_evaluation_summary

    with pytest.raises(ValueError, match="evaluation"):
        get_evaluation_summary(evaluation_id, tmp_path)
