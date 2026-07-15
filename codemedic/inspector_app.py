"""Minimal Streamlit Run Inspector for the CodeMedic public APIs."""

from __future__ import annotations

from typing import Any


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
        diagnosis = state["diagnosis"]
        st.subheader("Diagnosis")
        st.write(
            {
                "root_cause": diagnosis.get("root_cause"),
                "confidence": diagnosis.get("confidence"),
                "suspected_files": diagnosis.get("suspected_files", []),
            }
        )
    if state.get("patch"):
        st.subheader("Patch Review")
        st.code(state["patch"].get("unified_diff", ""), language="diff")
    if state.get("test_results"):
        st.subheader("Tests")
        st.json(state["test_results"])


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
        options = ["approved", "rejected"]
        if state.get("retry_count", 0) < 1:
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


def main() -> None:
    """Render the minimal Run Task, Review, Tests, and Trajectory views."""
    st = _streamlit()
    from codemedic.config import settings
    from codemedic.graph.builder import get_run_state, get_trajectory, run_workflow

    st.set_page_config(page_title="CodeMedic Run Inspector", layout="wide")
    st.title("CodeMedic Run Inspector")

    repository_path = st.text_input("Repository Path")
    issue = st.text_area("Issue")
    error_log = st.text_area("Error Log (optional)")
    st.text_input("Model", value=settings.openai_model_name, disabled=True)

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
        st.json(get_trajectory(run_id))


if __name__ == "__main__":  # pragma: no cover
    main()
