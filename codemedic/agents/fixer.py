"""Fixer agent — generates Unified Diff patches based on diagnosis.

The Fixer only produces text (Unified Diff) — it does NOT modify any files.
Uses plain text prompting (not structured output) for Console Go compatibility,
then parses the response into a PatchProposal.
"""

from __future__ import annotations

import re

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

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

Rules:
1. Generate a complete Unified Diff that fixes ALL identified bugs.
2. Only modify files listed in the diagnosis.
3. Do NOT add new features, refactor unrelated code, or change formatting.
4. Explain the rationale for each change.
5. List any risks.
6. Do NOT write files or execute commands — only produce the diff text.

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


def _parse_fixer_response(text: str) -> PatchProposal:
    """Parse the model's text response into a PatchProposal."""
    diff_parts = []
    in_diff = False
    for line in text.splitlines():
        if line.startswith("--- a/"):
            in_diff = True
        if in_diff:
            if line.startswith("MODIFIED_FILES:"):
                break
            diff_parts.append(line)
    unified_diff = "\n".join(diff_parts)

    files_match = re.search(r"MODIFIED_FILES:\s*(.+)", text)
    rationale_match = re.search(r"RATIONALE:\s*(.+)", text, re.DOTALL)
    risks_match = re.search(r"RISKS:\s*(.+)", text, re.DOTALL)
    tests_match = re.search(r"TEST_SUGGESTIONS:\s*(.+)", text, re.DOTALL)

    modified_files = [
        f.strip() for f in (files_match.group(1).split(",") if files_match else [])
    ]
    rationale = rationale_match.group(1).strip() if rationale_match else ""
    risks_str = risks_match.group(1) if risks_match else ""
    risks = [r.strip() for r in risks_str.split("|") if r.strip()]
    tests_str = tests_match.group(1) if tests_match else ""
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
) -> PatchProposal:
    """Run the Fixer agent to produce a patch proposal.

    Args:
        issue: Original issue description.
        diagnosis: Structured diagnosis from the Investigator.
        context: Retrieved repository context (file contents).

    Returns:
        A PatchProposal with Unified Diff and rationale.
    """
    llm = build_fixer()

    user_message = f"""Issue: {issue}

Diagnosis:
- Root cause: {diagnosis.root_cause}
- Suspected files: {', '.join(diagnosis.suspected_files)}
- Evidence:
{_format_evidence(diagnosis)}
- Confidence: {diagnosis.confidence}

Repository context:
{context[:6000]}

Generate a Unified Diff patch that fixes all identified bugs."""

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]

    response = llm.invoke(messages)
    content = response.content
    if isinstance(content, str):
        result = _parse_fixer_response(content)
    else:
        result = _parse_fixer_response(str(content))
    return result


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
