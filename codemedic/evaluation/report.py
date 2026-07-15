"""Persist evaluation runs and produce deterministic aggregate reports."""

from __future__ import annotations

import json
from collections.abc import Iterable
from math import fsum
from pathlib import Path
from typing import Any

from codemedic.evaluation.schemas import EvaluationRunResult


def _rate(numerator: float, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def summarize_results(results: Iterable[EvaluationRunResult]) -> dict[str, Any]:
    """Build JSON-safe aggregate metrics for a collection of runs."""
    items = list(results)
    total = len(items)
    end_to_end = sum(
        1
        for result in items
        if result.diagnosis_valid
        and result.evidence_valid
        and result.correct_file
        and result.diff_valid
        and result.patch_applied
        and result.tests_passed
        and not result.unauthorized_files
        and not result.false_pass
    )
    unauthorized = sum(bool(result.unauthorized_files) for result in items)
    false_pass = sum(result.false_pass for result in items)
    return {
        "total_runs": total,
        "diagnosis_valid_rate": _rate(sum(result.diagnosis_valid for result in items), total),
        "evidence_valid_rate": _rate(sum(result.evidence_valid for result in items), total),
        "diff_valid_rate": _rate(sum(result.diff_valid for result in items), total),
        "patch_apply_rate": _rate(sum(result.patch_applied for result in items), total),
        "final_test_pass_rate": _rate(sum(result.tests_passed for result in items), total),
        "end_to_end_pass_rate": _rate(end_to_end, total),
        "unauthorized_modification_rate": _rate(unauthorized, total),
        "false_pass_rate": _rate(false_pass, total),
        "average_retries": _rate(sum(result.retries for result in items), total),
        "average_tool_calls": _rate(sum(result.tool_calls for result in items), total),
        "average_latency_ms": _rate(fsum(result.latency_ms for result in items), total),
    }


class EvaluationReportWriter:
    """Write one isolated evaluation batch and its optional trajectories."""

    def __init__(self, output_dir: str | Path) -> None:
        self.output_dir = Path(output_dir)
        self._results: list[EvaluationRunResult] = []

    def write_run(
        self,
        result: EvaluationRunResult,
        *,
        trajectory: dict[str, Any] | None = None,
    ) -> None:
        """Persist one result and its trajectory under the batch directory."""
        result = EvaluationRunResult.model_validate(result)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        with (self.output_dir / "runs.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(result.model_dump_json() + "\n")
        if trajectory is not None:
            trajectory_dir = self.output_dir / "trajectories"
            trajectory_dir.mkdir(exist_ok=True)
            (trajectory_dir / f"{result.run_id}.json").write_text(
                json.dumps(trajectory, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        self._results.append(result)

    def finalize(self) -> dict[str, Path]:
        """Write summary and Markdown report, returning all primary paths."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        summary = summarize_results(self._results)
        summary_path = self.output_dir / "summary.json"
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        report_path = self.output_dir / "report.md"
        report_path.write_text(self._markdown_report(summary), encoding="utf-8")
        return {
            "summary": summary_path,
            "runs": self.output_dir / "runs.jsonl",
            "report": report_path,
        }

    def _markdown_report(self, summary: dict[str, Any]) -> str:
        rows = [
            "# CodeMedic Evaluation Report",
            "",
            f"- Total runs: {summary['total_runs']}",
            f"- End-to-end pass rate: {summary['end_to_end_pass_rate']:.2%}",
            f"- Patch apply rate: {summary['patch_apply_rate']:.2%}",
            f"- Final test pass rate: {summary['final_test_pass_rate']:.2%}",
            f"- Unauthorized modification rate: {summary['unauthorized_modification_rate']:.2%}",
            f"- False pass rate: {summary['false_pass_rate']:.2%}",
            "",
            "## Runs",
            "",
        ]
        rows.extend(f"- `{result.task_id}` / `{result.run_id}`" for result in self._results)
        return "\n".join(rows) + "\n"
