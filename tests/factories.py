"""Shared fixtures for the checked-in Demo repository."""

from __future__ import annotations

from codemedic.schemas.diagnosis import DiagnosisResult, Evidence


def build_demo_evidence() -> list[Evidence]:
    """Return Evidence matching the current Demo source lines."""
    return [
        Evidence(
            file_path="src/utils/math_helpers.py",
            line_start=40,
            line_end=43,
            excerpt="resut = 1",
            reason="Variable typo breaks factorial",
        ),
        Evidence(
            file_path="src/services/data_service.py",
            line_start=23,
            line_end=23,
            excerpt='max_retries: int = "three"',
            reason="Config default violates its annotation",
        ),
        Evidence(
            file_path="src/services/data_service.py",
            line_start=44,
            line_end=44,
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
