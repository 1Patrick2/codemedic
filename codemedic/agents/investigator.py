"""Investigator agent — reads repository code and logs to diagnose issues.

Uses LangGraph create_react_agent with 4 read-only tools.
Structured DiagnosisResult is extracted from the agent's final response.
"""

from __future__ import annotations

import re
import time
from typing import Any

from langchain.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from pydantic import SecretStr

from codemedic.config import settings
from codemedic.schemas.diagnosis import DiagnosisResult, Evidence
from codemedic.tools.repository import list_repo_tree as _list_repo_tree
from codemedic.tools.repository import parse_log as _parse_log
from codemedic.tools.repository import read_file as _read_file
from codemedic.tools.repository import search_code as _search_code
from codemedic.tracing.local_trace import LocalTracer

INVESTIGATOR_PROMPT = """\
You are an Investigator agent. Your job is to diagnose the root cause of
a software issue.

You have access to a repository and an optional error log. Use the available
tools to explore the codebase, search for relevant code, read files, and
analyze log output.

Rules:
1. Always search for relevant code before jumping to conclusions.
2. Read suspicious files to confirm your hypotheses.
3. Reference specific file paths and line numbers in your evidence.
4. If the error log is provided, parse it first to understand the failure.
5. If you cannot find enough evidence, note what information is missing.
6. Do NOT modify any files — you are read-only.
7. You have a maximum of {max_steps} tool-calling rounds — use them wisely.

When you are ready with your diagnosis, produce a structured report with:
- Root cause (one concise sentence)
- Suspected files (list of file paths)
- Evidence (file path, line numbers, relevant excerpt, why it matters)
- Confidence (0.0 to 1.0)
- Missing information (what else would help)
"""


def _parse_text_diagnosis(text: str, issue: str) -> DiagnosisResult:
    """Parse the agent's text response into a structured DiagnosisResult.

    Attempts to extract fields from structured text; falls back to using
    the full text as the root cause if parsing fails.
    """
    evidence_list: list[Evidence] = []
    lines = text.splitlines()

    # Extract all suspected .py file paths mentioned
    file_paths = set(re.findall(r'(?:src|tests|demo_repos)/[\w/\\\-\.]+\.py', text))

    # Try to extract evidence: "File: xxx.py:40" or "`xxx.py` line 40"
    for fpath in file_paths:
        # Find line numbers mentioned near this file
        for i, line in enumerate(lines):
            if fpath in line:
                # Look for line numbers in this or nearby lines
                nearby = " ".join(lines[max(0, i - 1):min(len(lines), i + 3)])
                nums = re.findall(r'(?:line|L)\s*(\d+)', nearby, re.IGNORECASE)
                start = int(nums[0]) if nums else None
                evidence_list.append(
                    Evidence(
                        file_path=fpath,
                        line_start=start,
                        line_end=start,
                        excerpt=line.strip()[:200],
                        reason="Identified during investigation",
                    )
                )

    # Also find line numbers in markdown bold like **Line 40**
    if not any(e.line_start for e in evidence_list):
        md_pattern = (
            r'(?:`([^`]+\.py)`|[*-]+\s*\*\*?([^*]+)\*\*?\s*:?)\s*'
            r'(?:line\s*(\d+))?'
        )
        for match in re.finditer(md_pattern, text, re.IGNORECASE):
            path = match.group(1) or match.group(2) or ""
            if ".py" in path and not path.startswith("test_"):
                line_num = match.group(3)
                start = int(line_num) if line_num else None
                exists = any(e.file_path == path for e in evidence_list)
                if not exists:
                    evidence_list.append(
                        Evidence(
                            file_path=path.strip().strip("`").strip("*"),
                            line_start=start,
                            line_end=start,
                            excerpt="",
                            reason="Mentioned in analysis",
                        )
                    )

    # Try to find confidence as a decimal number
    confidence = 0.7
    conf_match = re.search(r"(?:confidence|confident)[:\s]+(\d+\.?\d*)", text, re.IGNORECASE)
    if conf_match:
        try:
            confidence = float(conf_match.group(1))
            confidence = max(0.0, min(1.0, confidence))
        except ValueError:
            pass

    # Use the Analysis section as root cause
    root_cause = issue
    for line in lines:
        stripped = line.strip().strip("#").strip()
        if stripped and not stripped.startswith("==") and len(stripped) < 200:
            root_cause = stripped
            break

    suspected_files = sorted(set(e.file_path for e in evidence_list))

    return DiagnosisResult(
        suspected_files=suspected_files,
        root_cause=root_cause,
        evidence=evidence_list,
        confidence=confidence,
        missing_information=[],
    )


def build_investigator() -> Any:
    """Build and return a compiled Investigator agent.

    Uses create_react_agent with 4 read-only tools.
    Structured output is handled by parsing the text response.

    Returns:
        A callable CompiledStateGraph agent.
    """

    # ── Wrap tool functions with LangChain @tool decorator ──────────

    @tool
    def list_repo_tree(repository_path: str) -> str:
        """List the directory tree of a repository (max depth 4)."""
        return _list_repo_tree(repository_path)

    @tool
    def search_code(repository_path: str, pattern: str) -> str:
        """Search for a regex pattern in Python source files inside the repository."""
        return _search_code(repository_path, pattern)

    @tool
    def read_file(repository_path: str, file_path: str) -> str:
        """Read the content of a file from the repository (with line numbers)."""
        return _read_file(repository_path, file_path)

    @tool
    def parse_log(log_content: str) -> str:
        """Parse an error log and extract key diagnostic information."""
        return _parse_log(log_content)

    # ── Model ───────────────────────────────────────────────────────

    api_key = SecretStr(settings.openai_api_key) if settings.openai_api_key else None
    llm = ChatOpenAI(
        model=settings.openai_model_name,
        temperature=settings.openai_temperature,
        api_key=api_key,
        base_url=settings.openai_api_base or None,
    )

    # ── Build agent ─────────────────────────────────────────────────

    agent = create_react_agent(
        llm,
        tools=[list_repo_tree, search_code, read_file, parse_log],
        prompt=INVESTIGATOR_PROMPT.format(max_steps=settings.max_investigation_steps),
        name="investigator",
    )
    return agent


def run_investigator(
    issue: str,
    repository_path: str,
    error_log: str | None = None,
    *,
    trace: LocalTracer | None = None,
) -> DiagnosisResult:
    """Run the Investigator agent against a repository issue.

    Args:
        issue: Description of the issue to investigate.
        repository_path: Path to the repository root.
        error_log: Optional error log content.
        trace: Optional LocalTracer instance for recording steps.

    Returns:
        A DiagnosisResult with root cause and evidence.
    """
    agent = build_investigator()
    tracer = trace or LocalTracer(task_id="investigate_cli")

    # Build user message
    msg_parts = [f"Issue: {issue}", f"Repository path: {repository_path}"]
    if error_log:
        msg_parts.append(f"\nError log:\n{error_log}")

    user_message = "\n\n".join(msg_parts)

    start = time.perf_counter()
    tracer.record_step(
        node_name="investigator",
        agent_name="investigator",
        model=settings.openai_model_name,
        state_transition="invoking agent",
    )

    try:
        result = agent.invoke({"messages": [{"role": "user", "content": user_message}]})

        elapsed_ms = int((time.perf_counter() - start) * 1000)
        tracer.record_step(
            node_name="investigator",
            agent_name="investigator",
            model=settings.openai_model_name,
            latency_ms=elapsed_ms,
            state_transition="completed",
            final_status="success",
        )

        # Extract the final AI message as text
        messages = result.get("messages", [])
        final_text = ""
        for msg in reversed(messages):
            if getattr(msg, "type", None) == "ai" and msg.content:
                final_text = msg.content
                break

        if final_text:
            diagnosis = _parse_text_diagnosis(final_text, issue)
            return diagnosis

        # Fallback: construct from raw result
        return _fallback_diagnosis(issue, str(result))

    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        tracer.record_step(
            node_name="investigator",
            agent_name="investigator",
            model=settings.openai_model_name,
            latency_ms=elapsed_ms,
            state_transition="error",
            error=str(exc),
            final_status="error",
        )
        return DiagnosisResult(
            suspected_files=[],
            root_cause=f"Investigator agent error: {exc}",
            evidence=[],
            confidence=0.0,
            missing_information=["Agent invocation failed — check API key and network"],
        )

    finally:
        tracer.close()


def _fallback_diagnosis(issue: str, raw: str) -> DiagnosisResult:
    """Construct a minimal DiagnosisResult when the agent output is unparseable."""
    return DiagnosisResult(
        suspected_files=[],
        root_cause=f"Could not extract structured diagnosis. Raw output: {raw[:500]}",
        evidence=[],
        confidence=0.0,
        missing_information=["Structured output parsing failed"],
    )
