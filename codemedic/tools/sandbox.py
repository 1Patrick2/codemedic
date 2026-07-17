"""Sandbox operations — creating temp copies, applying patches."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
import uuid
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

    # Keep the copy outside the target repository so git apply does not
    # discover and use the target repository's parent .git directory.
    sandbox_root = Path(tempfile.gettempdir()) / "codemedic_sandboxes"
    sandbox_root.mkdir(parents=True, exist_ok=True)
    sandbox_dir = sandbox_root / f"codemedic_sandbox_{uuid.uuid4().hex}"
    sandbox_dir.mkdir()
    try:
        shutil.copytree(
            source,
            sandbox_dir,
            symlinks=False,
            ignore_dangling_symlinks=True,
            ignore=_ignore_sandbox_artifacts,
            dirs_exist_ok=True,
        )
    except Exception:
        cleanup_sandbox(str(sandbox_dir))
        raise
    return str(sandbox_dir)


def _ignore_sandbox_artifacts(_directory: str, names: list[str]) -> set[str]:
    """Exclude caches, VCS metadata, and local secrets from test copies."""
    ignored = {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
    }
    return {
        name for name in names
        if name in ignored or (name.startswith(".env") and name != ".env.example")
    }


def apply_patch(repo_path: str, unified_diff: str) -> dict[str, Any]:
    """Apply a Unified Diff patch to the repository copy.

    Uses git apply --check then git apply. No fallback — only git apply.
    The sandbox must be a git repository or the diff format must work
    without .git metadata.

    Args:
        repo_path: Path to the repository root (the temp copy).
        unified_diff: The Unified Diff string to apply.

    Returns:
        dict with keys:
          success: bool
          stdout: str
          stderr: str
          returncode: int
          modified_files: list[str]
    """
    repo = Path(repo_path).resolve()
    modified_files = verify_patch_boundaries(
        unified_diff, allowed_files=None
    )["modified_files"]
    before_hashes = {
        file_path: _file_hash(repo / file_path)
        for file_path in modified_files
    }

    try:
        check_result = subprocess.run(
            [
                "git", "apply", "--check",
                "--ignore-whitespace",
            ],
            input=unified_diff,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=repo,
            timeout=30,
        )
        if check_result.returncode != 0:
            return {
                "success": False,
                "stdout": check_result.stdout,
                "stderr": check_result.stderr,
                "returncode": check_result.returncode,
                "modified_files": [],
            }
    except FileNotFoundError:
        return {
            "success": False,
            "stdout": "",
            "stderr": "git command not found — unable to apply patches",
            "returncode": -1,
            "modified_files": [],
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "stdout": "",
            "stderr": "git apply --check timed out",
            "returncode": -1,
            "modified_files": [],
        }

    try:
        apply_result = subprocess.run(
            [
                "git", "apply",
                "--ignore-whitespace",
            ],
            input=unified_diff,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=repo,
            timeout=30,
        )
        return {
            "success": apply_result.returncode == 0,
            "stdout": apply_result.stdout,
            "stderr": apply_result.stderr,
            "returncode": apply_result.returncode,
            "modified_files": (
                [
                    file_path for file_path in modified_files
                    if _file_hash(repo / file_path) != before_hashes[file_path]
                ]
                if apply_result.returncode == 0
                else []
            ),
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "stdout": "",
            "stderr": "git apply timed out",
            "returncode": -1,
            "modified_files": [],
        }


def _file_hash(path: Path) -> str | None:
    """Return a stable content hash, including a marker for missing files."""
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


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

    modified = set()
    for line in unified_diff.splitlines():
        if line.startswith("--- a/") or line.startswith("+++ b/"):
            path = line[6:].strip()
            path = re.sub(r"\s+\d{4}-\d{2}-\d{2}.*", "", path)
            modified.add(path)

    modified_list = sorted(modified)

    if allowed_files is None:
        return {"valid": True, "modified_files": modified_list, "violations": []}

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
    if not (
        path.name.startswith("codemedic_sandbox_")
        or path.parent.name.startswith("codemedic_sandbox_")
    ):
        return
    root = path
    if path.parent.name.startswith("codemedic_sandbox_"):
        root = path.parent
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
