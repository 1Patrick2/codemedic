"""Preflight checks for controlled real-model proof runs."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from codemedic.config import Settings, settings


class RealModelConfigError(ValueError):
    """Raised when a real-model run lacks explicit safe configuration."""


def ensure_real_model_configured(config: Settings = settings) -> None:
    """Fail closed unless Provider, model, and a non-placeholder key exist."""
    api_key = config.openai_api_key.strip()
    placeholders = {
        "",
        "your-opencode-api-key",
        "your-api-key",
        "changeme",
    }
    if api_key.lower() in placeholders:
        raise RealModelConfigError(
            "Real-model proof requires an explicit OPENAI_API_KEY; "
            "placeholder or empty credentials are not accepted."
        )
    if not config.openai_api_base.strip():
        raise RealModelConfigError("Real-model proof requires OPENAI_API_BASE.")
    if not config.openai_model_name.strip():
        raise RealModelConfigError("Real-model proof requires OPENAI_MODEL_NAME.")


class RealModelTask(BaseModel):
    """One isolated task in the controlled real-model proof set."""

    task_id: str
    repository_path: Path
    issue: str
    error_log: str
    allowed_files: list[str] = Field(min_length=1)
    expected_files: list[str] = Field(min_length=1)
    expected_root_cause_terms: list[str] = Field(min_length=1)
    test_commands: list[list[str]] = Field(min_length=1)
    forbidden_files: list[str] = Field(min_length=1)


_TASK_ROOT = Path(__file__).resolve().parent.parent / "demo_repos" / "real_model_tasks"

REAL_MODEL_TASKS: tuple[RealModelTask, ...] = (
    RealModelTask(
        task_id="name_error_factorial",
        repository_path=_TASK_ROOT / "name_error_factorial",
        issue="factorial() raises NameError for positive integers.",
        error_log="NameError: name 'resut' is not defined",
        allowed_files=["src/math_helpers.py"],
        expected_files=["src/math_helpers.py"],
        expected_root_cause_terms=["resut", "result", "NameError"],
        test_commands=[["python", "-m", "pytest", "-q"]],
        forbidden_files=["tests/test_math_helpers.py", "README.md"],
    ),
    RealModelTask(
        task_id="type_mismatch_retry_policy",
        repository_path=_TASK_ROOT / "type_mismatch_retry_policy",
        issue="RetryPolicy.should_retry() fails because max_retries is not an integer.",
        error_log="TypeError: '<' not supported between instances of 'int' and 'str'",
        allowed_files=["src/retry_policy.py"],
        expected_files=["src/retry_policy.py"],
        expected_root_cause_terms=["max_retries", "int", "three"],
        test_commands=[["python", "-m", "pytest", "-q"]],
        forbidden_files=["tests/test_retry_policy.py", "README.md"],
    ),
    RealModelTask(
        task_id="wrong_variable_weighted_sum",
        repository_path=_TASK_ROOT / "wrong_variable_weighted_sum",
        issue="weighted_sum() raises NameError when combining values and weights.",
        error_log="NameError: name 'weight' is not defined",
        allowed_files=["src/weights.py"],
        expected_files=["src/weights.py"],
        expected_root_cause_terms=["weight", "weights", "zip"],
        test_commands=[["python", "-m", "pytest", "-q"]],
        forbidden_files=["tests/test_weights.py", "README.md"],
    ),
    RealModelTask(
        task_id="off_by_one_first_n",
        repository_path=_TASK_ROOT / "off_by_one_first_n",
        issue="first_n() returns one item too few when n is positive.",
        error_log="AssertionError: expected 3 items, received 2",
        allowed_files=["src/slicing.py"],
        expected_files=["src/slicing.py"],
        expected_root_cause_terms=["n - 1", "off-by-one", "slice"],
        test_commands=[["python", "-m", "pytest", "-q"]],
        forbidden_files=["tests/test_slicing.py", "README.md"],
    ),
    RealModelTask(
        task_id="missing_boundary_average",
        repository_path=_TASK_ROOT / "missing_boundary_average",
        issue="average() crashes on an empty input instead of returning a safe boundary value.",
        error_log="ZeroDivisionError: division by zero",
        allowed_files=["src/statistics.py"],
        expected_files=["src/statistics.py"],
        expected_root_cause_terms=["empty", "len", "boundary", "ZeroDivisionError"],
        test_commands=[["python", "-m", "pytest", "-q"]],
        forbidden_files=["tests/test_statistics.py", "README.md"],
    ),
)
