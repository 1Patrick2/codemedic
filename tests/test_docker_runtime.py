from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from codemedic.schemas.results import TestResult
from codemedic.tools.docker_runtime import (
    ExecutionBackend,
    build_docker_command,
    run_tests_in_docker,
)
from codemedic.tools.sandbox import cleanup_sandbox, create_temp_copy
from codemedic.tools.test_runner import run_tests


def test_docker_command_has_process_network_and_mount_isolation(tmp_path: Path) -> None:
    command = build_docker_command(
        tmp_path,
        ["python", "-m", "pytest", "-q"],
        image="codemedic-runner:test",
        timeout_seconds=30,
        memory_limit="512m",
        cpu_limit="1.0",
        pids_limit=64,
    )

    assert command[:3] == ["docker", "run", "--rm"]
    assert command[command.index("--network") + 1] == "none"
    assert "--read-only" in command
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert "--security-opt" in command
    assert "no-new-privileges:true" in command
    assert command[command.index("--cpus") + 1] == "1.0"
    assert command[command.index("--memory") + 1] == "512m"
    assert command[command.index("--pids-limit") + 1] == "64"
    assert "--env" not in command
    assert ".env" not in " ".join(command)
    assert command[-4:] == ["python", "-m", "pytest", "-q"]


def test_docker_runner_fails_closed_without_fallback(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("codemedic.tools.docker_runtime.shutil.which", lambda _: None)

    results = run_tests_in_docker(
        str(tmp_path),
        commands=[["python", "-m", "pytest", "-q"]],
    )

    assert len(results) == 1
    assert isinstance(results[0], TestResult)
    assert results[0].returncode == -1
    assert results[0].timed_out is False
    assert "Docker CLI not found" in results[0].stderr


def test_docker_runner_rejects_non_whitelisted_command(tmp_path: Path) -> None:
    results = run_tests_in_docker(
        str(tmp_path),
        commands=[["sh", "-c", "echo unsafe"]],
    )

    assert results[0].returncode == -1
    assert "whitelist" in results[0].stderr


def test_docker_runner_converts_timeout_to_test_result(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("codemedic.tools.docker_runtime.shutil.which", lambda _: "docker")

    def timeout(*_args: object, **_kwargs: object) -> None:
        raise subprocess.TimeoutExpired("docker", 1)

    monkeypatch.setattr("codemedic.tools.docker_runtime.subprocess.run", timeout)

    results = run_tests_in_docker(
        str(tmp_path),
        commands=[["python", "-m", "pytest", "-q"]],
        timeout=1,
    )

    assert results[0].returncode == -1
    assert results[0].timed_out is True
    assert "timed out" in results[0].stderr


def test_run_tests_dispatches_to_docker_backend(monkeypatch, tmp_path: Path) -> None:
    expected = [
        TestResult(command_id="docker", argv=["python"], returncode=0),
    ]
    monkeypatch.setattr(
        "codemedic.tools.test_runner.run_tests_in_docker",
        lambda *_args, **_kwargs: expected,
    )

    results = run_tests(
        str(tmp_path),
        execution_backend=ExecutionBackend.DOCKER,
    )

    assert results == expected


def test_initial_state_persists_execution_backend() -> None:
    from codemedic.graph.state import create_initial_state

    state = create_initial_state(
        "run tests",
        "repository",
        execution_backend=ExecutionBackend.DOCKER.value,
    )

    assert state["execution_backend"] == "docker"


def test_runtime_constructor_selects_execution_backend(tmp_path: Path) -> None:
    from codemedic.graph.runtime import WorkflowRuntime

    runtime = WorkflowRuntime(
        tmp_path / "checkpoint.db",
        execution_backend=ExecutionBackend.DOCKER,
    )
    try:
        assert runtime._execution_backend is ExecutionBackend.DOCKER
    finally:
        runtime.close()


def test_execution_backend_values_are_explicit() -> None:
    assert ExecutionBackend.TEMPORARY.value == "temporary"
    assert ExecutionBackend.DOCKER.value == "docker"


def test_temporary_workspace_excludes_environment_secrets(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("OPENAI_API_KEY=secret", encoding="utf-8")
    (tmp_path / ".env.example").write_text("OPENAI_API_KEY=", encoding="utf-8")
    sandbox = create_temp_copy(str(tmp_path))
    try:
        assert not (Path(sandbox) / ".env").exists()
        assert (Path(sandbox) / ".env.example").exists()
    finally:
        cleanup_sandbox(sandbox)


@pytest.mark.skipif(
    __import__("shutil").which("docker") is None,
    reason="Docker CLI is not available",
)
def test_docker_backend_smoke_is_opt_in() -> None:
    assert ExecutionBackend.DOCKER.value == "docker"
