"""Tests for the repository exploration tools.

These tests verify the 4 read-only tools using the sample_project demo repo.
They do NOT call any LLM — they test the tool functions directly.
"""

import os
import tempfile
from pathlib import Path

from codemedic.tools.repository import (
    _is_binary,
    _resolve_safe_path,
    list_repo_tree,
    parse_log,
    read_file,
    search_code,
)

DEMO_REPO = Path(__file__).resolve().parent.parent / "demo_repos" / "sample_project"


# ── Helper tests ────────────────────────────────────────────────────────────


class TestSafePath:
    def test_resolve_normal_path(self) -> None:
        repo = DEMO_REPO
        safe = _resolve_safe_path(repo, "src/utils")
        assert safe is not None
        assert safe == (repo / "src/utils").resolve()

    def test_rejects_traversal(self) -> None:
        repo = DEMO_REPO
        safe = _resolve_safe_path(repo, "../sample_project/../../etc/passwd")
        assert safe is None

    def test_rejects_absolute_traversal(self) -> None:
        repo = DEMO_REPO
        safe = _resolve_safe_path(repo, "C:\\Windows\\System32")
        assert safe is None


class TestIsBinary:
    def test_python_file_is_not_binary(self) -> None:
        assert not _is_binary(DEMO_REPO / "src/utils/math_helpers.py")

    def test_detects_binary(self) -> None:
        # Create a small file with null bytes
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
            f.write(b"\x00\x01\x02")
            tmp = f.name
        try:
            assert _is_binary(Path(tmp))
        finally:
            os.unlink(tmp)


# ── list_repo_tree tests ────────────────────────────────────────────────────


class TestListRepoTree:
    def test_lists_top_level(self) -> None:
        tree = list_repo_tree(str(DEMO_REPO))
        assert "src/" in tree
        assert "tests/" in tree
        assert "README.md" in tree
        assert "pyproject.toml" in tree

    def test_rejects_nonexistent_path(self) -> None:
        result = list_repo_tree("/nonexistent/path")
        assert "ERROR" in result

    def test_respects_max_depth(self) -> None:
        tree = list_repo_tree(str(DEMO_REPO), max_depth=1)
        # Should show top-level but not deep nesting
        assert "src/" in tree
        # Should show "..." for deeper content
        assert "...]"


# ── search_code tests ────────────────────────────────────────────────────────


class TestSearchCode:
    def test_finds_function_name(self) -> None:
        results = search_code(str(DEMO_REPO), r"def factorial")
        assert "factorial" in results
        assert "math_helpers.py" in results

    def test_finds_import(self) -> None:
        results = search_code(str(DEMO_REPO), r"from src")
        assert len(results) > 0
        assert "from src" in results

    def test_multiline_search(self) -> None:
        results = search_code(str(DEMO_REPO), r"BUG")
        assert len(results) > 0
        assert "BUG" in results

    def test_no_match(self) -> None:
        results = search_code(str(DEMO_REPO), r"XYZZY_NONEXISTENT_12345")
        assert "No matches found" in results

    def test_invalid_regex(self) -> None:
        results = search_code(str(DEMO_REPO), r"[invalid")
        assert "ERROR" in results


# ── read_file tests ──────────────────────────────────────────────────────────


class TestReadFile:
    def test_reads_python_file(self) -> None:
        content = read_file(str(DEMO_REPO), "src/utils/math_helpers.py")
        assert "factorial" in content
        assert "def factorial" in content
        # Check line numbers are present
        assert "1:" in content

    def test_rejects_path_traversal(self) -> None:
        result = read_file(str(DEMO_REPO), "../../../etc/passwd")
        assert "ERROR" in result

    def test_file_not_found(self) -> None:
        result = read_file(str(DEMO_REPO), "nonexistent.py")
        assert "ERROR" in result

    def test_rejects_binary(self) -> None:
        result = read_file(str(DEMO_REPO), ".gitignore")
        assert "ERROR" in result or ".gitignore" in result


# ── parse_log tests ─────────────────────────────────────────────────────────


class TestParseLog:
    def test_finds_errors(self) -> None:
        log = "[2024-01-01] ERROR: something broke\n[2024-01-01] INFO: continuing"
        result = parse_log(log)
        assert "ERROR" in result or "Error" in result
        assert "something broke" in result

    def test_detects_traceback(self) -> None:
        log = (
            "Traceback (most recent call last):\n"
            '  File "test.py", line 5, in foo\n'
            "    bar()\n"
            "NameError: name 'x' is not defined"
        )
        result = parse_log(log)
        assert "Traceback" in result
        assert "NameError" in result

    def test_empty_log(self) -> None:
        result = parse_log("")
        assert "No obvious errors" in result

    def test_truncates_long_log(self) -> None:
        long_log = "\n".join(f"line {i}" for i in range(500))
        result = parse_log(long_log, max_lines=50)
        assert "[truncated at 50 lines]" in result


# ── CLI help test ────────────────────────────────────────────────────────────


class TestCliHelp:
    def test_cli_imports(self) -> None:
        from codemedic.cli import main
        assert main is not None

    def test_cli_help(self) -> None:
        import subprocess
        import sys
        result = subprocess.run(
            [sys.executable, "-m", "codemedic.cli", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "investigate" in result.stdout or "investigate" in result.stderr
