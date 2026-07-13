"""Verifier agent — summarizes test results after sandbox execution.

The Verifier LLM only summarizes test results and gives recommendations.
It does NOT determine the final status — that is done by program logic.
"""

from __future__ import annotations

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from codemedic.config import settings

VERIFIER_PROMPT = """\
You are a Verifier agent. Your job is to analyze test execution results.

You will receive:
- The original issue description
- The patch that was applied
- Test results (command, returncode, stdout, stderr)

Rules:
1. Summarize which tests passed and which failed.
2. Determine if failures are related to the original issue or pre-existing.
3. Recommend whether the fix should be: accepted, retried with changes, or rejected.
4. Do NOT determine the final status yourself — only give advisory text.
5. Do NOT suggest new code or patches.
"""


def run_verifier(
    issue: str,
    patch_summary: str,
    test_results: list[dict],
) -> str:
    """Run the Verifier agent to analyze test results.

    Args:
        issue: Original issue description.
        patch_summary: Brief description of the patch applied.
        test_results: List of test result dicts from run_tests().

    Returns:
        A text analysis of the test results.
    """
    api_key = SecretStr(settings.openai_api_key) if settings.openai_api_key else None
    llm = ChatOpenAI(
        model=settings.openai_model_name,
        temperature=0,
        api_key=api_key,
        base_url=settings.openai_api_base or None,
    )

    # Format test results
    lines = []
    for r in test_results:
        lines.append(f"Command: {r['command']}")
        lines.append(f"Return code: {r['returncode']}")
        lines.append(f"Timed out: {r.get('timed_out', False)}")
        stdout = r.get("stdout", "")[:500]
        if stdout:
            lines.append(f"stdout:\n{stdout}")
        stderr = r.get("stderr", "")[:500]
        if stderr:
            lines.append(f"stderr:\n{stderr}")
        lines.append("---")

    test_report = "\n".join(lines)

    user_message = f"""Issue: {issue}

Patch applied:
{patch_summary}

Test results:
{test_report}

Analyze these results and provide a summary."""

    messages = [
        {"role": "system", "content": VERIFIER_PROMPT},
        {"role": "user", "content": user_message},
    ]

    response = llm.invoke(messages)
    return response.content if isinstance(response.content, str) else str(response.content)
