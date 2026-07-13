"""Verify the basic project environment is healthy."""

import importlib
import sys
from pathlib import Path

import pytest


def test_python_version() -> None:
    """Python must be 3.11.x for LangChain/LangGraph compatibility."""
    assert sys.version_info.major == 3
    assert sys.version_info.minor == 11


@pytest.mark.parametrize(
    ("module_name", "expected_attr"),
    [
        ("langchain.agents", "create_agent"),
        ("langchain.tools", "tool"),
        ("langgraph.graph", "StateGraph"),
        ("langgraph.types", "interrupt"),
        ("langgraph.types", "Command"),
        ("langgraph.prebuilt", "create_react_agent"),
        ("langgraph.checkpoint.sqlite", "SqliteSaver"),
        ("pydantic", "BaseModel"),
        ("pydantic_settings", "BaseSettings"),
        ("unidiff", "PatchSet"),
    ],
)
def test_core_imports(module_name: str, expected_attr: str) -> None:
    """Key APIs must be importable at runtime."""
    module = importlib.import_module(module_name)
    assert hasattr(module, expected_attr), (
        f"{module_name} does not export {expected_attr}"
    )


def test_config_defaults() -> None:
    """Settings loads with safe defaults — API key may be from .env."""
    from codemedic.config import Settings

    s = Settings()
    # api_key can be empty (default) or populated from .env — both are valid
    assert isinstance(s.openai_api_key, str)
    assert s.max_investigation_steps == 6
    assert s.max_retrieval_rounds == 2
    assert s.max_fixer_retries == 1


def test_config_defaults_without_env(monkeypatch) -> None:
    """Without .env file, Settings has safe defaults."""
    from codemedic.config import Settings

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.openai_api_key == ""
    assert s.max_investigation_steps == 6


def test_config_resolves_runtime_dir() -> None:
    """Runtime dir property returns a valid Path."""
    from codemedic.config import Settings

    s = Settings()
    path = s.resolved_runtime_dir
    assert isinstance(path, Path)
    assert path.name == "runtime"


def test_env_example_has_no_real_keys() -> None:
    """The .env.example file must not contain real API keys."""
    env_example = Path(__file__).resolve().parent.parent / ".env.example"
    assert env_example.exists(), ".env.example not found"
    content = env_example.read_text(encoding="utf-8")
    # Flag patterns that look like real keys (with actual secret payload)
    # Placeholders like "your-api-key-here" are fine.
    suspicious = ["sk-proj-", "sk-ant-"]
    for token in suspicious:
        assert token not in content, f".env.example contains suspicious token: {token}"


def test_runtime_dir_not_in_git() -> None:
    """runtime directories should be gitignored."""
    gitignore = Path(__file__).resolve().parent.parent / ".gitignore"
    assert gitignore.exists(), ".gitignore not found"
    content = gitignore.read_text(encoding="utf-8")
    assert "runtime/checkpoints/" in content
    assert "runtime/traces/" in content
    assert "runtime/sandboxes/" in content
