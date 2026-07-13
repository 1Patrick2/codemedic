"""Repository context — validated root directory for tool access."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RepositoryContext:
    """Validated repository root that all tools are bound to.

    Tools receive this context at construction time, not as a parameter
    from the model, preventing path traversal attacks.
    """

    root: Path

    def __init__(self, root: str | Path) -> None:
        """Create a validated repository context.

        Args:
            root: Path to the repository root directory (str or Path).

        Raises:
            NotADirectoryError: If the root does not exist or is not a directory.
        """
        resolved = Path(root).resolve(strict=False)
        if not resolved.is_dir():
            raise NotADirectoryError(f"Repository root not found: {root}")
        object.__setattr__(self, "root", resolved)

    def resolve_path(self, relative: str) -> Path | None:
        """Resolve a relative path within the repository.

        Returns None if the path escapes the repository root.
        """
        target = (self.root / relative).resolve()
        try:
            target.relative_to(self.root)
        except ValueError:
            return None

        # Reject symlink escapes: the resolved path must still be inside root
        if target.is_symlink():
            real = target.resolve(strict=False)
            try:
                real.relative_to(self.root)
            except ValueError:
                return None

        return target

    def is_within(self, path: Path) -> bool:
        """Check if a resolved path is within the repository root."""
        try:
            path.relative_to(self.root)
            return True
        except ValueError:
            return False
