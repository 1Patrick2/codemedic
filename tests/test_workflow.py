"""Tests for the LangGraph workflow (Stage 2-3).

Tests the StateGraph structure, nodes, routers, and full workflow
without relying on LLM calls (except for a single smoke test).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from codemedic.graph.nodes import (
    final_report_node,
    fixer_node,
    hybrid_retrieve,
    intake,
)
from codemedic.graph.routers import (
    INSUFFICIENT,
    SUFFICIENT,
    UNCERTAIN,
    evidence_gate_router,
    patch_review_router,
)
from codemedic.graph.state import RepairState, create_initial_state
from codemedic.schemas.adapters import get_diagnosis
from codemedic.schemas.diagnosis import DiagnosisResult
from tests.factories import build_demo_diagnosis

DEMO_REPO = str(Path(__file__).resolve().parent.parent / "demo_repos" / "sample_project")


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def initial_state() -> RepairState:
    return create_initial_state(
        issue="Test issue",
        repository_path=DEMO_REPO,
        review_policy="controlled_auto",
    )


@pytest.fixture
def diagnosis_high_conf() -> DiagnosisResult:
    return build_demo_diagnosis(
        confidence=0.85,
        evidence=build_demo_diagnosis().evidence[:1],
        suspected_files=["src/utils/math_helpers.py"],
    )


@pytest.fixture
def diagnosis_low_conf() -> DiagnosisResult:
    return DiagnosisResult(
        suspected_files=[],
        root_cause="Could not determine root cause",
        evidence=[],
        confidence=0.3,
        missing_information=["Need more context"],
    )


# ── State tests ──────────────────────────────────────────────────────────────


class TestState:
    def test_create_initial_state(self, initial_state: RepairState) -> None:
        assert initial_state["issue"] == "Test issue"
        assert initial_state["repository_path"] == DEMO_REPO
        assert initial_state["investigation_steps"] == 0
        assert initial_state["retrieval_round"] == 0
        assert initial_state["retry_count"] == 0
        assert initial_state["errors"] == []
        assert initial_state["diagnosis"] is None

    def test_state_task_id_is_generated(self) -> None:
        s1 = create_initial_state("A", DEMO_REPO)
        s2 = create_initial_state("B", DEMO_REPO)
        assert s1["task_id"] != s2["task_id"]  # each should be unique


# ── Intake node tests ────────────────────────────────────────────────────────


class TestIntake:
    def test_intake_initialises_counters(self, initial_state: RepairState) -> None:
        result = intake(initial_state)
        assert result["investigation_steps"] == 0
        assert result["retrieval_round"] == 0
        assert result["retry_count"] == 0

    def test_intake_reports_missing_repo(self) -> None:
        state = create_initial_state("X", "/nonexistent/path")
        result = intake(state)
        assert len(result["errors"]) == 1
        assert "not found" in result["errors"][0]


# ── Hybrid retrieve node tests ────────────────────────────────────────────────


class TestHybridRetrieve:
    def test_retrieves_context(self, initial_state: RepairState) -> None:
        result = hybrid_retrieve(initial_state)
        assert "retrieved_context" in result
        assert len(result["retrieved_context"]) > 0
        # Should include tree and file contents
        assert result["retrieved_context"][0]["type"] == "tree"

    def test_increments_retrieval_round(self, initial_state: RepairState) -> None:
        result = hybrid_retrieve(initial_state)
        assert result["retrieval_round"] == 1


# ── Evidence gate router tests ────────────────────────────────────────────────


class TestEvidenceGate:
    def test_routes_to_sufficient_when_no_diagnosis(self) -> None:
        state = create_initial_state("X", DEMO_REPO)
        # No diagnosis set → insufficient (needs more info)
        route = evidence_gate_router(state)
        assert route == INSUFFICIENT

    def test_routes_to_sufficient_with_high_confidence(
        self, initial_state: RepairState, diagnosis_high_conf: DiagnosisResult
    ) -> None:
        state = dict(initial_state)
        state["diagnosis"] = diagnosis_high_conf
        state["evidence_validation"] = {
            "valid": True,
            "errors": [],
            "validated_evidence": [],
        }
        route = evidence_gate_router(state)  # type: ignore[arg-type]
        assert route == SUFFICIENT

    def test_routes_to_insufficient_with_low_confidence_and_rounds_left(
        self, initial_state: RepairState, diagnosis_low_conf: DiagnosisResult
    ) -> None:
        state = dict(initial_state)
        state["diagnosis"] = diagnosis_low_conf
        state["evidence_validation"] = {
            "valid": True,
            "errors": [],
            "validated_evidence": [],
        }
        route = evidence_gate_router(state)  # type: ignore[arg-type]
        assert route == INSUFFICIENT

    def test_proceeds_when_out_of_rounds(
        self, initial_state: RepairState, diagnosis_low_conf: DiagnosisResult
    ) -> None:
        state = dict(initial_state)
        state["diagnosis"] = diagnosis_low_conf
        state["retrieval_round"] = 2
        state["evidence_validation"] = {
            "valid": True,
            "errors": [],
            "validated_evidence": [],
        }
        route = evidence_gate_router(state)  # type: ignore[arg-type]
        # Low confidence + no evidence + out of rounds → uncertain
        assert route == UNCERTAIN

    def test_missing_evidence_validation_routes_to_uncertain(
        self, initial_state: RepairState, diagnosis_high_conf: DiagnosisResult
    ) -> None:
        state = dict(initial_state)
        state["diagnosis"] = diagnosis_high_conf

        assert evidence_gate_router(state) == UNCERTAIN


# ── Fixer stub tests ─────────────────────────────────────────────────────────


class TestFixerNode:
    def test_no_diagnosis_adds_error(self, initial_state: RepairState) -> None:
        result = fixer_node(initial_state)
        assert "errors" in result
        assert any("No diagnosis" in e for e in result["errors"])


# ── Human review router tests ────────────────────────────────────────────────


class TestPatchReviewRouter:
    def test_approved_routes_correctly(self, initial_state: RepairState) -> None:
        state = dict(initial_state)
        state["human_decision"] = "approved"
        route = patch_review_router(state)  # type: ignore[arg-type]
        assert route == "approved"

    def test_rejected_routes_correctly(self, initial_state: RepairState) -> None:
        state = dict(initial_state)
        state["human_decision"] = "rejected"
        route = patch_review_router(state)  # type: ignore[arg-type]
        assert route == "rejected"

    def test_retry_routes_correctly(self, initial_state: RepairState) -> None:
        state = dict(initial_state)
        state["human_decision"] = "retry"
        route = patch_review_router(state)  # type: ignore[arg-type]
        assert route == "retry"

    def test_defaults_to_rejected(self, initial_state: RepairState) -> None:
        route = patch_review_router(initial_state)
        assert route == "rejected"


# ── Final report node tests ──────────────────────────────────────────────────


class TestFinalReport:
    def test_generates_report_with_diagnosis(
        self, initial_state: RepairState, diagnosis_high_conf: DiagnosisResult
    ) -> None:
        state = dict(initial_state)
        state["diagnosis"] = diagnosis_high_conf
        result = final_report_node(state)  # type: ignore[arg-type]
        assert "final_report" in result
        assert result["final_report"]["task_id"] == state["task_id"]
        assert result["final_report"]["evidence_count"] == 1

    def test_generates_report_without_diagnosis(self, initial_state: RepairState) -> None:
        result = final_report_node(initial_state)
        assert result["final_report"]["root_cause"] == "No diagnosis produced"
        assert result["final_report"]["evidence_count"] == 0


# ── Workflow integration test (no LLM) ──────────────────────────────────────


class TestWorkflowGraph:
    def test_build_and_compile(self) -> None:
        """Graph can be built and compiled without errors."""
        from codemedic.graph.builder import build_workflow

        graph = build_workflow()
        compiled = graph.compile()
        assert compiled is not None

    @patch("codemedic.agents.fixer.run_fixer")
    @patch("codemedic.graph.nodes.run_investigator")
    def test_workflow_runs_end_to_end(
        self,
        mock_investigator,
        mock_fixer,
        initial_state: RepairState
    ) -> None:
        """Full workflow with mocked investigator, fixer, and sandbox."""
        mock_investigator.return_value = build_demo_diagnosis()
        from codemedic.schemas.patch import PatchProposal
        from tests.test_real_sandbox_e2e import FIX_FILES, FIX_PATCH
        mock_fixer.return_value = PatchProposal(
            modified_files=FIX_FILES,
            unified_diff=FIX_PATCH,
            rationale="Fix variable name typo",
            risks=["Low risk - simple rename"],
            test_suggestions=["python -m pytest tests/"],
        )
        from langgraph.checkpoint.memory import MemorySaver
        from langgraph.types import Command

        from codemedic.graph.builder import compile_workflow

        initial_state["thread_id"] = "test_e2e"
        agent = compile_workflow(checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "test_e2e"}}

        # First invoke pauses at human_review
        result = agent.invoke(initial_state, config)

        # Check review type
        interrupt_payload = result["__interrupt__"][0].value
        assert interrupt_payload["review_type"] == "patch"  # must be patch review
        assert result["workflow_status"] == "waiting_patch_review"

        # Resume with 'approved' decision
        result = agent.invoke(Command(resume={"decision": "approved"}), config)

        assert result["final_status"] == "通过"
        assert result["final_report"] is not None
        assert result["diagnosis"] is not None
        assert get_diagnosis(result).confidence == 0.85
        assert result["thread_id"] == "test_e2e"
        assert result["patch_apply_result"]["success"] is True
        assert result["patch_apply_result"]["modified_files"] == FIX_FILES
        assert result["test_results"]
        assert all(item["returncode"] == 0 for item in result["test_results"])

    @patch("codemedic.graph.nodes.run_investigator")
    @patch("codemedic.agents.fixer.run_fixer")
    def test_real_retry_uses_failure_feedback_and_allows_second_approval(
        self,
        mock_fixer,
        mock_investigator,
        initial_state: RepairState,
    ) -> None:
        """A failed real sandbox test produces feedback for a second patch."""
        from codemedic.schemas.patch import PatchProposal
        from tests.test_real_sandbox_e2e import (
            FIRST_RETRY_PATCH,
            FIX_FILES,
            FIX_PATCH,
        )

        mock_investigator.return_value = build_demo_diagnosis(
            confidence=0.9,
            suspected_files=FIX_FILES,
        )
        mock_fixer.side_effect = [
            PatchProposal(
                modified_files=["src/utils/math_helpers.py"],
                unified_diff=FIRST_RETRY_PATCH,
                rationale="Fix the first factorial typo",
                risks=[],
                test_suggestions=[],
            ),
            PatchProposal(
                modified_files=FIX_FILES,
                unified_diff=FIX_PATCH,
                rationale="Fix all reported Demo failures",
                risks=[],
                test_suggestions=[],
            ),
        ]

        from langgraph.checkpoint.memory import MemorySaver
        from langgraph.types import Command

        from codemedic.graph.builder import compile_workflow

        initial_state["thread_id"] = "test_real_retry"
        agent = compile_workflow(checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "test_real_retry"}}

        first = agent.invoke(initial_state, config)
        assert first["__interrupt__"][0].value["review_type"] == "patch"
        first = agent.invoke(Command(resume={"decision": "approved"}), config)

        second_review = first["__interrupt__"][0].value
        assert second_review["review_type"] == "patch"
        assert second_review["options"] == ["approved", "rejected"]
        assert first["fix_attempt_count"] == 2
        assert first["previous_patch"] is not None
        assert "failed" in first["failure_feedback"]

        completed = agent.invoke(Command(resume={"decision": "approved"}), config)

        assert completed["final_status"] == "通过"
        assert completed["patch_apply_result"]["modified_files"] == FIX_FILES

    @patch("codemedic.graph.nodes.run_investigator")
    @patch("codemedic.agents.fixer.run_fixer")
    def test_invalid_diff_is_fail_closed_after_retry_budget(
        self,
        mock_fixer,
        mock_investigator,
        initial_state: RepairState,
    ) -> None:
        """An invalid Diff exhausts retry and cannot report a successful repair."""
        from langgraph.checkpoint.memory import MemorySaver

        from codemedic.graph.builder import compile_workflow
        from codemedic.schemas.patch import PatchProposal

        mock_investigator.return_value = build_demo_diagnosis()
        mock_fixer.return_value = PatchProposal(
            modified_files=["src/utils/math_helpers.py"],
            unified_diff="not a unified diff",
            rationale="Invalid patch for fail-closed coverage",
            risks=[],
            test_suggestions=[],
        )

        initial_state["thread_id"] = "test_invalid_diff_fail_closed"
        agent = compile_workflow(checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "test_invalid_diff_fail_closed"}}

        result = agent.invoke(initial_state, config)

        assert result["final_status"] == "人工复核"
        assert result["final_status"] != "通过"
        assert result["test_results"] == []
        assert result["retry_count"] == 1
        assert result["fix_attempt_count"] == 2
        assert mock_fixer.call_count == 2

    @patch("codemedic.graph.nodes.run_investigator")
    @patch("codemedic.agents.fixer.run_fixer")
    def test_patch_apply_failure_skips_tests_and_cannot_pass(
        self,
        mock_fixer,
        mock_investigator,
        initial_state: RepairState,
    ) -> None:
        """A failed Apply routes directly to Final Report without running tests."""
        from langgraph.checkpoint.memory import MemorySaver
        from langgraph.types import Command

        from codemedic.graph.builder import compile_workflow
        from codemedic.schemas.patch import PatchProposal
        from codemedic.schemas.results import PatchApplyResult
        from tests.test_real_sandbox_e2e import FIX_FILES, FIX_PATCH

        mock_investigator.return_value = build_demo_diagnosis()
        mock_fixer.return_value = PatchProposal(
            modified_files=FIX_FILES,
            unified_diff=FIX_PATCH,
            rationale="Valid patch before simulated Apply failure",
            risks=[],
            test_suggestions=[],
        )
        failed_apply = PatchApplyResult(
            success=False,
            returncode=1,
            stderr="simulated git apply failure",
        ).model_dump()

        initial_state["thread_id"] = "test_apply_failure_fail_closed"
        with (
            patch("codemedic.graph.builder.apply_patch_node", return_value={
                "patch_apply_result": failed_apply,
                "sandbox_path": None,
                "sandbox_cleaned": True,
            }),
            patch("codemedic.graph.builder.run_tests_node") as run_tests,
        ):
            agent = compile_workflow(checkpointer=MemorySaver())
            config = {"configurable": {"thread_id": "test_apply_failure_fail_closed"}}
            first = agent.invoke(initial_state, config)
            assert first["__interrupt__"][0].value["review_type"] == "patch"

            completed = agent.invoke(Command(resume={"decision": "approved"}), config)

        assert completed["final_status"] == "人工复核"
        assert completed["final_status"] != "通过"
        run_tests.assert_not_called()

    @patch("codemedic.graph.nodes.run_investigator")
    @patch("codemedic.agents.fixer.run_fixer")
    def test_workflow_does_retrieval_loop(
        self, mock_fixer, mock_investigator, initial_state: RepairState
    ) -> None:
        """Low-confidence diagnosis triggers re-retrieval."""
        mock_investigator.return_value = DiagnosisResult(
            suspected_files=[],
            root_cause="Cannot determine",
            evidence=[],
            confidence=0.3,
            missing_information=["Need more context"],
        )
        from codemedic.schemas.patch import PatchProposal

        mock_fixer.return_value = PatchProposal(
            modified_files=[],
            unified_diff="",
            rationale="No patch needed for this routing test",
            risks=[],
            test_suggestions=[],
        )

        from langgraph.checkpoint.memory import MemorySaver
        from langgraph.types import Command

        from codemedic.graph.builder import compile_workflow

        agent = compile_workflow(checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "test_loop"}}
        initial_state["review_policy"] = "manual"

        # First invoke pauses at diagnosis review
        result = agent.invoke(initial_state, config)

        interrupt_payload = result["__interrupt__"][0].value
        assert interrupt_payload["review_type"] == "diagnosis"
        assert result["workflow_status"] == "waiting_diagnosis_review"

        # Accept the diagnosis with the diagnosis-specific decision.
        result = agent.invoke(
            Command(resume={"decision": "accept_diagnosis"}),
            config,
        )

        # Should have completed at least 1 retrieval round

        # Should have completed at least 1 retrieval round
        assert result["retrieval_round"] >= 1
        assert result["investigation_steps"] >= 1
        assert result["final_status"] is not None

    @patch("codemedic.agents.fixer.run_fixer")
    @patch("codemedic.graph.nodes.run_investigator")
    def test_diagnosis_review_override_authorizes_demo_files(
        self,
        mock_investigator,
        mock_fixer,
        initial_state: RepairState,
    ) -> None:
        from langgraph.checkpoint.memory import MemorySaver
        from langgraph.types import Command

        from codemedic.graph.builder import compile_workflow
        from codemedic.schemas.patch import PatchProposal
        from tests.test_real_sandbox_e2e import FIX_FILES, FIX_PATCH

        mock_investigator.return_value = DiagnosisResult(
            suspected_files=[],
            root_cause="Needs human authorization",
            evidence=[],
            confidence=0.3,
            missing_information=["Need review"],
        )
        mock_fixer.return_value = PatchProposal(
            modified_files=FIX_FILES,
            unified_diff=FIX_PATCH,
            rationale="Fix all Demo failures",
            risks=[],
            test_suggestions=[],
        )

        initial_state["thread_id"] = "test_diagnosis_override"
        initial_state["review_policy"] = "manual"
        agent = compile_workflow(checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "test_diagnosis_override"}}

        first = agent.invoke(initial_state, config)
        assert first["__interrupt__"][0].value["review_type"] == "diagnosis"
        assert "approved_files" in first["__interrupt__"][0].value

        second = agent.invoke(
            Command(
                resume={
                    "decision": "accept_diagnosis",
                    "approved_files": FIX_FILES,
                }
            ),
            config,
        )

        assert second["approved_files"] == sorted(FIX_FILES)
        assert second["allowed_files"] == sorted(FIX_FILES)
        assert second["__interrupt__"][0].value["review_type"] == "patch"

        completed = agent.invoke(Command(resume={"decision": "approved"}), config)

        assert completed["final_status"] == "通过"

    @patch("codemedic.agents.fixer.run_fixer")
    @patch("codemedic.graph.nodes.run_investigator")
    def test_invalid_diagnosis_review_override_never_calls_fixer(
        self,
        mock_investigator,
        mock_fixer,
        initial_state: RepairState,
    ) -> None:
        from langgraph.checkpoint.memory import MemorySaver
        from langgraph.types import Command

        from codemedic.graph.builder import compile_workflow

        mock_investigator.return_value = DiagnosisResult(
            suspected_files=[],
            root_cause="Needs human authorization",
            evidence=[],
            confidence=0.3,
            missing_information=["Need review"],
        )

        initial_state["thread_id"] = "test_invalid_diagnosis_override"
        initial_state["review_policy"] = "manual"
        agent = compile_workflow(checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "test_invalid_diagnosis_override"}}

        first = agent.invoke(initial_state, config)
        assert first["__interrupt__"][0].value["review_type"] == "diagnosis"

        rejected = agent.invoke(
            Command(
                resume={
                    "decision": "accept_diagnosis",
                    "approved_files": ["../outside.py"],
                }
            ),
            config,
        )

        assert rejected["final_status"] == "拒绝"
        mock_fixer.assert_not_called()

    @patch("codemedic.agents.fixer.run_fixer")
    @patch("codemedic.graph.nodes.run_investigator")
    def test_empty_diagnosis_review_override_never_calls_fixer(
        self,
        mock_investigator,
        mock_fixer,
        initial_state: RepairState,
    ) -> None:
        from langgraph.checkpoint.memory import MemorySaver
        from langgraph.types import Command

        from codemedic.graph.builder import compile_workflow

        mock_investigator.return_value = DiagnosisResult(
            suspected_files=[],
            root_cause="Needs human authorization",
            evidence=[],
            confidence=0.3,
            missing_information=["Need review"],
        )

        initial_state["thread_id"] = "test_empty_diagnosis_override"
        initial_state["review_policy"] = "manual"
        agent = compile_workflow(checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "test_empty_diagnosis_override"}}

        first = agent.invoke(initial_state, config)
        assert first["__interrupt__"][0].value["review_type"] == "diagnosis"

        rejected = agent.invoke(
            Command(resume={"decision": "accept_diagnosis", "approved_files": []}),
            config,
        )

        assert rejected["final_status"] == "拒绝"
        mock_fixer.assert_not_called()


# ── Smoke test with real LLM ─────────────────────────────────────────────────


@pytest.mark.skip(reason="Requires API key and credits")
class TestWorkflowReal:
    def test_real_workflow(self) -> None:
        """Run the actual workflow against the demo repo."""
        from codemedic.graph.builder import run_workflow

        result = run_workflow(
            issue="Find all bugs in the sample project",
            repository_path=DEMO_REPO,
        )
        assert result.state["final_status"] == "通过"
        assert result.state["diagnosis"] is not None
