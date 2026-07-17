"""Application configuration for CodeMedic.

Uses pydantic-settings for typed, validated configuration from environment variables.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """CodeMedic application settings.

    Loads from environment variables with optional .env file support.
    All values have safe defaults — never requires an API key to load.
    """

    # ── OpenAI-compatible Provider ──────────────────────────────────
    openai_api_key: str = ""
    """API key for OpenAI-compatible provider. Empty by default; set in .env."""

    openai_api_base: str = "https://api.openai.com/v1"
    """Base URL for the OpenAI-compatible API endpoint."""

    openai_model_name: str = "gpt-4o"
    """Model name to use for agent invocations."""

    openai_temperature: float = 0.0
    """Temperature for LLM calls."""

    # ── LangSmith (optional) ────────────────────────────────────────
    langchain_tracing_v2: bool = False
    """Enable LangSmith tracing."""

    langchain_api_key: str = ""
    """LangSmith API key (required only if tracing is enabled)."""

    langchain_project: str = "codemedic"
    """LangSmith project name."""

    # ── Paths ────────────────────────────────────────────────────────
    repository_path: str = ""
    """Default target repository path for diagnosis."""

    runtime_dir: str = ""
    """Directory for runtime artifacts (checkpoints, traces, sandboxes).
    Defaults to '<project_root>/runtime' if empty."""

    # ── Agent Limits ─────────────────────────────────────────────────
    max_investigation_steps: int = 6
    """Maximum tool-calling rounds for the Investigator agent."""

    max_retrieval_rounds: int = 2
    """Maximum hybrid-retrieval rounds in the workflow."""

    max_fixer_retries: int = 1
    """Maximum retry count for the Fixer agent."""

    # ── Sandbox ──────────────────────────────────────────────────────
    test_timeout_seconds: int = 120
    """Timeout for test execution in the sandbox."""

    max_output_length: int = 10000
    """Maximum characters to capture from test stdout/stderr."""

    max_file_size_bytes: int = 524_288
    """Maximum file size (bytes) the Investigator is allowed to read."""

    execution_backend: Literal["temporary", "docker"] = "temporary"
    """Backend used to execute tests. Docker is opt-in and fail-closed."""

    docker_image: str = "codemedic-runner:local"
    """Docker image used by the isolated test runner."""

    docker_timeout_seconds: int = 120
    docker_memory_limit: str = "512m"
    docker_cpu_limit: str = "1.0"
    docker_pids_limit: int = 128

    @property
    def resolved_runtime_dir(self) -> Path:
        """Return the runtime directory, defaulting to project-root/runtime."""
        if self.runtime_dir:
            return Path(self.runtime_dir).resolve()
        # Assume we're running from the project root
        return Path.cwd() / "runtime"

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        "extra": "ignore",
    }


# Global singleton – import this in application code
settings = Settings()
