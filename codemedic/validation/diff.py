"""Diff validation — verifies Unified Diff patches against security rules."""

from __future__ import annotations

import re
from typing import Any

DiffValidationResult = dict[str, Any]
"""Result of diff validation:
  valid: bool
  errors: list[str]
  modified_files: list[str]
  violations: list[str]
"""


def _check_markdown_fences(lines: list[str]) -> list[str]:
    """Check for residual Markdown code-fence markers in diff content.

    After the parser strips outer fences, any remaining triple-backtick
    line inside the diff body makes the patch invalid.
    """
    errors: list[str] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "```":
            errors.append(
                f"PATCH_CONTAINS_MARKDOWN_FENCE at line {i + 1}"
            )
        elif stripped.startswith("```") and not (
            stripped.startswith("--- a/") or stripped.startswith("+++ b/")
        ):
            errors.append(
                f"PATCH_CONTAINS_MARKDOWN_FENCE at line {i + 1}"
            )
    return errors


def _check_hunk_headers(lines: list[str]) -> list[str]:
    """Validate that each hunk header has a correct old/new line count.

    A valid hunk header: @@ -old_start,old_count +new_start,new_count @@
    The parser checks that the header can be parsed and that the ranges
    are non-negative integers.
    """
    hunk_pattern = re.compile(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@")
    errors: list[str] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("@@"):
            continue
        match = hunk_pattern.match(stripped)
        if not match:
            errors.append(f"INVALID_HUNK_HEADER at line {i + 1}: {stripped[:60]!r}")
            continue
        # Ensure both old and new start line are >= 0
        old_start, old_count_str, new_start, new_count_str = match.groups()
        old_start = int(old_start)
        new_start = int(new_start)
        if old_start < 0 or new_start < 0:
            errors.append(
                f"NEGATIVE_HUNK_RANGE at line {i + 1}: {stripped[:60]!r}"
            )
    return errors


def validate_diff(
    unified_diff: str,
    *,
    allowed_files: list[str] | None = None,
    max_files: int = 3,
) -> DiffValidationResult:
    """Validate a Unified Diff patch against security rules.

    Args:
        unified_diff: The Unified Diff string.
        allowed_files: List of file paths the patch is allowed to modify.
            If None, allows all files (except system/protected paths).
        max_files: Maximum number of files the diff can modify (default 3).

    Returns:
        DiffValidationResult with valid flag, errors, and modified_files.
    """
    errors: list[str] = []
    lines = unified_diff.splitlines()

    # Check diff is not empty
    if not unified_diff.strip():
        errors.append("Diff is empty")
        return {"valid": False, "errors": errors, "modified_files": [], "violations": []}

    # Check for valid diff headers
    has_a = any(line.startswith("--- a/") for line in lines)
    has_b = any(line.startswith("+++ b/") for line in lines)
    if not has_a or not has_b:
        errors.append("Diff must have '--- a/' and '+++ b/' headers")
        return {"valid": False, "errors": errors, "modified_files": [], "violations": []}

    # Check for residual Markdown fences inside the diff body
    fence_errors = _check_markdown_fences(lines)
    errors.extend(fence_errors)

    # Validate hunk header structure
    hunk_errors = _check_hunk_headers(lines)
    errors.extend(hunk_errors)

    if errors:
        return {"valid": False, "errors": errors, "modified_files": [], "violations": []}

    # Extract modified files from diff headers
    modified = _extract_diff_files(lines)

    # Check for max files
    if len(modified) > max_files:
        errors.append(f"Diff modifies {len(modified)} files, max allowed is {max_files}")
        return {"valid": False, "errors": errors, "modified_files": modified, "violations": []}

    violations: list[str] = []

    for fpath in modified:
        # Reject absolute paths
        if fpath.startswith("/") or fpath.startswith("\\"):
            violations.append(f"Absolute path not allowed: {fpath}")
            continue

        # Reject path traversal
        if ".." in fpath.split("/"):
            violations.append(f"Path traversal detected: {fpath}")
            continue

        # Reject /dev/null (file creation/deletion)
        if "/dev/null" in fpath:
            violations.append(f"File creation/deletion not allowed: {fpath}")
            continue

        # Reject .git directory changes
        if ".git/" in fpath:
            violations.append(f".git directory change not allowed: {fpath}")
            continue

        # Check against allowed_files
        if allowed_files is not None and fpath not in allowed_files:
            violations.append(
                f"File not in allowed list: {fpath} "
                f"(allowed: {', '.join(allowed_files)})"
            )

    if violations:
        errors.extend(violations)

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "modified_files": modified,
        "violations": violations,
    }


def check_declared_files_match_diff(
    declared_files: list[str],
    unified_diff: str,
) -> list[str]:
    """Check that the declared modified_files match the actual diff content.

    Returns a list of mismatch descriptions (empty if all match).
    """
    diff_files = _extract_diff_files(unified_diff.splitlines())
    declared_set = set(declared_files)
    diff_set = set(diff_files)

    mismatches: list[str] = []

    extra_declared = declared_set - diff_set
    if extra_declared:
        mismatches.append(
            f"Declared but not in diff: {', '.join(sorted(extra_declared))}"
        )

    extra_diff = diff_set - declared_set
    if extra_diff:
        mismatches.append(
            f"In diff but not declared: {', '.join(sorted(extra_diff))}"
        )

    return mismatches


def _extract_diff_files(lines: list[str]) -> list[str]:
    """Extract modified file paths from diff header lines.

    Handles both `--- a/path` and `+++ b/path` formats.
    Only counts files that actually have changes (both sides present).
    """
    seen = set()
    files: list[str] = []

    for i, line in enumerate(lines):
        if line.startswith("+++ b/"):
            path = line[6:].strip()
            # Remove trailing timestamp if present (git diff format)
            path = re.sub(r"\s+\d{4}-\d{2}-\d{2}.*", "", path)
            # Check there's a corresponding --- a/ nearby
            if i > 0 and lines[i - 1].startswith("--- a/"):
                if path not in seen:
                    seen.add(path)
                    files.append(path)

    return files
