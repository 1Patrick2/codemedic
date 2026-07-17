"""Small deterministic retrieval baselines used by Stage 4 experiments."""

from __future__ import annotations

import ast
import json
import re
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from codemedic.evaluation.schemas import RepairTask
from codemedic.retrieval.schemas import (
    RetrievalBaseline,
    RetrievalComparisonReport,
    RetrievalResult,
    RetrievalRun,
    RetrievedFile,
)

_TERM_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{1,}")


def _repository_files(repository_path: str | Path) -> list[Path]:
    root = Path(repository_path).resolve()
    files: list[Path] = []
    for path in sorted(root.rglob("*.py")):
        relative_parts = path.relative_to(root).parts
        if any(part.startswith(".") or part == "__pycache__" for part in relative_parts):
            continue
        if path.is_file():
            files.append(path)
    return files


def _query_terms(issue: str, error_log: str | None) -> list[str]:
    terms: list[str] = []
    for text in (issue, error_log or ""):
        for term in _TERM_PATTERN.findall(text.lower()):
            if term not in terms:
                terms.append(term)
    return terms


def _read_files(repository_path: str | Path) -> list[tuple[str, str]]:
    root = Path(repository_path).resolve()
    result: list[tuple[str, str]] = []
    for path in _repository_files(root):
        try:
            result.append((path.relative_to(root).as_posix(), path.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError):
            continue
    return result


def _score(path: str, content: str, terms: Iterable[str]) -> float:
    lowered_path = path.lower()
    lowered_content = content.lower()
    return float(
        sum(lowered_path.count(term) * 4 + lowered_content.count(term) for term in terms)
    )


def _select_files(files: list[RetrievedFile], token_budget: int) -> list[RetrievedFile]:
    selected: list[RetrievedFile] = []
    used = 0
    for item in files:
        remaining = token_budget - used
        if remaining <= 0:
            break
        header_size = len(f"--- {item.path} ---\n")
        separator_size = 2 if selected else 0
        content_limit = max(0, remaining - header_size)
        if content_limit == 0:
            break
        selected.append(item.model_copy(update={"content": item.content[:content_limit]}))
        used += separator_size + header_size + min(len(item.content), content_limit)
    return selected


def _render_context(files: list[RetrievedFile]) -> str:
    return "\n\n".join(f"--- {item.path} ---\n{item.content}" for item in files)


def _result(
    baseline: RetrievalBaseline,
    files: list[RetrievedFile],
    terms: list[str],
    *,
    tool_calls: int,
    token_budget: int,
    started: float,
) -> RetrievalResult:
    selected = _select_files(files, token_budget)
    context = _render_context(selected)
    if len(context) > token_budget and selected:
        overflow = len(context) - token_budget
        last = selected[-1]
        selected[-1] = last.model_copy(
            update={"content": last.content[: max(0, len(last.content) - overflow)]}
        )
        context = _render_context(selected)
    return RetrievalResult(
        baseline=baseline,
        files=selected,
        query_terms=terms,
        tool_calls=tool_calls,
        token_estimate=len(context),
        latency_ms=(time.perf_counter() - started) * 1000,
    )


def retrieve_baseline_a(
    repository_path: str | Path,
    issue: str,
    error_log: str | None = None,
    *,
    token_budget: int = 12000,
) -> RetrievalResult:
    """Baseline A: file tree, keyword matching, and error-log matching."""
    started = time.perf_counter()
    terms = _query_terms(issue, error_log)
    files = [
        RetrievedFile(path=path, content=content, score=_score(path, content, terms))
        for path, content in _read_files(repository_path)
    ]
    files.sort(key=lambda item: (-item.score, item.path))
    return _result(
        RetrievalBaseline.BASELINE_A,
        files,
        terms,
        tool_calls=2,
        token_budget=token_budget,
        started=started,
    )


def _ast_metadata(content: str) -> tuple[list[str], list[str], list[str]]:
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return [], [], []
    symbols: list[str] = []
    references: list[str] = []
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.append(node.name)
        elif isinstance(node, ast.Name):
            references.append(node.id)
        elif isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    return sorted(set(symbols)), sorted(set(references)), sorted(set(imports))


def _retrieve_ast(
    repository_path: str | Path,
    issue: str,
    error_log: str | None,
    *,
    baseline: RetrievalBaseline,
    token_budget: int,
    include_repo_map: bool = False,
) -> RetrievalResult:
    started = time.perf_counter()
    terms = _query_terms(issue, error_log)
    source_files = _read_files(repository_path)
    module_to_path = {
        path[:-3].replace("/", "."): path
        for path, _ in source_files
        if path.endswith(".py")
    }
    files: list[RetrievedFile] = []
    for path, content in source_files:
        symbols, references, imports = _ast_metadata(content)
        score = _score(path, content, terms)
        score += sum(3 for symbol in symbols if symbol.lower() in terms)
        score += sum(2 for reference in references if reference.lower() in terms)
        dependencies = []
        if include_repo_map:
            dependencies = sorted(
                {
                    dependency_path
                    for imported_module in imports
                    for module_name, dependency_path in module_to_path.items()
                    if imported_module == module_name
                    or imported_module.startswith(f"{module_name}.")
                }
            )
            score += sum(2 for dependency in dependencies if dependency)
        files.append(
            RetrievedFile(
                path=path,
                content=content,
                score=score,
                symbols=symbols,
                references=references,
                imports=imports,
                dependencies=dependencies,
            )
        )
    files.sort(key=lambda item: (-item.score, item.path))
    return _result(
        baseline,
        files,
        terms,
        tool_calls=3,
        token_budget=token_budget,
        started=started,
    )


def retrieve_baseline_b(
    repository_path: str | Path,
    issue: str,
    error_log: str | None = None,
    *,
    token_budget: int = 12000,
) -> RetrievalResult:
    """Baseline B: Baseline A plus Python AST symbol metadata."""
    return _retrieve_ast(
        repository_path,
        issue,
        error_log,
        baseline=RetrievalBaseline.BASELINE_B,
        token_budget=token_budget,
    )


def retrieve_baseline_c(
    repository_path: str | Path,
    issue: str,
    error_log: str | None = None,
    *,
    token_budget: int = 12000,
) -> RetrievalResult:
    """Baseline C: Baseline B plus lightweight symbol/reference/import metadata."""
    return _retrieve_ast(
        repository_path,
        issue,
        error_log,
        baseline=RetrievalBaseline.BASELINE_C,
        token_budget=token_budget,
        include_repo_map=True,
    )


def _evidence_metrics(result: RetrievalResult, task: RepairTask) -> tuple[bool, bool | None]:
    selected = {item.path: item.content for item in result.files}
    expected_files = {item.file_path for item in task.expected_evidence}
    evidence_valid = not expected_files or expected_files <= selected.keys()
    if not task.expected_evidence:
        return evidence_valid, None

    line_matches: list[bool] = []
    for evidence in task.expected_evidence:
        content = selected.get(evidence.file_path, "")
        lines = content.splitlines()
        if evidence.line_start is None or evidence.line_start > len(lines):
            line_matches.append(False)
            continue
        line = lines[evidence.line_start - 1]
        line_matches.append(
            evidence.excerpt.strip() in line and (
                evidence.line_end is None or evidence.line_end == evidence.line_start
            )
        )
    return evidence_valid, all(line_matches)


def compare_retrieval_baselines(
    tasks: Iterable[RepairTask],
    *,
    token_budget: int = 12000,
) -> RetrievalComparisonReport:
    """Run A/B/C over identical tasks and return comparable measurements."""
    retrieval_functions = {
        RetrievalBaseline.BASELINE_A: retrieve_baseline_a,
        RetrievalBaseline.BASELINE_B: retrieve_baseline_b,
        RetrievalBaseline.BASELINE_C: retrieve_baseline_c,
    }
    runs: list[RetrievalRun] = []
    for task in tasks:
        task = RepairTask.model_validate(task)
        for baseline in RetrievalBaseline:
            result = retrieval_functions[baseline](
                task.repository_path,
                task.issue,
                task.error_log,
                token_budget=token_budget,
            )
            evidence_valid, line_accuracy = _evidence_metrics(result, task)
            runs.append(
                RetrievalRun(
                    task_id=task.task_id,
                    baseline=baseline,
                    correct_file=set(task.expected_files) <= set(result.file_paths),
                    evidence_valid=evidence_valid,
                    evidence_line_accuracy=line_accuracy,
                    selected_files=result.file_paths,
                    tool_calls=result.tool_calls,
                    token_estimate=result.token_estimate,
                    latency_ms=result.latency_ms,
                )
            )

    summary: dict[str, dict[str, Any]] = {}

    def optional_rate(values: Iterable[bool | None]) -> float | None:
        known = [value for value in values if value is not None]
        return sum(known) / len(known) if known else None

    for baseline in RetrievalBaseline:
        baseline_runs = [run for run in runs if run.baseline == baseline]
        count = len(baseline_runs)
        summary[baseline.value] = {
            "correct_file_rate": sum(run.correct_file for run in baseline_runs) / count
            if count
            else 0.0,
            "evidence_valid_rate": sum(run.evidence_valid for run in baseline_runs) / count
            if count
            else 0.0,
            "evidence_line_accuracy_rate": sum(
                run.evidence_line_accuracy is True for run in baseline_runs
            )
            / count
            if count
            else 0.0,
            "patch_apply_rate": optional_rate(
                run.patch_applied for run in baseline_runs
            ),
            "final_test_pass_rate": optional_rate(
                run.tests_passed for run in baseline_runs
            ),
            "average_tool_calls": sum(run.tool_calls for run in baseline_runs) / count
            if count
            else 0.0,
            "average_tokens": sum(run.token_estimate for run in baseline_runs) / count
            if count
            else 0.0,
            "average_latency_ms": sum(run.latency_ms for run in baseline_runs) / count
            if count
            else 0.0,
        }
    return RetrievalComparisonReport(
        baselines=list(RetrievalBaseline),
        runs=runs,
        summary=summary,
    )


def write_retrieval_report(
    report: RetrievalComparisonReport,
    output_dir: str | Path,
) -> dict[str, Path]:
    """Persist a comparison in the same small, reviewable formats as Evaluation."""
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    summary_path = destination / "summary.json"
    runs_path = destination / "runs.jsonl"
    markdown_path = destination / "report.md"

    summary_path.write_text(
        json.dumps(report.summary, indent=2, sort_keys=True), encoding="utf-8"
    )
    runs_path.write_text(
        "".join(f"{run.model_dump_json()}\n" for run in report.runs),
        encoding="utf-8",
    )
    lines = [
        "# Retrieval Baseline Comparison",
        "",
        "| Baseline | Correct File | Evidence Valid | Evidence Line | Patch Apply | "
        "Final Test | Tool Calls | Tokens | Latency (ms) |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]

    def display_rate(value: float | None) -> str:
        return f"{value:.3f}" if value is not None else "n/a"

    for baseline in report.baselines:
        metrics = report.summary[baseline.value]
        lines.append(
            "| {baseline} | {correct:.3f} | {evidence:.3f} | {line:.3f} | "
            "{patch} | {tests} | {tools:.2f} | {tokens:.2f} | {latency:.2f} |".format(
                baseline=baseline.value,
                correct=metrics["correct_file_rate"],
                evidence=metrics["evidence_valid_rate"],
                line=metrics["evidence_line_accuracy_rate"],
                patch=display_rate(metrics["patch_apply_rate"]),
                tests=display_rate(metrics["final_test_pass_rate"]),
                tools=metrics["average_tool_calls"],
                tokens=metrics["average_tokens"],
                latency=metrics["average_latency_ms"],
            )
        )
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "summary": summary_path,
        "runs": runs_path,
        "report": markdown_path,
    }
