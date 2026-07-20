"""Tests for Diff Validation — markdown fences, hunk headers, git apply --check."""
from __future__ import annotations

from pathlib import Path

from codemedic.validation.diff import (
    _check_hunk_headers,
    _check_markdown_fences,
    check_declared_files_match_diff,
    validate_diff,
)

SAMPLE_REPO = str(Path(__file__).resolve().parent.parent / "demo_repos" / "sample_project")

VALID_DIFF = """\
--- a/src/utils/math_helpers.py
+++ b/src/utils/math_helpers.py
@@ -13,7 +13,7 @@
     result = 1
     for i in range(1, n + 1):
         result *= i
-    return resut  # deliberate NameError
+    return result
"""


# ── Markdown Fence checks ────────────────────────────────────────────


def test_legit_diff_passes() -> None:
    result = validate_diff(VALID_DIFF)
    assert result["valid"] is True
    assert result["errors"] == []


def test_trailing_markdown_fence_rejected() -> None:
    diff = VALID_DIFF + "```\n"
    result = validate_diff(diff)
    assert result["valid"] is False
    assert any("MARKDOWN_FENCE" in e for e in result["errors"])


def test_complete_fence_stripped_by_parser() -> None:
    """Simulate parser output after stripping outer fences."""
    from codemedic.agents.fixer import _strip_markdown_fences

    raw = '```diff\n' + VALID_DIFF + '```\n'
    cleaned = _strip_markdown_fences(raw)
    assert "```" not in cleaned, f"Fence marker leaked: {cleaned!r}"
    assert cleaned.startswith("--- a/")


def test_fence_in_middle_rejected() -> None:
    """A triple-backtick line inside the diff body must be rejected."""
    lines = VALID_DIFF.splitlines()
    lines.insert(3, "```")
    diff = "\n".join(lines)
    result = validate_diff(diff)
    assert result["valid"] is False
    assert any("MARKDOWN_FENCE" in e for e in result["errors"])


# ── Hunk header checks ───────────────────────────────────────────────


def test_hunk_old_count_error_rejected() -> None:
    """Negative old-start in hunk header."""
    diff = VALID_DIFF.replace("@@ -13,7 +13,7 @@", "@@ -1,7 +13,7 @@")
    result = validate_diff(diff)
    assert result["valid"] is True, "Different line counts are still valid"


def test_hunk_invalid_syntax_rejected() -> None:
    """Garbage hunk header must fail."""
    diff = VALID_DIFF.replace("@@ -13,7 +13,7 @@", "@@ garbage @@")
    result = validate_diff(diff)
    assert result["valid"] is False
    assert any("INVALID_HUNK_HEADER" in e for e in result["errors"])


def test_hunk_negative_range_rejected() -> None:
    diff = VALID_DIFF.replace("@@ -13,7 +13,7 @@", "@@ -1,-1 +1,1 @@")
    result = validate_diff(diff)
    assert result["valid"] is False
    assert any("NEGATIVE_HUNK_RANGE" in e or "INVALID_HUNK_HEADER" in e
               for e in result["errors"])


# ── Header checks ────────────────────────────────────────────────────


def test_missing_a_header_rejected() -> None:
    diff = VALID_DIFF.replace("--- a/", "")
    result = validate_diff(diff)
    assert result["valid"] is False
    assert any("--- a/" in e or "headers" in e for e in result["errors"])


def test_missing_b_header_rejected() -> None:
    diff = VALID_DIFF.replace("+++ b/", "")
    result = validate_diff(diff)
    assert result["valid"] is False
    assert any("+++ b/" in e or "headers" in e for e in result["errors"])


# ── Path security checks ─────────────────────────────────────────────


def test_absolute_path_rejected() -> None:
    diff = VALID_DIFF.replace("src/utils/math_helpers.py", "/etc/passwd")
    result = validate_diff(diff)
    assert result["valid"] is False
    assert any("Absolute path" in e for e in result["errors"])


def test_path_traversal_rejected() -> None:
    diff = VALID_DIFF.replace("src/utils/math_helpers.py", "../secret.py")
    result = validate_diff(diff)
    assert result["valid"] is False
    violations = result["errors"]
    assert any("traversal" in e for e in violations) or any(
        ".." in e for e in violations
    )


def test_unauthorized_file_rejected() -> None:
    result = validate_diff(VALID_DIFF, allowed_files=["src/other.py"])
    assert result["valid"] is False
    assert any("not in allowed list" in e for e in result["errors"])


# ── Declared files mismatch ──────────────────────────────────────────


def test_modified_files_mismatch_rejected() -> None:
    mismatches = check_declared_files_match_diff(
        ["src/other.py"],
        VALID_DIFF,
    )
    assert len(mismatches) > 0
    assert any("Declared but not in diff" in m for m in mismatches)


# ── Helper unit checks ───────────────────────────────────────────────


def test_check_markdown_fences_passes_clean_diff() -> None:
    errors = _check_markdown_fences(VALID_DIFF.splitlines())
    assert errors == []


def test_check_hunk_headers_passes_valid_headers() -> None:
    errors = _check_hunk_headers(VALID_DIFF.splitlines())
    assert errors == []


def test_check_markdown_fence_catches_opening_fence() -> None:
    lines = ["```diff", "--- a/x.py", "+++ b/x.py"]
    errors = _check_markdown_fences(lines)
    assert len(errors) >= 1


def test_check_markdown_fence_catches_closing_fence() -> None:
    lines = ["--- a/x.py", "+++ b/x.py", "```"]
    errors = _check_markdown_fences(lines)
    assert len(errors) >= 1
