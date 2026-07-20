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

    trajectory = reader.read_trajectory("run-1")
    assert trajectory["artifact_contents"]["patch_1.diff"].startswith("diff --git")


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
        first = runtime.run("Fix the Demo", DEMO_REPO, review_policy="controlled_auto")
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


def test_agent_execution_metadata_is_kept_in_trajectory_not_state(
    tmp_path,
    monkeypatch,
) -> None:
    from codemedic.agents.execution import AgentExecutionResult
    from codemedic.graph.runtime import WorkflowRuntime
    from tests.test_runtime import DEMO_REPO, _mock_agent_outputs

    diagnosis, patch_proposal = _mock_agent_outputs()
    investigator_execution = AgentExecutionResult(
        parsed_result=diagnosis.model_dump(mode="json"),
        raw_text='{"root_cause":"demo"}',
        messages=[{"type": "ai", "content": "demo"}],
        tool_calls=[{"id": "call-1", "name": "read_file", "args": {"file_path": "src/app.py"}}],
        tool_results=[
            {
                "tool_call_id": "call-1",
                "name": "read_file",
                "content": "1: broken = True",
            }
        ],
        model="test-model",
        provider="test-provider",
        total_tokens=10,
        latency_ms=1.0,
    )
    fixer_execution = AgentExecutionResult(
        parsed_result=patch_proposal.model_dump(mode="json"),
        raw_text=patch_proposal.unified_diff,
        messages=[{"type": "ai", "content": patch_proposal.unified_diff}],
        model="test-model",
        provider="test-provider",
        total_tokens=20,
        latency_ms=2.0,
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "codemedic.graph.nodes.run_investigator_execution",
        lambda **_: investigator_execution,
    )
    monkeypatch.setattr(
        "codemedic.agents.fixer.run_fixer_execution",
        lambda **_: fixer_execution,
    )

    with WorkflowRuntime(tmp_path / "checkpoint.db") as runtime:
        result = runtime.run("Fix the Demo", DEMO_REPO)

    trajectory = TrajectoryReader(tmp_path / "runtime" / "runs").read_trajectory(
        result.state["run_id"]
    )
    model_responses = [
        event
        for event in trajectory["events"]
        if event["event_type"] == "model_response"
    ]

    assert any("execution" in event["output_data"] for event in model_responses)
    assert any(event["event_type"] == "tool_call" for event in trajectory["events"])
    assert any(event["event_type"] == "tool_result" for event in trajectory["events"])
    assert "execution" not in result.state


def test_trajectory_sanitizes_absolute_paths_and_credentials() -> None:
    from codemedic.tracing.recorder import sanitize_data

    sanitized = sanitize_data(
        {
            "repository_path": r"E:\\private\\repo",
            "api_key": "sk-secret-value",
            "relative_file": "src/app.py",
        }
    )

    assert sanitized["repository_path"] == "[LOCAL_PATH]"
    assert sanitized["api_key"] == "[REDACTED]"
    assert sanitized["relative_file"] == "src/app.py"


def test_trajectory_preserves_numeric_token_usage_metrics() -> None:
    from codemedic.tracing.recorder import sanitize_data

    sanitized = sanitize_data(
        {
            "prompt_tokens": 11,
            "completion_tokens": 7,
            "total_tokens": 18,
            "api_token": "secret",
        }
    )

    assert sanitized["prompt_tokens"] == 11
    assert sanitized["completion_tokens"] == 7
    assert sanitized["total_tokens"] == 18
    assert sanitized["api_token"] == "[REDACTED]"
