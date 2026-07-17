"""Docker-backed test execution with an explicit fail-closed boundary."""

from __future__ import annotations

import shutil
import subprocess
import time
import uuid
from enum import Enum
from pathlib import Path

from codemedic.config import settings
from codemedic.schemas.results import TestResult


class ExecutionBackend(str, Enum):
    """Supported test execution backends."""

    TEMPORARY = "temporary"
    DOCKER = "docker"


def _container_command(command: list[str]) -> list[str] | None:
    """Translate a whitelisted host Python command to the image Python."""
    from codemedic.tools.test_runner import PY, is_command_allowed

    if not command or command[0] not in {PY, "python", "python3"}:
        return None
    host_command = [PY, *command[1:]]
    if not is_command_allowed(host_command):
        return None
    return ["python", *command[1:]]


def build_docker_command(
    workspace: str | Path,
    command: list[str],
    *,
    image: str,
    timeout_seconds: int,
    memory_limit: str,
    cpu_limit: str,
    pids_limit: int,
    container_name: str | None = None,
) -> list[str]:
    """Build the constrained Docker command without exposing host secrets."""
    container_command = _container_command(command)
    if container_command is None:
        raise ValueError(f"Command not in whitelist: {' '.join(command)}")

    workspace_path = Path(workspace).resolve()
    return [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=64m",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--user",
        "1000:1000",
        "--cpus",
        cpu_limit,
        "--memory",
        memory_limit,
        "--pids-limit",
        str(pids_limit),
        "--stop-timeout",
        str(timeout_seconds),
        "--mount",
        f"type=bind,src={workspace_path},dst=/workspace,rw",
        "--workdir",
        "/workspace",
        *(["--name", container_name] if container_name else []),
        image,
        *container_command,
    ]


def _result(
    command_id: str,
    command: list[str],
    *,
    returncode: int,
    stdout: str = "",
    stderr: str = "",
    timed_out: bool = False,
    duration_ms: int = 0,
) -> TestResult:
    max_output = settings.max_output_length
    truncated = len(stdout) > max_output or len(stderr) > max_output
    return TestResult(
        command_id=command_id,
        argv=list(command),
        returncode=returncode,
        stdout=stdout[:max_output],
        stderr=stderr[:max_output],
        timed_out=timed_out,
        duration_ms=duration_ms,
        output_truncated=truncated,
    )


def _cleanup_container(container_name: str) -> None:
    """Best-effort cleanup for a timed-out or interrupted Docker process."""
    try:
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def run_tests_in_docker(
    repo_path: str,
    commands: list[list[str]] | None = None,
    *,
    timeout: int | None = None,
    image: str | None = None,
    memory_limit: str | None = None,
    cpu_limit: str | None = None,
    pids_limit: int | None = None,
) -> list[TestResult]:
    """Execute whitelisted commands in Docker, never falling back to the host."""
    from codemedic.tools.test_runner import PY

    repo = Path(repo_path).resolve()
    if not repo.is_dir():
        return [_result(
            "init", ["error"], returncode=-1,
            stderr=f"Repository not found: {repo_path}",
        )]

    cmd_list = commands or [[PY, "-m", "pytest", "-q", "-p", "no:cacheprovider"]]
    timeout_s = timeout or settings.docker_timeout_seconds
    results: list[TestResult] = []

    for cmd_id, command in enumerate(cmd_list):
        command_id = f"cmd_{cmd_id}"
        container_name = f"codemedic-test-{uuid.uuid4().hex}"
        try:
            docker_command = build_docker_command(
                repo,
                command,
                image=image or settings.docker_image,
                timeout_seconds=timeout_s,
                memory_limit=memory_limit or settings.docker_memory_limit,
                cpu_limit=cpu_limit or settings.docker_cpu_limit,
                pids_limit=pids_limit or settings.docker_pids_limit,
                container_name=container_name,
            )
        except ValueError as exc:
            results.append(_result(command_id, command, returncode=-1, stderr=str(exc)))
            continue

        if shutil.which("docker") is None:
            results.append(_result(
                command_id,
                command,
                returncode=-1,
                stderr=(
                    "Docker CLI not found; Docker backend is unavailable; "
                    "no host fallback was attempted"
                ),
            ))
            continue

        start_time = time.perf_counter()
        try:
            proc = subprocess.run(
                docker_command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_s,
                check=False,
            )
            results.append(_result(
                command_id,
                command,
                returncode=proc.returncode,
                stdout=proc.stdout or "",
                stderr=proc.stderr or "",
                duration_ms=int((time.perf_counter() - start_time) * 1000),
            ))
        except subprocess.TimeoutExpired:
            results.append(_result(
                command_id,
                command,
                returncode=-1,
                stderr=f"Docker test timed out after {timeout_s}s",
                timed_out=True,
                duration_ms=int((time.perf_counter() - start_time) * 1000),
            ))
        except (FileNotFoundError, OSError) as exc:
            results.append(_result(
                command_id,
                command,
                returncode=-1,
                stderr=f"Docker execution failed: {exc}",
                duration_ms=int((time.perf_counter() - start_time) * 1000),
            ))
        finally:
            _cleanup_container(container_name)

    return results
