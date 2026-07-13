"""Test runner — executes whitelist test commands in a sandbox."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from codemedic.config import settings

# White-listed test commands
ALLOWED_COMMANDS: list[list[str]] = [
    ["python", "-m", "pytest"],
    ["python", "-m", "pytest", "-v"],
    ["python", "-m", "pytest", "-q"],
    ["python", "-m", "ruff", "check"],
    ["python", "-m", "mypy"],
    ["python", "-m", "pip", "check"],
]


def is_command_allowed(command: list[str]) -> bool:
    """Check if a command is in the allowed list.

    Args:
        command: The command as a list of strings (subprocess style).

    Returns:
        True if the command is allowed.
    """
    for allowed in ALLOWED_COMMANDS:
        if command == allowed:
            return True
    return False


def run_tests(
    repo_path: str,
    commands: list[list[str]] | None = None,
    *,
    timeout: int | None = None,
) -> list[dict[str, Any]]:
    """Execute whitelist test commands in the repository.

    Args:
        repo_path: Path to the repository root (sandbox copy).
        commands: List of commands to run. Each command is a list of strings.
            If None, runs a default pytest.
        timeout: Timeout per command in seconds. Defaults to config value.

    Returns:
        List of result dicts, each with:
          command: str
          returncode: int
          stdout: str (truncated)
          stderr: str (truncated)
          timed_out: bool
    """
    repo = Path(repo_path).resolve()
    if not repo.is_dir():
        return [{
            "command": str(commands),
            "returncode": -1,
            "stdout": "",
            "stderr": f"Repository not found: {repo_path}",
            "timed_out": False,
        }]

    timeout_s = timeout or settings.test_timeout_seconds
    max_output = settings.max_output_length
    results: list[dict[str, Any]] = []

    cmd_list = commands or [["python", "-m", "pytest", "-q"]]

    for cmd in cmd_list:
        if not is_command_allowed(cmd):
            results.append({
                "command": " ".join(cmd),
                "returncode": -1,
                "stdout": "",
                "stderr": f"Command not in whitelist: {' '.join(cmd)}",
                "timed_out": False,
            })
            continue

        timed_out = False
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=repo,
                timeout=timeout_s,
            )
            returncode = proc.returncode
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired:
            returncode = -1
            stdout = ""
            stderr = f"Command timed out after {timeout_s}s"
            timed_out = True
        except FileNotFoundError as exc:
            returncode = -1
            stdout = ""
            stderr = str(exc)
            timed_out = False

        results.append({
            "command": " ".join(cmd),
            "returncode": returncode,
            "stdout": stdout[:max_output],
            "stderr": stderr[:max_output],
            "timed_out": timed_out,
        })

    return results
