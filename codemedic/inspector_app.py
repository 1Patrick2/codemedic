"""Minimal Streamlit Run Inspector for the CodeMedic public APIs."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from pydantic import BaseModel

_TRAJECTORY_CATEGORIES: dict[str, set[str]] = {
    "model": {"model_request", "model_response"},
    "tool": {"tool_call", "tool_result"},
    "validation": {"validation", "patch_apply", "test_result"},
    "human": {"interrupt", "resume"},
    "error": {"workflow_failed"},
}


def _as_mapping(value: object) -> dict[str, Any]:
    """Convert state values to a display-safe mapping."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _trajectory_events(
    trajectory: Mapping[str, Any],
    event_types: set[str] | None = None,
    category: str | None = None,
) -> list[dict[str, Any]]:
    """Return JSON-safe trajectory events, optionally filtered by type."""
    events: list[dict[str, Any]] = []
    category_types = _TRAJECTORY_CATEGORIES.get(category or "")
    for raw_event in trajectory.get("events", []):
        event = _as_mapping(raw_event)
        if not event:
            continue
        if event_types and event.get("event_type") not in event_types:
            continue
        if category_types and event.get("event_type") not in category_types:
            continue
        events.append(event)
    return events


def _filter_evaluation_runs(
    runs: Sequence[Mapping[str, Any]],
    *,
    task_id: str | None = None,
    model: str | None = None,
    failure_category: str | None = None,
) -> list[dict[str, Any]]:
    """Filter persisted evaluation rows for presentation only."""
    filtered: list[dict[str, Any]] = []
    for raw_run in runs:
        run = _as_mapping(raw_run)
        if task_id and run.get("task_id") != task_id:
            continue
        if model and run.get("model") != model:
            continue
        if failure_category and run.get("failure_category") != failure_category:
            continue
        filtered.append(run)
    return filtered


def _streamlit() -> Any:
    try:
        import streamlit as st
    except ImportError as exc:  # pragma: no cover - exercised when optional UI is absent
        raise RuntimeError(
            "Streamlit is optional. Install the 'streamlit' extra to run the Inspector."
        ) from exc
    return st


def _show_state(st: Any, result: Any) -> None:
    state = result.state
    st.subheader("Workflow State")
    st.write(
        {
            "task_id": result.task_id,
            "thread_id": result.thread_id,
            "run_id": state.get("run_id"),
            "workflow_status": result.workflow_status,
            "interrupted": result.interrupted,
        }
    )

    if state.get("diagnosis"):
        diagnosis = _as_mapping(state["diagnosis"])
        st.subheader("Diagnosis")
        st.write(
            {
                "root_cause": diagnosis.get("root_cause"),
                "confidence": diagnosis.get("confidence"),
                "suspected_files": diagnosis.get("suspected_files", []),
                "missing_information": diagnosis.get("missing_information", []),
            }
        )
        st.write(
            {
                "evidence": [
                    _as_mapping(item) for item in diagnosis.get("evidence", [])
                ],
                "evidence_validation": _as_mapping(state.get("evidence_validation")),
                "allowed_files": state.get("allowed_files", []),
                "approved_files": state.get("approved_files", []),
            }
        )
    if state.get("patch"):
        st.subheader("Patch Review")
        patch = _as_mapping(state["patch"])
        st.code(patch.get("unified_diff", ""), language="diff")
        st.write(
            {
                "modified_files": state.get(
                    "modified_files", patch.get("modified_files", [])
                ),
                "diff_validation": _as_mapping(state.get("diff_validation")),
                "risks": patch.get("risks", state.get("risks", [])),
                "test_suggestions": patch.get(
                    "test_suggestions", state.get("test_suggestions", [])
                ),
            }
        )
    if state.get("test_results"):
        st.subheader("Tests")
        st.json([_as_mapping(item) for item in state["test_results"]])


def _show_review(st: Any, result: Any) -> None:
    if not result.interrupted:
        return

    state = result.state
    review_type = (
        "diagnosis"
        if result.workflow_status == "waiting_diagnosis_review"
        else "patch"
    )
    st.subheader(f"{review_type.title()} Review")
    if review_type == "diagnosis":
        decision = st.selectbox("Decision", ["accept_diagnosis", "reject"])
        approved_text = st.text_area(
            "Approved Files (one repository-relative path per line)",
            value="\n".join(state.get("approved_files", [])),
        )
        approved_files = [line.strip() for line in approved_text.splitlines() if line.strip()]
    else:
        from codemedic.config import settings

        options = ["approved", "rejected"]
        if state.get("retry_count", 0) < settings.max_fixer_retries:
            options.append("retry")
        decision = st.selectbox("Decision", options)
        approved_files = None
    reason = st.text_area("Reason / Retry Feedback")

    if st.button("Resume Workflow"):
        from codemedic.graph.builder import resume_workflow

        result = resume_workflow(
            decision,
            reason,
            thread_id=result.thread_id,
            approved_files=approved_files,
        )
        st.session_state["workflow_result"] = result
        st.rerun()


def _show_patch_history(st: Any, trajectory: Mapping[str, Any]) -> None:
    """Render ordered patch artifacts so retry revisions can be compared."""
    contents = _as_mapping(trajectory.get("artifact_contents"))
    patch_names = sorted(
        name
        for name in trajectory.get("artifacts", [])
        if isinstance(name, str) and name.startswith("patch_") and name.endswith(".diff")
    )
    if not patch_names:
        return

    st.subheader("Patch History")
    for name in patch_names:
        st.write(name)
        content = contents.get(name)
        if isinstance(content, str):
            st.code(content, language="diff")


def _show_evaluation(
    st: Any,
    evaluation_ids: list[str],
    summary_loader: Callable[[str], Mapping[str, Any]],
    runs_loader: Callable[[str], list[Any]],
) -> None:
    """Render Evaluation data supplied by the public read-only API."""
    st.subheader("Evaluation")
    if not evaluation_ids:
        st.write("No persisted evaluations available.")
        return

    selected = st.selectbox("Evaluation", evaluation_ids)
    summary = dict(summary_loader(selected))
    all_runs = [_as_mapping(run) for run in runs_loader(selected)]
    task_options = ["All"] + sorted(
        {str(run["task_id"]) for run in all_runs if run.get("task_id")}
    )
    model_options = ["All"] + sorted(
        {str(run["model"]) for run in all_runs if run.get("model")}
    )
    failure_options = ["All"] + sorted(
        {
            str(run["failure_category"])
            for run in all_runs
            if run.get("failure_category")
        }
    )
    task_filter = st.selectbox("Evaluation Task", task_options)
    model_filter = st.selectbox("Evaluation Model", model_options)
    failure_filter = st.selectbox("Evaluation Failure", failure_options)
    runs = _filter_evaluation_runs(
        all_runs,
        task_id=None if task_filter == "All" else task_filter,
        model=None if model_filter == "All" else model_filter,
        failure_category=None if failure_filter == "All" else failure_filter,
    )
    st.write(
        {
            "evaluation_id": selected,
            "summary": summary,
            "run_count": len(runs),
        }
    )
    st.json(runs)


def main() -> None:
    """Render the minimal Run Task, Review, Tests, and Trajectory views."""
    st = _streamlit()
    from codemedic.config import settings
    from codemedic.evaluation.api import (
        get_evaluation_runs,
        get_evaluation_summary,
        list_evaluations,
    )
    from codemedic.graph.builder import get_run_state, get_trajectory, run_workflow

    st.set_page_config(page_title="CodeMedic Run Inspector", layout="wide")
    st.title("CodeMedic Run Inspector")

    repository_path = st.text_input("Repository Path")
    issue = st.text_area("Issue")
    error_log = st.text_area("Error Log (optional)")
    st.text_input("Model", value=settings.openai_model_name, disabled=True)

    _show_evaluation(
        st,
        list_evaluations(),
        get_evaluation_summary,
        get_evaluation_runs,
    )

    if st.button("Run Task"):
        result = run_workflow(issue, repository_path, error_log or None)
        st.session_state["workflow_result"] = result

    result = st.session_state.get("workflow_result")
    if result is None:
        return

    try:
        result = get_run_state(result.thread_id)
        st.session_state["workflow_result"] = result
    except ValueError:
        pass

    _show_state(st, result)
    _show_review(st, result)

    run_id = result.state.get("run_id")
    if run_id:
        st.subheader("Trajectory")
        trajectory = get_trajectory(run_id)
        event_type_values: set[str] = set()
        for event in _trajectory_events(trajectory):
            event_type = event.get("event_type")
            if isinstance(event_type, str):
                event_type_values.add(event_type)
        event_types = sorted(event_type_values)
        selected_types = st.multiselect(
            "Trajectory event types",
            event_types,
            default=event_types,
        )
        category_options = ["all", *sorted(_TRAJECTORY_CATEGORIES)]
        selected_category = st.selectbox("Trajectory category", category_options)
        st.json(
            {
                "run": trajectory.get("run", {}),
                "events": _trajectory_events(
                    trajectory,
                    set(selected_types),
                    category=None if selected_category == "all" else selected_category,
                ),
            }
        )
        _show_patch_history(st, trajectory)


if __name__ == "__main__":  # pragma: no cover
    main()
