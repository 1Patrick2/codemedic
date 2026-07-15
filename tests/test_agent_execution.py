from __future__ import annotations

from dataclasses import dataclass

import pytest


@dataclass
class FakeMessage:
    type: str
    content: object
    tool_calls: list[dict[str, object]] | None = None
    tool_call_id: str | None = None
    name: str | None = None
    usage_metadata: dict[str, int] | None = None


class FakeAgent:
    def __init__(self, result: dict[str, object]) -> None:
        self.result = result

    def invoke(self, _input: object, _config: object) -> dict[str, object]:
        return self.result


class FakeLlm:
    def __init__(self, response: FakeMessage) -> None:
        self.response = response

    def invoke(self, _messages: object) -> FakeMessage:
        return self.response


def test_extract_agent_execution_captures_messages_tools_and_usage() -> None:
    from codemedic.agents.execution import extract_agent_execution

    result = {
        "messages": [
            FakeMessage(
                type="ai",
                content="I will inspect the file.",
                tool_calls=[
                    {
                        "id": "call-1",
                        "name": "read_file",
                        "args": {"file_path": "src/app.py"},
                    }
                ],
            ),
            FakeMessage(
                type="tool",
                content="1: broken = True",
                tool_call_id="call-1",
                name="read_file",
            ),
            FakeMessage(
                type="ai",
                content="{\"root_cause\": \"broken variable\"}",
                usage_metadata={
                    "input_tokens": 11,
                    "output_tokens": 7,
                    "total_tokens": 18,
                },
            ),
        ]
    }

    execution = extract_agent_execution(
        result,
        parsed_result={"root_cause": "broken variable"},
        model="test-model",
        provider="test-provider",
        latency_ms=12.5,
    )

    assert execution.raw_text == '{"root_cause": "broken variable"}'
    assert execution.model == "test-model"
    assert execution.provider == "test-provider"
    assert execution.prompt_tokens == 11
    assert execution.completion_tokens == 7
    assert execution.total_tokens == 18
    assert execution.tool_calls == [
        {
            "id": "call-1",
            "name": "read_file",
            "args": {"file_path": "src/app.py"},
        }
    ]
    assert execution.tool_results == [
        {
            "tool_call_id": "call-1",
            "name": "read_file",
            "content": "1: broken = True",
        }
    ]
    assert execution.messages[0]["type"] == "ai"
    assert execution.model_dump()["parsed_result"] == {
        "root_cause": "broken variable"
    }


def test_agent_execution_serializes_errors_without_losing_parsed_result() -> None:
    from codemedic.agents.execution import AgentExecutionResult

    execution = AgentExecutionResult(
        parsed_result={"confidence": 0.0},
        model="test-model",
        provider=None,
        latency_ms=2.0,
        error="provider timeout",
    )

    restored = AgentExecutionResult.model_validate(execution.model_dump())

    assert restored == execution
    assert restored.error == "provider timeout"


def test_real_model_config_gate_rejects_missing_or_placeholder_credentials() -> None:
    from codemedic.config import Settings
    from codemedic.real_model import RealModelConfigError, ensure_real_model_configured

    with pytest.raises(RealModelConfigError):
        ensure_real_model_configured(Settings(openai_api_key=""))

    with pytest.raises(RealModelConfigError):
        ensure_real_model_configured(Settings(openai_api_key="your-opencode-api-key"))


def test_real_model_config_gate_accepts_explicit_openai_compatible_settings() -> None:
    from codemedic.config import Settings
    from codemedic.real_model import ensure_real_model_configured

    settings = Settings(
        openai_api_key="sk-test-configured",
        openai_api_base="https://provider.example/v1",
        openai_model_name="repair-model",
    )

    ensure_real_model_configured(settings)


def test_investigator_execution_entrypoint_returns_parsed_and_raw_metadata(
    tmp_path,
    monkeypatch,
) -> None:
    from codemedic.agents import investigator

    monkeypatch.chdir(tmp_path)
    response = {
        "messages": [
            FakeMessage(
                type="ai",
                content=(
                    '```json\n{"root_cause":"broken variable",'
                    '"suspected_files":[],"evidence":[],"confidence":0.7,'
                    '"missing_information":[]}\n```'
                ),
                usage_metadata={
                    "input_tokens": 4,
                    "output_tokens": 6,
                    "total_tokens": 10,
                },
            )
        ]
    }
    monkeypatch.setattr(
        investigator,
        "build_investigator",
        lambda _repository_path: FakeAgent(response),
    )

    execution = investigator.run_investigator_execution(
        "Find the bug",
        str(tmp_path),
    )

    assert execution.parsed_result["root_cause"] == "broken variable"
    assert execution.raw_text is not None
    assert execution.total_tokens == 10
    assert execution.error is None


def test_fixer_execution_entrypoint_returns_parsed_and_raw_metadata(monkeypatch) -> None:
    from codemedic.agents import fixer
    from codemedic.schemas.diagnosis import DiagnosisResult

    response = FakeMessage(
        type="ai",
        content=(
            "--- a/src/app.py\n+++ b/src/app.py\n"
            "@@ -1,1 +1,1 @@\n-old\n+new\n"
            "MODIFIED_FILES: src/app.py\n"
            "RATIONALE: fix\nRISKS: none\nTEST_SUGGESTIONS: pytest"
        ),
        usage_metadata={"input_tokens": 8, "output_tokens": 12, "total_tokens": 20},
    )
    monkeypatch.setattr(fixer, "build_fixer", lambda: FakeLlm(response))

    execution = fixer.run_fixer_execution(
        "Fix the bug",
        DiagnosisResult(
            suspected_files=["src/app.py"],
            root_cause="broken variable",
            evidence=[],
            confidence=0.9,
            missing_information=[],
        ),
        "src/app.py content",
    )

    assert execution.parsed_result["modified_files"] == ["src/app.py"]
    assert execution.raw_text is not None
    assert execution.total_tokens == 20
    assert execution.error is None
