import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from codemedic.tracing.events import EVENT_TYPES, WorkflowEvent
from codemedic.tracing.reader import TrajectoryReader
from codemedic.tracing.recorder import (
    TrajectoryRecorder,
    register_recorder,
    unregister_recorder,
)
from codemedic.tracing.serializer import event_from_json, event_to_json


def test_workflow_event_round_trips_with_typed_event_type() -> None:
    event = WorkflowEvent(
        event_id="event-1",
        run_id="run-1",
        thread_id="thread-1",
        sequence=1,
        timestamp=datetime(2026, 7, 15, tzinfo=timezone.utc),
        node="intake",
        event_type="node_started",
        summary="Intake started",
        input_data={"issue": "broken factorial"},
        output_data={},
        duration_ms=None,
    )

    restored = event_from_json(event_to_json(event))

    assert restored == event
    assert set(EVENT_TYPES) >= {"node_started", "workflow_failed"}


def test_workflow_event_rejects_unknown_event_type() -> None:
    with pytest.raises(ValueError):
        WorkflowEvent(
            event_id="event-1",
            run_id="run-1",
            thread_id="thread-1",
            sequence=1,
            timestamp=datetime.now(timezone.utc),
            node="intake",
            event_type="unknown",  # type: ignore[arg-type]
            summary="invalid",
            input_data={},
            output_data={},
        )


def test_recorder_persists_ordered_redacted_events_and_artifacts(tmp_path) -> None:
    with TrajectoryRecorder("run-1", "thread-1", root_dir=tmp_path) as recorder:
        recorder.record(
            node="investigator_agent",
            event_type="model_request",
            summary="Investigator request",
            input_data={
                "api_key": "sk-secret-value",
                "prompt": "Bearer super-secret-token",
            },
        )
        recorder.record(
            node="investigator_agent",
            event_type="model_response",
            summary="Investigator response",
            output_data={"root_cause": "NameError"},
        )
        recorder.write_artifact("patch_1.diff", "diff --git a/a.py b/a.py\n")
        recorder.write_json_artifact("tests.json", [{"returncode": 0}])
        recorder.finish("completed")

    reader = TrajectoryReader(tmp_path)
    events = reader.read_events("run-1")
    run = reader.read_run("run-1")

    assert [event.sequence for event in events] == [1, 2]
    assert events[0].input_data == {
        "api_key": "[REDACTED]",
        "prompt": "Bearer [REDACTED]",
    }
    assert run["status"] == "completed"
    assert reader.read_artifact("run-1", "patch_1.diff").startswith("diff --git")
    assert json.loads(reader.read_artifact("run-1", "tests.json")) == [{"returncode": 0}]


def test_recorder_rejects_artifact_path_traversal(tmp_path) -> None:
    recorder = TrajectoryRecorder("run-1", "thread-1", root_dir=tmp_path)
    with pytest.raises(ValueError):
        recorder.write_artifact("../secret.txt", "secret")
    recorder.close()


def test_build_workflow_records_node_events_for_active_recorder(tmp_path) -> None:
    from codemedic.graph.builder import build_workflow
    from codemedic.graph.state import create_initial_state

    state = create_initial_state(
        issue="invalid repository",
        repository_path=str(tmp_path / "missing"),
        run_id="run-1",
        thread_id="thread-1",
    )
    recorder = TrajectoryRecorder("run-1", "thread-1", root_dir=tmp_path / "runs")
    register_recorder(recorder)
    try:
        build_workflow().compile().invoke(state)
    finally:
        unregister_recorder("run-1", "thread-1")
        recorder.close()

    events = TrajectoryReader(tmp_path / "runs").read_events("run-1")
    assert [(event.node, event.event_type) for event in events[:2]] == [
        ("intake", "node_started"),
        ("intake", "node_completed"),
    ]
    assert any(event.node == "final_report" for event in events)


def test_runtime_persists_workflow_trajectory_for_public_run(tmp_path, monkeypatch) -> None:
    from codemedic.graph.runtime import WorkflowRuntime

    monkeypatch.chdir(tmp_path)
    with WorkflowRuntime(checkpoint_path=tmp_path / "checkpoint.db") as runtime:
        result = runtime.run("invalid repository", str(tmp_path / "missing"))

    run_id = result.state["run_id"]
    trajectory = TrajectoryReader(tmp_path / "runtime" / "runs").read_trajectory(run_id)

    assert trajectory["run"]["thread_id"] == result.thread_id
    assert trajectory["run"]["status"] == result.workflow_status
    assert any(event["event_type"] == "workflow_failed" for event in trajectory["events"])


def test_review_interrupt_is_not_recorded_as_node_failure(tmp_path, monkeypatch) -> None:
    from codemedic.graph.runtime import WorkflowRuntime
    from tests.test_runtime import DEMO_REPO, _mock_agent_outputs

    diagnosis, patch_proposal = _mock_agent_outputs()
    monkeypatch.chdir(tmp_path)
    db_path = tmp_path / "checkpoint.db"

    with (
        patch("codemedic.graph.nodes.run_investigator", return_value=diagnosis),
        patch("codemedic.agents.fixer.run_fixer", return_value=patch_proposal),
        WorkflowRuntime(db_path) as runtime,
    ):
        first = runtime.run("Fix the Demo", DEMO_REPO)
        run_id = first.state["run_id"]
        reader = TrajectoryReader(tmp_path / "runtime" / "runs")
        events = reader.read_events(run_id)

        assert first.workflow_status == "waiting_patch_review"
        assert any(
            event.node == "patch_review" and event.event_type == "interrupt"
            for event in events
        )
        assert not any(
            event.node == "patch_review" and event.summary.endswith("failed")
            for event in events
        )
        assert not any(event.event_type == "workflow_failed" for event in events)

        runtime.resume("approved", thread_id=first.thread_id)

    events = reader.read_events(run_id)
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert any(event.event_type == "resume" for event in events)
    assert any(event.event_type == "workflow_completed" for event in events)
