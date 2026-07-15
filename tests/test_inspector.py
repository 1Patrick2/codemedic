import pytest


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
