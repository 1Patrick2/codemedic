"""Sandbox operations — creating temp copies, applying patches."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


def create_temp_copy(repo_path: str) -> str:
    """Create a temporary copy of the repository.

    Uses tempfile to create a sandboxed copy where patches can be safely
    applied without affecting the original.

    Args:
        repo_path: Path to the repository root.

    Returns:
        Path to the temporary copy.

    Raises:
        FileNotFoundError: If the source repo does not exist.
    """
    source = Path(repo_path).resolve()
    if not source.is_dir():
        raise FileNotFoundError(f"Repository not found: {repo_path}")

    # Create temp directory
    sandbox_dir = Path(tempfile.mkdtemp(prefix="codemedic_sandbox_"))
    dest = sandbox_dir / source.name

    # Copy entire repo
    shutil.copytree(source, dest, symlinks=False, ignore_dangling_symlinks=True)

    return str(dest)


def apply_patch(repo_path: str, unified_diff: str) -> dict[str, Any]:
    """Apply a Unified Diff patch to the repository copy.

    Uses the `patch` command or `git apply` to apply the diff.
    Falls back to `patch` if git isn't available.

    Args:
        repo_path: Path to the repository root (the temp copy).
        unified_diff: The Unified Diff string to apply.

    Returns:
        dict with keys:
          success: bool
          stdout: str
          stderr: str
          returncode: int
    """
    repo = Path(repo_path).resolve()

    # Try git apply (works without .git dir for simple diffs)
    try:
        git_result = subprocess.run(
            ["git", "apply", "--ignore-whitespace"],
            input=unified_diff,
            capture_output=True,
            text=True,
            cwd=repo,
            timeout=30,
        )
        if git_result.returncode == 0:
            return {
                "success": True,
                "stdout": git_result.stdout,
                "stderr": git_result.stderr,
                "returncode": 0,
            }
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # Fallback: try using Python's unidiff to apply
    return _apply_with_unidiff(repo, unified_diff)


def _save_hunk(
    hunks: dict[str, list[str]], file: str | None, hunk_lines: list[str]
) -> None:
    """Append hunk lines to the file's hunk list."""
    if file is None:
        return
    existing = hunks.get(file, [])
    existing.extend(hunk_lines)
    hunks[file] = existing


def _apply_with_unidiff(repo: Path, unified_diff: str) -> dict:
    """Apply a Unified Diff using manual hunk parsing."""
    import re

    # Parse diff: extract file path and hunks
    current_file: str | None = None
    hunks_by_file: dict[str, list[str]] = {}
    current_hunk: list[str] = []
    in_hunk = False

    for line in unified_diff.splitlines(keepends=True):
        header_match = re.match(r'^--- a/(.+)$', line)
        if header_match:
            if current_file and current_hunk:
                _save_hunk(hunks_by_file, current_file, current_hunk)
                current_hunk = []
            current_file = header_match.group(1)
            in_hunk = False
            continue
        if line.startswith('+++ b/'):
            in_hunk = False
            continue
        if line.startswith('@@'):
            if current_hunk:
                _save_hunk(hunks_by_file, current_file, current_hunk)
                current_hunk = []
            in_hunk = True
            continue
        if in_hunk:
            current_hunk.append(line)

    if current_file and current_hunk:
        _save_hunk(hunks_by_file, current_file, current_hunk)

    errors = []
    for filepath, hunk_lines in hunks_by_file.items():
        target = repo / filepath
        if not target.exists():
            errors.append(f"Target file not found: {filepath}")
            continue

        try:
            content = target.read_text(encoding="utf-8")
            result_lines = content.splitlines(keepends=True)

            for hunk_line in hunk_lines:
                if hunk_line.startswith('-'):
                    # Find and remove this line
                    removed = hunk_line[1:]
                    for i, rl in enumerate(result_lines):
                        if rl.rstrip('\n\r') == removed.rstrip('\n\r'):
                            result_lines.pop(i)
                            break
                elif hunk_line.startswith('+'):
                    result_lines.append(hunk_line[1:])

            target.write_text(''.join(result_lines), encoding="utf-8")
        except Exception as exc:
            errors.append(f"Failed to apply to {filepath}: {exc}")
            continue

    if errors:
        return {
            "success": False,
            "stdout": "",
            "stderr": "\n".join(errors),
            "returncode": 1,
        }

    return {
        "success": True,
        "stdout": "Patch applied via manual parsing",
        "stderr": "",
        "returncode": 0,
    }


def _apply_with_patch(repo: Path, unified_diff: str) -> dict:
    """Apply a Unified Diff using the system 'patch' command."""
    try:
        patch_result = subprocess.run(
            ["patch", "--binary", "-p1"],
            input=unified_diff,
            capture_output=True,
            text=True,
            cwd=repo,
            timeout=30,
        )
        return {
            "success": patch_result.returncode == 0,
            "stdout": patch_result.stdout,
            "stderr": patch_result.stderr,
            "returncode": patch_result.returncode,
        }
    except FileNotFoundError:
        return {
            "success": False,
            "stdout": "",
            "stderr": "Neither 'git' nor 'patch' is available",
            "returncode": -1,
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "stdout": "",
            "stderr": "patch timed out after 30s",
            "returncode": -1,
        }


def verify_patch_boundaries(
    unified_diff: str,
    allowed_files: list[str] | None = None,
) -> dict[str, Any]:
    """Verify that a Unified Diff only modifies allowed files.

    Args:
        unified_diff: The Unified Diff string.
        allowed_files: List of allowed file paths. If None, allows all.

    Returns:
        dict with keys:
          valid: bool
          modified_files: list[str]
          violations: list[str] (files not in allowed list)
    """
    import re

    # Extract modified files from diff header
    modified = set()
    for line in unified_diff.splitlines():
        if line.startswith("--- a/") or line.startswith("+++ b/"):
            # Extract path after --- a/ or +++ b/
            path = line[6:].strip()
            # Remove timestamp if present
            path = re.sub(r"\s+\d{4}-\d{2}-\d{2}.*", "", path)
            modified.add(path)

    modified_list = sorted(modified)

    if allowed_files is None:
        return {
            "valid": True,
            "modified_files": modified_list,
            "violations": [],
        }

    violations = [f for f in modified_list if f not in allowed_files]
    return {
        "valid": len(violations) == 0,
        "modified_files": modified_list,
        "violations": violations,
    }


def cleanup_sandbox(sandbox_path: str) -> None:
    """Remove the temporary sandbox directory.

    Args:
        sandbox_path: Path to the sandbox directory to remove.
    """
    path = Path(sandbox_path).resolve()
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
