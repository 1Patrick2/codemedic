"""Type-safe accessors for workflow result schemas.

Graph state stores dicts for LangGraph serialization compatibility.
These adapters provide typed reads and writes via Pydantic model_validate/model_dump.
"""

from __future__ import annotations

from codemedic.graph.state import RepairState
from codemedic.schemas.results import (
    DiffValidationResult,
    EvidenceValidationResult,
    PatchApplyResult,
    TestResult,
)

# ── Writes ────────────────────────────────────────────────────────────────────


def set_evidence_validation(
    state: RepairState, result: EvidenceValidationResult
) -> dict:
    """Prepare state update dict with serialized EvidenceValidationResult."""
    return {"evidence_validation": result.model_dump()}


def set_diff_validation(state: RepairState, result: DiffValidationResult) -> dict:
    """Prepare state update dict with serialized DiffValidationResult."""
    return {"diff_validation": result.model_dump()}


def set_patch_apply_result(state: RepairState, result: PatchApplyResult) -> dict:
    """Prepare state update dict with serialized PatchApplyResult."""
    return {"patch_apply_result": result.model_dump()}


def set_test_results(state: RepairState, results: list[TestResult]) -> dict:
    """Prepare state update dict with serialized list of TestResult."""
    return {"test_results": [r.model_dump() for r in results]}


# ── Reads ─────────────────────────────────────────────────────────────────────


def get_evidence_validation(
    state: RepairState,
) -> EvidenceValidationResult | None:
    """Read and validate evidence validation from state."""
    raw = state.get("evidence_validation")
    if raw is None:
        return None
    return EvidenceValidationResult.model_validate(raw)


def get_diff_validation(state: RepairState) -> DiffValidationResult | None:
    """Read and validate diff validation from state."""
    raw = state.get("diff_validation")
    if raw is None:
        return None
    return DiffValidationResult.model_validate(raw)


def get_patch_apply_result(state: RepairState) -> PatchApplyResult | None:
    """Read and validate patch apply result from state."""
    raw = state.get("patch_apply_result")
    if raw is None:
        return None
    return PatchApplyResult.model_validate(raw)


def get_test_results(state: RepairState) -> list[TestResult]:
    """Read and validate test results from state."""
    raws = state.get("test_results", [])
    return [TestResult.model_validate(r) for r in raws]
