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
    human_review_router,
)
from codemedic.graph.state import RepairState, create_initial_state
from codemedic.schemas.diagnosis import DiagnosisResult, Evidence

DEMO_REPO = str(Path(__file__).resolve().parent.parent / "demo_repos" / "sample_project")


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def initial_state() -> RepairState:
    return create_initial_state(
        issue="Test issue",
        repository_path=DEMO_REPO,
    )


@pytest.fixture
def diagnosis_high_conf() -> DiagnosisResult:
    return DiagnosisResult(
        suspected_files=["src/utils/math_helpers.py"],
        root_cause="Variable name typo in factorial()",
        evidence=[
            Evidence(
                file_path="src/utils/math_helpers.py",
                line_start=40,
                line_end=43,
                excerpt="resut vs result",
                reason="NameError due to typo",
            )
        ],
        confidence=0.85,
        missing_information=[],
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
        route = evidence_gate_router(state)  # type: ignore[arg-type]
        assert route == SUFFICIENT

    def test_routes_to_insufficient_with_low_confidence_and_rounds_left(
        self, initial_state: RepairState, diagnosis_low_conf: DiagnosisResult
    ) -> None:
        state = dict(initial_state)
        state["diagnosis"] = diagnosis_low_conf
        route = evidence_gate_router(state)  # type: ignore[arg-type]
        assert route == INSUFFICIENT

    def test_proceeds_when_out_of_rounds(
        self, initial_state: RepairState, diagnosis_low_conf: DiagnosisResult
    ) -> None:
        state = dict(initial_state)
        state["diagnosis"] = diagnosis_low_conf
        state["retrieval_round"] = 2
        route = evidence_gate_router(state)  # type: ignore[arg-type]
        # Low confidence + no evidence + out of rounds → uncertain
        assert route == UNCERTAIN


# ── Fixer stub tests ─────────────────────────────────────────────────────────


class TestFixerNode:
    def test_no_diagnosis_adds_error(self, initial_state: RepairState) -> None:
        result = fixer_node(initial_state)
        assert "errors" in result
        assert any("No diagnosis" in e for e in result["errors"])


# ── Human review router tests ────────────────────────────────────────────────


class TestHumanReviewRouter:
    def test_approved_routes_correctly(self, initial_state: RepairState) -> None:
        state = dict(initial_state)
        state["human_decision"] = "approved"
        route = human_review_router(state)  # type: ignore[arg-type]
        assert route == "approved"

    def test_rejected_routes_correctly(self, initial_state: RepairState) -> None:
        state = dict(initial_state)
        state["human_decision"] = "rejected"
        route = human_review_router(state)  # type: ignore[arg-type]
        assert route == "rejected"

    def test_retry_routes_correctly(self, initial_state: RepairState) -> None:
        state = dict(initial_state)
        state["human_decision"] = "retry"
        route = human_review_router(state)  # type: ignore[arg-type]
        assert route == "retry"

    def test_defaults_to_rejected(self, initial_state: RepairState) -> None:
        route = human_review_router(initial_state)
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

    @patch("codemedic.tools.test_runner.run_tests")
    @patch("codemedic.tools.sandbox.apply_patch")
    @patch("codemedic.tools.sandbox.create_temp_copy")
    @patch("codemedic.agents.fixer.run_fixer")
    @patch("codemedic.graph.nodes.run_investigator")
    def test_workflow_runs_end_to_end(
        self, mock_investigator, mock_fixer, mock_temp_copy, mock_apply, mock_run_tests, initial_state: RepairState
    ) -> None:
        """Full workflow with mocked investigator, fixer, and sandbox."""
        mock_investigator.return_value = DiagnosisResult(
            suspected_files=["src/utils/math_helpers.py"],
            root_cause="Variable name typo in factorial()",
            evidence=[
                Evidence(
                    file_path="src/utils/math_helpers.py",
                    line_start=40,
                    line_end=43,
                    excerpt="resut = 1",
                    reason="NameError due to typo: resut should be result",
                )
            ],
            confidence=0.85,
            missing_information=[],
        )
        from codemedic.schemas.patch import PatchProposal
        mock_fixer.return_value = PatchProposal(
            modified_files=["src/utils/math_helpers.py"],
            unified_diff="\n".join([
                "--- a/src/utils/math_helpers.py",
                "+++ b/src/utils/math_helpers.py",
                "@@ -40,6 +40,6 @@ def factorial(n: int) -> int:",
                "     if n == 0:",
                "         return 1",
                "-    resut = 1",
                "+    result = 1",
                "     for i in range(1, n + 1):",
                "-        resut *= i",
                "+        result *= i",
                "-    return resut",
                "+    return result",
            ]),
            rationale="Fix variable name typo",
            risks=["Low risk - simple rename"],
            test_suggestions=["python -m pytest tests/"],
        )
        mock_temp_copy.return_value = "/tmp/sandbox_test"
        mock_apply.return_value = {"success": True, "stdout": "", "stderr": "", "returncode": 0}
        mock_run_tests.return_value = [{
            "command": "python -m pytest -q",
            "returncode": 0,
            "stdout": "all tests passed",
            "stderr": "",
            "timed_out": False,
        }]

        from langgraph.checkpoint.memory import MemorySaver
        from langgraph.types import Command

        from codemedic.graph.builder import compile_workflow

        agent = compile_workflow(checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "test_e2e"}}

        # First invoke pauses at human_review
        result = agent.invoke(initial_state, config)

        # Resume with 'approved' decision
        result = agent.invoke(Command(resume={"decision": "approved"}), config)

        assert result["final_status"] == "通过"
        assert result["final_report"] is not None
        assert result["diagnosis"] is not None
        assert result["diagnosis"].confidence == 0.85

    @patch("codemedic.graph.nodes.run_investigator")
    def test_workflow_does_retrieval_loop(
        self, mock_investigator, initial_state: RepairState
    ) -> None:
        """Low-confidence diagnosis triggers re-retrieval."""
        mock_investigator.return_value = DiagnosisResult(
            suspected_files=[],
            root_cause="Cannot determine",
            evidence=[],
            confidence=0.3,
            missing_information=["Need more context"],
        )

        from langgraph.checkpoint.memory import MemorySaver
        from langgraph.types import Command

        from codemedic.graph.builder import compile_workflow

        agent = compile_workflow(checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "test_loop"}}

        # First invoke pauses at human_review
        result = agent.invoke(initial_state, config)

        # Resume with 'approved' to complete
        result = agent.invoke(Command(resume={"decision": "approved"}), config)

        # Should have completed at least 1 retrieval round

        # Should have completed at least 1 retrieval round
        assert result["retrieval_round"] >= 1
        assert result["investigation_steps"] >= 1
        assert result["final_status"] is not None


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
        assert result["final_status"] == "通过"
        assert result["diagnosis"] is not None
