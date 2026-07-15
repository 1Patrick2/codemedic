"""Read-only repository exploration tools for the Investigator agent.

All tools enforce strict security boundaries:
- Path traversal protection
- File size limits
- Binary file detection
- Directory recursion limits
"""

from __future__ import annotations

import re
from pathlib import Path

from codemedic.config import settings
from codemedic.tools.context import _is_rooted_path

# ── Helpers ──────────────────────────────────────────────────────────────────


def _resolve_safe_path(repo_root: Path, requested: str) -> Path | None:
    """Resolve a file path within the repo, rejecting traversal attempts."""
    if not isinstance(requested, str) or _is_rooted_path(requested):
        return None
    target = (repo_root / requested).resolve()
    try:
        target.relative_to(repo_root.resolve())
    except ValueError:
        return None
    return target


def _is_binary(path: Path) -> bool:
    """Quick binary file detection by checking a sample."""
    try:
        with open(path, "rb") as f:
            chunk = f.read(8192)
        return b"\0" in chunk
    except OSError:
        return True


# ── Tools ────────────────────────────────────────────────────────────────────


def list_repo_tree(repository_path: str, max_depth: int = 4) -> str:
    """List the directory tree of a repository.

    Args:
        repository_path: Absolute or relative path to the repository root.
        max_depth: Maximum directory depth to traverse (default 4, max 8).

    Returns:
        A tree-formatted string of the repository structure.
    """
    repo = Path(repository_path).resolve()
    if not repo.is_dir():
        return f"ERROR: path is not a directory: {repository_path}"

    max_depth = min(max_depth, 8)

    def _walk(dir_path: Path, depth: int) -> list[str]:
        if depth > max_depth:
            return ["  " * depth + "...", "  " * depth + f"[max depth {max_depth} reached]"]

        lines: list[str] = []
        try:
            entries = sorted(dir_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError:
            lines.append("  " * depth + "[permission denied]")
            return lines

        for entry in entries:
            name = entry.name
            if name.startswith(".") and depth == 0:
                # Skip hidden files but allow at top level
                pass
            if name.startswith("__pycache__") or name == ".git":
                continue

            indent = "  " * depth
            if entry.is_symlink():
                lines.append(f"{indent}{name} -> [symlink, skipped]")
            elif entry.is_dir():
                lines.append(f"{indent}{name}/")
                lines.extend(_walk(entry, depth + 1))
            else:
                lines.append(f"{indent}{name}")
        return lines

    tree = _walk(repo, 0)
    return "\n".join(tree) if tree else "(empty directory)"


def search_code(repository_path: str, pattern: str, file_pattern: str = "*.py") -> str:
    """Search for a regex pattern in files inside the repository.

    Args:
        repository_path: Path to the repository root.
        pattern: Regex pattern to search for.
        file_pattern: Glob pattern to filter files (default '*.py').

    Returns:
        Matching lines with file paths and line numbers.
    """
    repo = Path(repository_path).resolve()
    if not repo.is_dir():
        return f"ERROR: path is not a directory: {repository_path}"

    try:
        compiled = re.compile(pattern)
    except re.error as e:
        return f"ERROR: invalid regex pattern: {e}"

    results: list[str] = []
    max_file_size = settings.max_file_size_bytes
    max_results = 50

    files_scanned = 0
    for fpath in repo.rglob(file_pattern):
        # Skip hidden / cache dirs
        if any(part.startswith(".") or part == "__pycache__" for part in fpath.parts):
            continue

        safe = _resolve_safe_path(repo, str(fpath.relative_to(repo)))
        if safe is None:
            continue

        if safe.is_dir() or _is_binary(safe):
            continue

        if safe.stat().st_size > max_file_size:
            continue

        files_scanned += 1
        if files_scanned > 200:
            results.append("[scanned 200 files — truncated]")
            break

        try:
            text = safe.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            continue

        for lineno, line in enumerate(text.splitlines(), start=1):
            if compiled.search(line):
                results.append(f"{fpath.relative_to(repo)}:{lineno}: {line.strip()}")
                if len(results) >= max_results:
                    break
        if len(results) >= max_results:
            break

    if not results:
        return f"No matches found for pattern: {pattern}"
    return "\n".join(results)


def read_file(repository_path: str, file_path: str) -> str:
    """Read the content of a file from the repository.

    Args:
        repository_path: Path to the repository root.
        file_path: Relative path to the file within the repository.

    Returns:
        File content with line numbers, or an error message.
    """
    repo = Path(repository_path).resolve()
    if not repo.is_dir():
        return f"ERROR: path is not a directory: {repository_path}"

    safe = _resolve_safe_path(repo, file_path)
    if safe is None:
        return f"ERROR: path traversal detected: {file_path}"

    if not safe.exists():
        return f"ERROR: file not found: {file_path}"

    if safe.is_dir():
        return f"ERROR: path is a directory: {file_path}"

    if _is_binary(safe):
        return f"ERROR: binary file: {file_path}"

    if safe.stat().st_size > settings.max_file_size_bytes:
        return (
            f"ERROR: file too large ({safe.stat().st_size} bytes > "
            f"{settings.max_file_size_bytes} limit)"
        )

    try:
        text = safe.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError) as exc:
        return f"ERROR: could not read file: {exc}"

    lines = text.splitlines()
    # Show line numbers
    numbered = [f"{i:4d}: {line}" for i, line in enumerate(lines, start=1)]
    return "\n".join(numbered)


def parse_log(log_content: str, max_lines: int = 200) -> str:
    """Parse and summarize an error log.

    Extracts error lines, tracebacks, and key diagnostic information.

    Args:
        log_content: The raw log content as a string.
        max_lines: Maximum number of lines to process (default 200).

    Returns:
        A structured summary of the log.
    """
    lines = log_content.splitlines()
    was_truncated = len(lines) > max_lines
    if was_truncated:
        lines = lines[:max_lines]

    error_lines: list[str] = []
    traceback_lines: list[str] = []
    in_traceback = False

    for i, line in enumerate(lines):
        lower = line.lower()
        if any(kw in lower for kw in ("error", "exception", "traceback", "failed", "fail:")):
            if "traceback" in lower:
                in_traceback = True
                traceback_lines.append(f"L{i + 1}: {line}")
            else:
                error_lines.append(f"L{i + 1}: {line}")
        elif in_traceback:
            if line.startswith(" ") or line.startswith("\t"):
                traceback_lines.append(f"L{i + 1}: {line}")
            else:
                in_traceback = False

    result: list[str] = []
    result.append(f"--- Log Summary ({len(lines)} lines processed) ---")
    if was_truncated:
        result.append(f"[truncated at {max_lines} lines]")
    if error_lines:
        result.append(f"\nError/Exception lines ({len(error_lines)}):")
        result.extend(error_lines)
    if traceback_lines:
        result.append(f"\nTraceback ({len(traceback_lines)} lines):")
        result.extend(traceback_lines)
    if not error_lines and not traceback_lines:
        result.append("No obvious errors or exceptions found in the log.")

    return "\n".join(result)
