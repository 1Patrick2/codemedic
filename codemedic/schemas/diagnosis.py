"""Investigator agent's structured output schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    """A single piece of evidence supporting the diagnosis."""

    file_path: str = Field(description="Path to the relevant file")
    line_start: int | None = Field(default=None, description="Start line number")
    line_end: int | None = Field(default=None, description="End line number")
    excerpt: str = Field(description="Relevant code snippet or log excerpt")
    reason: str = Field(description="Why this evidence is relevant to the root cause")


class DiagnosisResult(BaseModel):
    """Structured output from the Investigator agent."""

    suspected_files: list[str] = Field(
        description="Files that likely contain the root cause"
    )
    root_cause: str = Field(description="Concise root cause description")
    evidence: list[Evidence] = Field(
        description="Supporting evidence for the diagnosis"
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence in the diagnosis (0.0 to 1.0)",
    )
    missing_information: list[str] = Field(
        description="Information that would help strengthen the diagnosis"
    )
