"""Real sandbox and test-runner proof for the demo repository."""

from __future__ import annotations

import difflib
import hashlib
import subprocess
import sys
from pathlib import Path

from codemedic.tools.sandbox import (
    apply_patch,
    cleanup_sandbox,
    create_temp_copy,
)
from codemedic.tools.test_runner import run_tests

DEMO_REPO = Path(__file__).resolve().parent.parent / "demo_repos" / "sample_project"
TARGET = "src/utils/math_helpers.py"
FIX_FILES = ["src/services/data_service.py", TARGET]


def _build_patch(
    relative_path: str,
    replacements: dict[str, str],
    *,
    context_lines: int = 3,
) -> str:
    """Build a real unified diff from the checked-in Demo source."""
    source = (DEMO_REPO / relative_path).read_bytes().decode("utf-8")
    fixed = source
    for old, new in replacements.items():
        fixed = fixed.replace(old, new)
    return "".join(difflib.unified_diff(
        source.splitlines(keepends=True),
        fixed.splitlines(keepends=True),
        fromfile=f"a/{relative_path}",
        tofile=f"b/{relative_path}",
        n=context_lines,
    ))


FIX_PATCH = "\n".join([
    _build_patch(
        "src/services/data_service.py",
        {
            'max_retries: int = "three"  # <-- BUG: type mismatch':
                "max_retries: int = 3",
            "zip(values, weight))  # <-- BUG: NameError":
                "zip(values, weights))",
        },
        context_lines=30,
    ),
    _build_patch(
        TARGET,
        {
            "resut = 1  # <-- BUG: typo 'resut' instead of 'result'":
                "result = 1  # <-- BUG: typo 'resut' instead of 'result'",
            "resut *= i": "result *= i",
        },
    ),
])
FIRST_RETRY_PATCH = _build_patch(
    TARGET,
    {
        "resut = 1  # <-- BUG: typo 'resut' instead of 'result'":
            "result = 1  # <-- BUG: typo 'resut' instead of 'result'",
    },
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_real_patch_apply_and_sandbox_tests() -> None:
    original_hashes = {
        path: _sha256(DEMO_REPO / path)
        for path in FIX_FILES
    }

    original_test = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=DEMO_REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert original_test.returncode != 0

    sandbox = create_temp_copy(str(DEMO_REPO))
    try:
        assert any(line.startswith(" ") for line in FIX_PATCH.splitlines())
        applied = apply_patch(sandbox, FIX_PATCH)
        assert applied["success"] is True, applied
        assert applied["returncode"] == 0
        assert applied["modified_files"] == FIX_FILES

        results = run_tests(sandbox)
        assert results
        assert all(result.returncode == 0 for result in results)
        assert all(not result.timed_out for result in results)
    finally:
        cleanup_sandbox(sandbox)

    assert {
        path: _sha256(DEMO_REPO / path)
        for path in FIX_FILES
    } == original_hashes
    assert not Path(sandbox).exists()
