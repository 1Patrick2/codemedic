"""Opt-in Docker integration tests for the isolated execution backend."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from codemedic.config import settings
from codemedic.tools.docker_runtime import ExecutionBackend
from codemedic.tools.sandbox import apply_patch, cleanup_sandbox, create_temp_copy
from codemedic.tools.test_runner import run_tests
from tests.test_real_sandbox_e2e import FIRST_RETRY_PATCH, TARGET

DEMO_REPO = Path(__file__).resolve().parent.parent / "demo_repos" / "sample_project"


def _docker_image_available() -> bool:
    if shutil.which("docker") is None:
        return False
    result = subprocess.run(
        ["docker", "image", "inspect", settings.docker_image],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


pytestmark = pytest.mark.skipif(
    not _docker_image_available(),
    reason="Docker CLI or the configured test image is unavailable",
)


def test_docker_demo_patch_and_tests_succeed() -> None:
    sandbox = create_temp_copy(str(DEMO_REPO))
    try:
        applied = apply_patch(sandbox, FIRST_RETRY_PATCH)
        assert applied["success"] is True, applied
        results = run_tests(sandbox, execution_backend=ExecutionBackend.DOCKER)
        assert results
        assert all(result.returncode == 0 for result in results)
        assert all(not result.timed_out for result in results)
    finally:
        cleanup_sandbox(sandbox)


def test_docker_test_failure_is_structured() -> None:
    sandbox = create_temp_copy(str(DEMO_REPO))
    try:
        target = Path(sandbox) / TARGET
        target.write_text(target.read_text(encoding="utf-8") + "\nassert False\n", encoding="utf-8")
        results = run_tests(sandbox, execution_backend=ExecutionBackend.DOCKER)
        assert results
        assert any(result.returncode != 0 for result in results)
        assert all(not result.timed_out for result in results)
    finally:
        cleanup_sandbox(sandbox)
