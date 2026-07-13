"""Final status derivation — single source of truth."""

from __future__ import annotations

from typing import Literal

from codemedic.graph.state import RepairState
from codemedic.schemas.adapters import (
    get_diff_validation,
    get_patch_apply_result,
    get_test_results,
)

FinalStatus = Literal["通过", "人工复核", "拒绝"]


def derive_final_status(state: RepairState) -> FinalStatus:
    """Determine final workflow status from accumulated state.

    Priority (first match wins):
      1. User rejected → 拒绝
      2. Diff invalid/missing → 人工复核
      3. Patch not applied → 人工复核
      4. No test results → 人工复核
      5. Test timed out → 人工复核
      6. Test failed → 人工复核
      7. All conditions met → 通过
    """
    # 1. User rejected
    decision = state.get("human_decision")
    if decision in ("rejected", "reject"):
        return "拒绝"

    # 2. Diff validation
    diff = get_diff_validation(state)
    if diff is None or not diff.valid:
        return "人工复核"

    # 3. Patch application
    apply_result = get_patch_apply_result(state)
    if apply_result is None or not apply_result.success:
        return "人工复核"

    # 4-6. Test results
    test_results = get_test_results(state)
    if not test_results:
        return "人工复核"

    if any(r.timed_out for r in test_results):
        return "人工复核"

    if any(r.returncode != 0 for r in test_results):
        return "人工复核"

    # 7. All good
    return "通过"
