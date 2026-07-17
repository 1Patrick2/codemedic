"""Direct tests for filesystem-backed Evidence validation."""

from __future__ import annotations

from pathlib import Path

from codemedic.schemas.diagnosis import DiagnosisResult, Evidence
from codemedic.tools.context import RepositoryContext
from codemedic.validation.evidence import validate_approved_files, validate_evidence
from tests.factories import build_demo_diagnosis

DEMO_REPO = Path(__file__).resolve().parent.parent / "demo_repos" / "sample_project"


def test_validate_evidence_accepts_current_demo_lines() -> None:
    result = validate_evidence(build_demo_diagnosis(), RepositoryContext(DEMO_REPO))

    assert result["valid"] is True
    assert len(result["validated_evidence"]) == 3


def test_validate_evidence_rejects_wrong_line_excerpt() -> None:
    diagnosis = DiagnosisResult(
        suspected_files=["src/utils/math_helpers.py"],
        root_cause="bad evidence",
        evidence=[
            Evidence(
                file_path="src/utils/math_helpers.py",
                line_start=40,
                excerpt="return result",
                reason="wrong excerpt",
            )
        ],
        confidence=0.8,
        missing_information=[],
    )

    result = validate_evidence(diagnosis, RepositoryContext(DEMO_REPO))

    assert result["valid"] is False
    assert result["validated_evidence"] == []
    assert any("Excerpt does not match" in error for error in result["errors"])


def test_validate_evidence_rejects_multi_line_ranges() -> None:
    math_path = DEMO_REPO / "src/utils/math_helpers.py"
    start = next(
        index
        for index, line in enumerate(
            math_path.read_text(encoding="utf-8").splitlines(), start=1
        )
        if "resut = 1" in line
    )
    diagnosis = DiagnosisResult(
        suspected_files=["src/utils/math_helpers.py"],
        root_cause="multi-line evidence is not precise enough",
        evidence=[
            Evidence(
                file_path="src/utils/math_helpers.py",
                line_start=start,
                line_end=start + 1,
                excerpt="resut = 1",
                reason="range should be rejected",
            )
        ],
        confidence=0.8,
        missing_information=[],
    )

    result = validate_evidence(diagnosis, RepositoryContext(DEMO_REPO))

    assert result["valid"] is False
    assert result["validated_evidence"] == []
    assert any("single line" in error.lower() for error in result["errors"])


def test_validate_evidence_rejects_traversal_and_absolute_paths() -> None:
    diagnosis = DiagnosisResult(
        suspected_files=["../outside.py", "C:\\Windows\\System32\\x.py"],
        root_cause="unsafe evidence",
        evidence=[
            Evidence(
                file_path="../outside.py",
                line_start=1,
                excerpt="x",
                reason="traversal",
            ),
            Evidence(
                file_path="C:\\Windows\\System32\\x.py",
                line_start=1,
                excerpt="x",
                reason="absolute",
            ),
        ],
        confidence=0.8,
        missing_information=[],
    )

    result = validate_evidence(diagnosis, RepositoryContext(DEMO_REPO))

    assert result["valid"] is False
    assert result["validated_evidence"] == []


def test_validate_approved_files_canonicalizes_valid_files() -> None:
    approved, errors = validate_approved_files(
        ["src/services/data_service.py", "src/services/data_service.py"],
        RepositoryContext(DEMO_REPO),
    )

    assert errors == []
    assert approved == ["src/services/data_service.py"]


def test_validate_approved_files_rejects_invalid_entries_without_partial_authorization() -> None:
    approved, errors = validate_approved_files(
        ["src/utils/math_helpers.py", "../outside.py", "src"],
        RepositoryContext(DEMO_REPO),
    )

    assert approved == []
    assert len(errors) == 2
    assert any("outside" in error.lower() or "traversal" in error.lower() for error in errors)
    assert any("directory" in error.lower() for error in errors)
