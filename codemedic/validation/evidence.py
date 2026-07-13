"""Evidence validation — verifies diagnosis evidence against the actual filesystem.

Ensures that evidence paths, line numbers, and excerpts are consistent
with the actual repository contents.
"""

from __future__ import annotations

from typing import Any

from codemedic.schemas.diagnosis import DiagnosisResult, Evidence
from codemedic.tools.context import RepositoryContext

ValidationResult = dict[str, Any]
"""Result of evidence validation:
  valid: bool
  errors: list[str]
  validated_evidence: list[Evidence]
"""


def validate_evidence(
    diagnosis: DiagnosisResult,
    ctx: RepositoryContext,
) -> ValidationResult:
    """Validate all evidence in a diagnosis against the repository.

    Checks:
      - Each evidence file_path exists in the repository
      - Each evidence line_start is within the file
      - File paths are relative (not absolute)
      - suspected_files are consistent with evidence

    Args:
        diagnosis: The diagnosis to validate.
        ctx: RepositoryContext for the target repository.

    Returns:
        ValidationResult with valid flag, errors, and validated evidence.
    """
    errors: list[str] = []
    validated: list[Evidence] = []

    for ev in diagnosis.evidence:
        issues = _validate_single_evidence(ev, ctx)
        if issues:
            for issue in issues:
                errors.append(f"[{ev.file_path}] {issue}")
        else:
            validated.append(ev)

    # Check suspected_files consistency
    if diagnosis.suspected_files:
        evidence_files = {e.file_path for e in diagnosis.evidence}
        missing = [f for f in diagnosis.suspected_files if f not in evidence_files]
        if missing:
            errors.append(
                f"suspected_files without evidence: {', '.join(missing)}"
            )

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "validated_evidence": validated,
    }


def _validate_single_evidence(
    ev: Evidence, ctx: RepositoryContext
) -> list[str]:
    """Validate a single evidence entry. Returns a list of error messages."""
    issues: list[str] = []

    # Check file path is relative (not absolute)
    if ev.file_path.startswith("/") or ev.file_path.startswith("\\"):
        issues.append("Absolute file path is not allowed")
        return issues

    # Check file path doesn't contain traversal
    if ".." in ev.file_path.split("/") or ".." in ev.file_path.split("\\"):
        issues.append("Path traversal detected")
        return issues

    # Check file exists in the repository
    resolved = ctx.resolve_path(ev.file_path)
    if resolved is None:
        issues.append("File is not within the repository")
        return issues

    if not resolved.exists():
        issues.append("File not found")
        return issues

    if resolved.is_dir():
        issues.append("Path is a directory, not a file")
        return issues

    # Check line numbers are valid
    if ev.line_start is not None:
        try:
            lines = resolved.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            issues.append("Could not read file to verify line numbers")
            return issues

        if ev.line_start < 1:
            issues.append(f"Line {ev.line_start} is before file start")
        elif ev.line_start > len(lines):
            issues.append(
                f"Line {ev.line_start} exceeds file length ({len(lines)} lines)"
            )

        if ev.line_end is not None:
            if ev.line_end < ev.line_start:
                issues.append(
                    f"line_end ({ev.line_end}) < line_start ({ev.line_start})"
                )
            elif ev.line_end > len(lines):
                issues.append(
                    f"Line {ev.line_end} exceeds file length ({len(lines)} lines)"
                )

        # Verify excerpt matches actual content (approximate)
        if ev.excerpt and ev.line_start and ev.line_start <= len(lines):
            actual = lines[ev.line_start - 1].strip()[:100]
            excerpt_clean = ev.excerpt.strip()[:100]
            if excerpt_clean and actual and excerpt_clean not in actual:
                issues.append(
                    f"Excerpt does not match line {ev.line_start} content"
                )

    return issues
