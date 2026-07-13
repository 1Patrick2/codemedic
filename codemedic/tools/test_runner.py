"""Test runner — executes whitelist test commands in a sandbox."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from codemedic.config import settings
from codemedic.schemas.results import TestResult

# White-listed test commands — using sys.executable for conda compatibility
PY = sys.executable

_ALLOWED_COMMANDS: list[list[str]] = [
    [PY, "-m", "pytest"],
    [PY, "-m", "pytest", "-v"],
    [PY, "-m", "pytest", "-q"],
    [PY, "-m", "ruff", "check"],
    [PY, "-m", "mypy"],
    [PY, "-m", "pip", "check"],
]


def is_command_allowed(command: list[str]) -> bool:
    for allowed in _ALLOWED_COMMANDS:
        if command == allowed:
            return True
    return False


def run_tests(
    repo_path: str,
    commands: list[list[str]] | None = None,
    *,
    timeout: int | None = None,
) -> list[TestResult]:
    """Execute whitelist test commands in the repository.

    Args:
        repo_path: Path to the repository root (sandbox copy).
        commands: List of commands to run. None = default pytest.
        timeout: Timeout per command in seconds. Defaults to config value.

    Returns:
        List of TestResult objects.
    """
    repo = Path(repo_path).resolve()
    if not repo.is_dir():
        return [TestResult(
            command_id="init",
            argv=["error"],
            returncode=-1,
            stderr=f"Repository not found: {repo_path}",
        )]

    timeout_s = timeout or settings.test_timeout_seconds
    max_output = settings.max_output_length
    results: list[TestResult] = []

    cmd_list = commands or [[PY, "-m", "pytest", "-q"]]

    for cmd_id, cmd in enumerate(cmd_list):
        if not is_command_allowed(cmd):
            results.append(TestResult(
                command_id=f"cmd_{cmd_id}",
                argv=list(cmd),
                returncode=-1,
                stderr=f"Command not in whitelist: {' '.join(cmd)}",
            ))
            continue

        start_time = time.perf_counter()
        timed_out = False

        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, cwd=repo,
                timeout=timeout_s,
            )
            returncode = proc.returncode
            raw_stdout = proc.stdout or ""
            raw_stderr = proc.stderr or ""
        except subprocess.TimeoutExpired:
            returncode = -1
            raw_stdout = ""
            raw_stderr = f"Command timed out after {timeout_s}s"
            timed_out = True
        except FileNotFoundError as exc:
            returncode = -1
            raw_stdout = ""
            raw_stderr = str(exc)

        duration_ms = int((time.perf_counter() - start_time) * 1000)
        truncated = len(raw_stdout) > max_output or len(raw_stderr) > max_output

        results.append(TestResult(
            command_id=f"cmd_{cmd_id}",
            argv=list(cmd),
            returncode=returncode,
            stdout=raw_stdout[:max_output],
            stderr=raw_stderr[:max_output],
            timed_out=timed_out,
            duration_ms=duration_ms,
            output_truncated=truncated,
        ))

    return results
