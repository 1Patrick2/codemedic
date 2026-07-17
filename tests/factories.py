"""Shared fixtures for the checked-in Demo repository."""

from __future__ import annotations

from pathlib import Path

from codemedic.schemas.diagnosis import DiagnosisResult, Evidence

DEMO_REPO = Path(__file__).resolve().parent.parent / "demo_repos" / "sample_project"


def find_line_number(path: Path, text: str) -> int:
    """Find the current source line containing text in the Demo repository."""
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if text in line:
            return index
    raise AssertionError(f"Text not found in {path}: {text}")


def build_demo_evidence() -> list[Evidence]:
    """Return Evidence matching the current Demo source lines."""
    math_path = DEMO_REPO / "src/utils/math_helpers.py"
    data_path = DEMO_REPO / "src/services/data_service.py"
    factorial_start = find_line_number(math_path, "resut = 1")
    retries_line = find_line_number(data_path, "max_retries: int")
    weighted_line = find_line_number(data_path, "zip(values, weight)")

    return [
        Evidence(
            file_path="src/utils/math_helpers.py",
            line_start=factorial_start,
            line_end=factorial_start,
            excerpt="resut = 1",
            reason="Variable typo breaks factorial",
        ),
        Evidence(
            file_path="src/services/data_service.py",
            line_start=retries_line,
            line_end=retries_line,
            excerpt='max_retries: int = "three"',
            reason="Config default violates its annotation",
        ),
        Evidence(
            file_path="src/services/data_service.py",
            line_start=weighted_line,
            line_end=weighted_line,
            excerpt="zip(values, weight)",
            reason="Undefined weight variable causes NameError",
        ),
    ]


def build_demo_diagnosis(
    *,
    confidence: float = 0.85,
    evidence: list[Evidence] | None = None,
    suspected_files: list[str] | None = None,
) -> DiagnosisResult:
    """Build a diagnosis backed by the current Demo repository."""
    selected_evidence = evidence if evidence is not None else build_demo_evidence()
    files = (
        suspected_files
        if suspected_files is not None
        else sorted({item.file_path for item in selected_evidence})
    )
    return DiagnosisResult(
        suspected_files=files,
        root_cause="Several deliberate Demo bugs cause test failures",
        evidence=selected_evidence,
        confidence=confidence,
        missing_information=[],
    )
