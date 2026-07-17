from __future__ import annotations

from codemedic.evaluation.schemas import RepairTask
from codemedic.retrieval.baselines import (
    compare_retrieval_baselines,
    retrieve_baseline_a,
    retrieve_baseline_b,
    retrieve_baseline_c,
    write_retrieval_report,
)
from codemedic.retrieval.schemas import RetrievalBaseline, RetrievalResult


def _retrieval_repo(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "math_helpers.py").write_text(
        "def factorial(n: int) -> int:\n"
        "    result = 1\n"
        "    return resut\n",
        encoding="utf-8",
    )
    (src / "consumer.py").write_text(
        "from src.math_helpers import factorial\n\n"
        "def run() -> int:\n"
        "    return factorial(3)\n",
        encoding="utf-8",
    )
    (src / "unrelated.py").write_text(
        "def unrelated() -> str:\n    return 'ok'\n",
        encoding="utf-8",
    )
    return tmp_path


def _task(repository_path) -> RepairTask:
    return RepairTask(
        task_id="retrieval-demo",
        repository_path=repository_path,
        issue="factorial raises NameError because resut is undefined",
        error_log="NameError: name 'resut' is not defined",
        allowed_files=["src/math_helpers.py"],
        expected_files=["src/math_helpers.py"],
        expected_evidence=[
            {
                "file_path": "src/math_helpers.py",
                "line_start": 3,
                "line_end": 3,
                "excerpt": "return resut",
            }
        ],
        test_commands=[["python", "-m", "pytest", "-q"]],
        expected_root_cause_terms=["resut"],
    )


def test_all_retrieval_baselines_share_serializable_result_contract(tmp_path) -> None:
    repository = _retrieval_repo(tmp_path)
    kwargs = {
        "repository_path": repository,
        "issue": "factorial raises NameError because resut is undefined",
        "error_log": "NameError: name 'resut' is not defined",
        "token_budget": 240,
    }

    results = [
        retrieve_baseline_a(**kwargs),
        retrieve_baseline_b(**kwargs),
        retrieve_baseline_c(**kwargs),
    ]

    assert [result.baseline for result in results] == [
        RetrievalBaseline.BASELINE_A,
        RetrievalBaseline.BASELINE_B,
        RetrievalBaseline.BASELINE_C,
    ]
    assert all(result.files for result in results)
    assert all(len(result.to_context()) <= 240 for result in results)
    assert all(
        RetrievalResult.model_validate_json(result.model_dump_json()) == result
        for result in results
    )
    assert all("src/math_helpers.py" in result.file_paths for result in results)


def test_ast_and_repo_map_add_structured_symbols_and_dependencies(tmp_path) -> None:
    repository = _retrieval_repo(tmp_path)

    ast_result = retrieve_baseline_b(
        repository,
        "factorial raises NameError because resut is undefined",
        "NameError: name 'resut' is not defined",
    )
    repo_map_result = retrieve_baseline_c(
        repository,
        "factorial raises NameError because resut is undefined",
        "NameError: name 'resut' is not defined",
    )

    assert any("factorial" in file.symbols for file in ast_result.files)
    assert any("factorial" in file.references for file in repo_map_result.files)
    assert any("src.math_helpers" in file.imports for file in repo_map_result.files)
    assert any("src/math_helpers.py" in file.dependencies for file in repo_map_result.files)


def test_retrieval_comparison_is_reproducible_and_scores_expected_files(tmp_path) -> None:
    repository = _retrieval_repo(tmp_path)
    task = _task(repository)

    report = compare_retrieval_baselines([task], token_budget=240)

    assert report.baselines == [
        RetrievalBaseline.BASELINE_A,
        RetrievalBaseline.BASELINE_B,
        RetrievalBaseline.BASELINE_C,
    ]
    assert len(report.runs) == 3
    assert all(run.correct_file for run in report.runs)
    assert all(run.evidence_valid for run in report.runs)
    assert all(run.patch_applied is None for run in report.runs)
    assert all(run.tests_passed is None for run in report.runs)
    assert all(
        report.summary[baseline.value]["patch_apply_rate"] is None
        for baseline in report.baselines
    )
    assert all(
        report.summary[baseline.value]["final_test_pass_rate"] is None
        for baseline in report.baselines
    )
    repeated = compare_retrieval_baselines([task], token_budget=240)
    def stable_fields(item):
        return (
            item.baseline,
            item.task_id,
            item.correct_file,
            item.evidence_valid,
            item.evidence_line_accuracy,
            tuple(item.selected_files),
        )
    assert [stable_fields(item) for item in report.runs] == [
        stable_fields(item) for item in repeated.runs
    ]

    paths = write_retrieval_report(report, tmp_path / "report")
    assert set(paths) == {"summary", "runs", "report"}
    assert all(path.exists() for path in paths.values())
    assert "baseline_a" in paths["summary"].read_text(encoding="utf-8")
    assert "Patch Apply" in paths["report"].read_text(encoding="utf-8")
    assert len(paths["runs"].read_text(encoding="utf-8").splitlines()) == 3
