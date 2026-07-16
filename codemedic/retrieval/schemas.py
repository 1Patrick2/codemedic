"""Serializable contracts for comparing deterministic retrieval baselines."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class RetrievalBaseline(str, Enum):
    """The retrieval capability level used for one run."""

    BASELINE_A = "baseline_a"
    BASELINE_B = "baseline_b"
    BASELINE_C = "baseline_c"


class RetrievedFile(BaseModel):
    """One repository file selected by a retrieval baseline."""

    path: str = Field(min_length=1)
    content: str = ""
    score: float = 0.0
    symbols: list[str] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)
    imports: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)


class RetrievalResult(BaseModel):
    """The common output contract shared by Baselines A, B, and C."""

    baseline: RetrievalBaseline
    files: list[RetrievedFile] = Field(default_factory=list)
    query_terms: list[str] = Field(default_factory=list)
    tool_calls: int = Field(default=0, ge=0)
    token_estimate: int = Field(default=0, ge=0)
    latency_ms: float = Field(default=0.0, ge=0)

    @property
    def file_paths(self) -> list[str]:
        return [item.path for item in self.files]

    def to_context(self) -> str:
        """Render selected files within the configured character budget."""
        return "\n\n".join(
            f"--- {item.path} ---\n{item.content}" for item in self.files
        )[: self.token_estimate]


class RetrievalRun(BaseModel):
    """Gold-task measurements for one baseline on one task."""

    task_id: str = Field(min_length=1)
    baseline: RetrievalBaseline
    correct_file: bool
    evidence_valid: bool
    evidence_line_accuracy: bool | None = None
    selected_files: list[str] = Field(default_factory=list)
    tool_calls: int = Field(default=0, ge=0)
    token_estimate: int = Field(default=0, ge=0)
    latency_ms: float = Field(default=0.0, ge=0)


class RetrievalComparisonReport(BaseModel):
    """Serializable comparison report for identical tasks across baselines."""

    baselines: list[RetrievalBaseline]
    runs: list[RetrievalRun] = Field(default_factory=list)
    summary: dict[str, dict[str, Any]] = Field(default_factory=dict)
