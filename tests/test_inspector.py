import pytest

from codemedic.schemas.diagnosis import DiagnosisResult, Evidence
from codemedic.schemas.results import WorkflowRunResult


class FakeStreamlit:
    def __init__(
        self,
        *,
        decision: str | None = None,
        approved_text: str = "",
        reason: str = "",
        button_value: bool = False,
    ) -> None:
        self.outputs: list[object] = []
        self.selectbox_options: list[list[str]] = []
        self.decision = decision
        self.approved_text = approved_text
        self.reason = reason
        self.button_value = button_value
        self.session_state: dict[str, object] = {}

    def subheader(self, value: str) -> None:
        self.outputs.append(value)

    def write(self, value: object) -> None:
        self.outputs.append(value)

    def code(self, value: str, language: str = "") -> None:
        self.outputs.append((value, language))

    def json(self, value: object) -> None:
        self.outputs.append(value)

    def selectbox(self, _label: str, options: list[str]) -> str:
        self.selectbox_options.append(options)
        return self.decision if self.decision in options else options[0]

    def text_area(self, label: str, value: str = "") -> str:
        if label.startswith("Approved Files"):
            return self.approved_text or value
        if label.startswith("Reason"):
            return self.reason
        return value

    def button(self, _label: str) -> bool:
        return self.button_value

    def rerun(self) -> None:
        self.outputs.append("rerun")


def _inspector_result(
    *,
    retry_count: int = 0,
    workflow_status: str = "waiting_patch_review",
) -> WorkflowRunResult:
    diagnosis = DiagnosisResult(
        suspected_files=["src/utils/math_helpers.py"],
        root_cause="A misspelled variable causes NameError",
        evidence=[
            Evidence(
                file_path="src/utils/math_helpers.py",
                line_start=40,
                line_end=40,
                excerpt="resut = 1",
                reason="The variable name is misspelled.",
            )
        ],
        confidence=0.8,
        missing_information=[],
    )
    return WorkflowRunResult(
        task_id="task-1",
        thread_id="thread-1",
        workflow_status=workflow_status,  # type: ignore[arg-type]
        interrupted=True,
        state={
            "run_id": "run-1",
            "diagnosis": diagnosis,
            "evidence_validation": {
                "valid": True,
                "errors": [],
                "validated_evidence": [item.model_dump() for item in diagnosis.evidence],
            },
            "patch": {"unified_diff": "diff --git a/a.py b/a.py"},
            "modified_files": ["src/utils/math_helpers.py"],
            "diff_validation": {
                "valid": True,
                "errors": [],
                "modified_files": ["src/utils/math_helpers.py"],
            },
            "risks": ["low"],
            "test_suggestions": ["pytest"],
            "test_results": [{"command": ["pytest"], "returncode": 0}],
            "allowed_files": ["src/utils/math_helpers.py"],
            "approved_files": ["src/utils/math_helpers.py"],
            "retry_count": retry_count,
        },
    )


def test_show_state_renders_pydantic_diagnosis_and_workflow_details() -> None:
    from codemedic.inspector_app import _show_state

    fake_st = FakeStreamlit()
    _show_state(fake_st, _inspector_result())

    rendered_text = repr(fake_st.outputs)
    assert "A misspelled variable causes NameError" in rendered_text
    assert "src/utils/math_helpers.py" in rendered_text
    assert "resut = 1" in rendered_text
    assert "diff --git a/a.py b/a.py" in rendered_text
    assert "diff_validation" in rendered_text
    assert "src/utils/math_helpers.py" in rendered_text
    assert "low" in rendered_text
    assert "pytest" in rendered_text
    assert "returncode" in rendered_text


def test_trajectory_events_preserve_order_and_filter_by_event_type() -> None:
    from codemedic.inspector_app import _trajectory_events

    trajectory = {
        "events": [
            {"sequence": 1, "event_type": "model_response", "summary": "model"},
            {"sequence": 2, "event_type": "tool_result", "summary": "tool"},
            {"sequence": 3, "event_type": "validation", "summary": "valid"},
        ]
    }

    assert [event["sequence"] for event in _trajectory_events(trajectory)] == [1, 2, 3]
    assert [
        event["event_type"]
        for event in _trajectory_events(trajectory, {"validation", "model_response"})
    ] == ["model_response", "validation"]


@pytest.mark.parametrize(
    ("retry_count", "max_retries", "expected"),
    [(0, 2, True), (1, 2, True), (2, 2, False)],
)
def test_show_review_uses_configured_retry_limit(
    retry_count: int,
    max_retries: int,
    expected: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codemedic.config import settings
    from codemedic.inspector_app import _show_review

    monkeypatch.setattr(settings, "max_fixer_retries", max_retries)
    fake_st = FakeStreamlit()

    _show_review(fake_st, _inspector_result(retry_count=retry_count))

    assert ("retry" in fake_st.selectbox_options[0]) is expected


def test_diagnosis_review_submits_approved_files(monkeypatch: pytest.MonkeyPatch) -> None:
    from codemedic.inspector_app import _show_review

    calls: list[dict[str, object]] = []

    def fake_resume(
        decision: str,
        reason: str,
        *,
        thread_id: str,
        approved_files: list[str] | None,
    ) -> WorkflowRunResult:
        calls.append(
            {
                "decision": decision,
                "reason": reason,
                "thread_id": thread_id,
                "approved_files": approved_files,
            }
        )
        return _inspector_result()

    monkeypatch.setattr("codemedic.graph.builder.resume_workflow", fake_resume)
    fake_st = FakeStreamlit(
        decision="accept_diagnosis",
        approved_text="src/utils/math_helpers.py\nsrc/services/data_service.py",
        reason="Authorize both files",
        button_value=True,
    )

    _show_review(
        fake_st,
        _inspector_result(workflow_status="waiting_diagnosis_review"),
    )

    assert calls == [
        {
            "decision": "accept_diagnosis",
            "reason": "Authorize both files",
            "thread_id": "thread-1",
            "approved_files": [
                "src/utils/math_helpers.py",
                "src/services/data_service.py",
            ],
        }
    ]
    assert fake_st.session_state["workflow_result"]


def test_patch_review_submits_retry_feedback(monkeypatch: pytest.MonkeyPatch) -> None:
    from codemedic.inspector_app import _show_review

    calls: list[tuple[str, str, str]] = []

    def fake_resume(
        decision: str,
        reason: str,
        *,
        thread_id: str,
        approved_files: list[str] | None,
    ) -> WorkflowRunResult:
        calls.append((decision, reason, thread_id))
        assert approved_files is None
        return _inspector_result()

    monkeypatch.setattr("codemedic.graph.builder.resume_workflow", fake_resume)
    fake_st = FakeStreamlit(
        decision="retry",
        reason="The proposed fix is incomplete",
        button_value=True,
    )

    _show_review(fake_st, _inspector_result(retry_count=0))

    assert calls == [("retry", "The proposed fix is incomplete", "thread-1")]


def test_public_inspector_apis_read_state_runs_and_trajectory(tmp_path, monkeypatch) -> None:
    from codemedic.graph.builder import (
        get_run_state,
        get_trajectory,
        list_runs,
        run_workflow,
    )

    monkeypatch.chdir(tmp_path)
    result = run_workflow("invalid repository", str(tmp_path / "missing"))

    inspected = get_run_state(result.thread_id)
    runs = list_runs()
    trajectory = get_trajectory(result.state["run_id"])

    assert inspected.thread_id == result.thread_id
    assert inspected.workflow_status == result.workflow_status
    assert result.state["run_id"] in runs
    assert trajectory["run"]["run_id"] == result.state["run_id"]
    assert trajectory["events"]


def test_public_inspector_rejects_unknown_thread(tmp_path, monkeypatch) -> None:
    from codemedic.graph.builder import get_run_state

    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="No workflow state"):
        get_run_state("missing-thread")


def test_streamlit_inspector_module_is_importable_without_optional_dependency() -> None:
    from codemedic.inspector_app import main

    assert callable(main)
