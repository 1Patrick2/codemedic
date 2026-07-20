"""Fixer agent — generates Unified Diff patches based on diagnosis.

The Fixer only produces text (Unified Diff) — it does NOT modify any files.
Uses plain text prompting (not structured output) for Console Go compatibility,
then parses the response into a PatchProposal.
"""

from __future__ import annotations

import re
import time

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from codemedic.agents.execution import AgentExecutionResult, extract_agent_execution
from codemedic.config import settings
from codemedic.schemas.diagnosis import DiagnosisResult
from codemedic.schemas.patch import PatchProposal

SYSTEM_PROMPT = """\
You are a Fixer agent. Your job is to generate a Unified Diff patch that
fixes the diagnosed issue.

You will receive:
- The original issue description
- The Investigator's diagnosis (root cause, evidence, suspected files)
- Retrieved repository context (relevant file contents)
- Retry feedback when an earlier patch was rejected or its tests failed

Rules:
1. Generate a complete Unified Diff that fixes ALL identified bugs.
2. Only modify files listed in the diagnosis.
3. Do NOT add new features, refactor unrelated code, or change formatting.
4. When retry feedback is present, produce a new patch that addresses it and
   do not repeat the previous patch unchanged unless the feedback proves it
   was already correct.
5. Explain the rationale for each change.
6. List any risks.
7. Do NOT write files or execute commands — only produce the diff text.

Use standard Unified Diff format:
--- a/file.py
+++ b/file.py
@@ -start,count +start,count @@
 unchanged line
-old line
+new line

At the end of your response, include a structured summary with these exact labels:
MODIFIED_FILES: file1.py, file2.py
RATIONALE: explanation of changes
RISKS: risk1 | risk2 | risk3
TEST_SUGGESTIONS: test1 | test2
"""


_SUMMARY_LABELS = (
    "MODIFIED_FILES",
    "RATIONALE",
    "RISKS",
    "TEST_SUGGESTIONS",
)


def _strip_markdown_fences(diff_text: str) -> str:
    """Strip Markdown code-fence markers from a diff block.

    Handles:
      - Opening ```diff or ``` or ```\n
      - Closing ``` (possibly with trailing whitespace)
    Does NOT modify content inside the fences.
    """
    lines = diff_text.splitlines()
    cleaned: list[str] = []
    for line in lines:
        stripped = line.strip()
        # Skip any Markdown code-fence markers
        if stripped.startswith("```"):
            continue
        cleaned.append(line)
    return "\n".join(cleaned)


def _extract_summary_section(text: str, label: str) -> str:
    """Extract one summary field without consuming later labeled fields."""
    labels = "|".join(re.escape(item) for item in _SUMMARY_LABELS)
    pattern = rf"(?ms)^{re.escape(label)}:\s*(.*?)(?=^(?:{labels}):|\Z)"
    match = re.search(pattern, text)
    return match.group(1).strip() if match else ""


def _parse_fixer_response(text: str) -> PatchProposal:
    """Parse the model's text response into a PatchProposal.

    Extracts the Unified Diff section starting from '--- a/'.
    Strips Markdown code fences (```diff / ```) if present.
    """
    diff_parts = []
    in_diff = False
    for line in text.splitlines():
        if line.startswith("--- a/"):
            in_diff = True
        if in_diff:
            if line.startswith("MODIFIED_FILES:"):
                break
            diff_parts.append(line)
    unified_diff = _strip_markdown_fences("\n".join(diff_parts))

    modified_files = [
        f.strip() for f in _extract_summary_section(text, "MODIFIED_FILES").split(",")
        if f.strip()
    ]
    rationale = _extract_summary_section(text, "RATIONALE")
    risks_str = _extract_summary_section(text, "RISKS")
    risks = [r.strip() for r in risks_str.split("|") if r.strip()]
    tests_str = _extract_summary_section(text, "TEST_SUGGESTIONS")
    test_suggestions = [t.strip() for t in tests_str.split("|") if t.strip()]

    return PatchProposal(
        modified_files=modified_files,
        unified_diff=unified_diff,
        rationale=rationale,
        risks=risks,
        test_suggestions=test_suggestions,
    )


def build_fixer() -> ChatOpenAI:
    """Build a ChatOpenAI instance for fixer output."""
    api_key = SecretStr(settings.openai_api_key) if settings.openai_api_key else None
    return ChatOpenAI(
        model=settings.openai_model_name,
        temperature=settings.openai_temperature,
        api_key=api_key,
        base_url=settings.openai_api_base or None,
    )


def run_fixer(
    issue: str,
    diagnosis: DiagnosisResult,
    context: str,
    *,
    previous_patch: dict[str, object] | None = None,
    failure_feedback: str | None = None,
    human_feedback: str | None = None,
    verifier_summary: str | None = None,
) -> PatchProposal:
    """Run Fixer and return only its validated business result."""
    execution = run_fixer_execution(
        issue,
        diagnosis,
        context,
        previous_patch=previous_patch,
        failure_feedback=failure_feedback,
        human_feedback=human_feedback,
        verifier_summary=verifier_summary,
    )
    if execution.error:
        raise RuntimeError(execution.error)
    return PatchProposal.model_validate(execution.parsed_result)


def run_fixer_execution(
    issue: str,
    diagnosis: DiagnosisResult,
    context: str,
    *,
    previous_patch: dict[str, object] | None = None,
    failure_feedback: str | None = None,
    human_feedback: str | None = None,
    verifier_summary: str | None = None,
) -> AgentExecutionResult:
    """Run the Fixer agent to produce a patch proposal.

    Args:
        issue: Original issue description.
        diagnosis: Structured diagnosis from the Investigator.
        context: Retrieved repository context (file contents).
        previous_patch: The previous PatchProposal serialized for retry.
        failure_feedback: Test or validation failures from the previous attempt.
        human_feedback: Optional reviewer feedback for the retry.
        verifier_summary: Advisory summary from the previous verification.

    Returns:
        Parsed patch proposal plus raw execution metadata for Trajectory/Evaluation.
    """
    llm = build_fixer()

    retry_context = _format_retry_context(
        previous_patch=previous_patch,
        failure_feedback=failure_feedback,
        human_feedback=human_feedback,
        verifier_summary=verifier_summary,
    )

    user_message = f"""Issue: {issue}

Diagnosis:
- Root cause: {diagnosis.root_cause}
- Suspected files: {', '.join(diagnosis.suspected_files)}
- Evidence:
{_format_evidence(diagnosis)}
- Confidence: {diagnosis.confidence}

Repository context:
{context[:6000]}

Retry context:
{retry_context}

Generate a Unified Diff patch that fixes all identified bugs."""

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]

    started = time.perf_counter()
    try:
        response = llm.invoke(messages)
        content = response.content
        raw_text = content if isinstance(content, str) else str(content)
        parsed = _parse_fixer_response(raw_text)
        return extract_agent_execution(
            {"messages": [response]},
            parsed_result=parsed,
            model=settings.openai_model_name,
            provider="openai-compatible",
            latency_ms=(time.perf_counter() - started) * 1000,
        )
    except Exception as exc:
        empty_patch = PatchProposal(
            modified_files=[],
            unified_diff="",
            rationale="",
            risks=[],
            test_suggestions=[],
        )
        return AgentExecutionResult(
            parsed_result=empty_patch.model_dump(mode="json"),
            model=settings.openai_model_name,
            provider="openai-compatible",
            latency_ms=(time.perf_counter() - started) * 1000,
            error=str(exc),
        )


def _format_retry_context(
    *,
    previous_patch: dict[str, object] | None,
    failure_feedback: str | None,
    human_feedback: str | None,
    verifier_summary: str | None,
) -> str:
    """Format prior attempt data so retries can respond to concrete feedback."""
    if not any((previous_patch, failure_feedback, human_feedback, verifier_summary)):
        return "(first attempt; no retry feedback)"

    sections: list[str] = []
    if previous_patch:
        sections.append(
            "Previous patch:\n"
            + str(previous_patch.get("unified_diff", "(no diff recorded)"))
        )
    if failure_feedback:
        sections.append(f"Test/validation feedback:\n{failure_feedback}")
    if human_feedback:
        sections.append(f"Human feedback:\n{human_feedback}")
    if verifier_summary:
        sections.append(f"Verifier summary:\n{verifier_summary}")
    return "\n\n".join(sections)


def _format_evidence(diagnosis: DiagnosisResult) -> str:
    """Format evidence items into a readable string."""
    lines = []
    for i, ev in enumerate(diagnosis.evidence, start=1):
        loc = f"{ev.file_path}"
        if ev.line_start:
            loc += f":L{ev.line_start}"
            if ev.line_end and ev.line_end != ev.line_start:
                loc += f"-L{ev.line_end}"
        lines.append(f"  {i}. [{loc}] {ev.reason}")
        if ev.excerpt:
            lines.append(f"     {ev.excerpt[:120]}")
    return "\n".join(lines) if lines else "  (no evidence provided)"
