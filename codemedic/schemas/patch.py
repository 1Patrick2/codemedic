"""Fixer agent's patch proposal schema."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PatchProposal(BaseModel):
    """Structured output from the Fixer agent."""

    modified_files: list[str] = Field(
        description="List of files to be modified, relative to repo root"
    )
    unified_diff: str = Field(
        description="The full Unified Diff output for all changes"
    )
    rationale: str = Field(
        description="Detailed explanation of why each change is needed"
    )
    risks: list[str] = Field(
        description="Potential risks or side effects of this patch"
    )
    test_suggestions: list[str] = Field(
        description="Recommended test commands to verify the fix"
    )
