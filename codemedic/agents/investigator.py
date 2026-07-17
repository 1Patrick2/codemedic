"""Investigator agent — reads repository code and logs to diagnose issues.

Tools are bound to a validated RepositoryContext at construction time,
so the model never controls the repository root path.
Structured DiagnosisResult is extracted from the agent's final response.
"""

from __future__ import annotations

import json as json_lib
import re
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from langchain.agents import create_agent
from langchain.tools import tool
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from codemedic.agents.execution import AgentExecutionResult, extract_agent_execution
from codemedic.config import settings
from codemedic.schemas.diagnosis import DiagnosisResult, Evidence
from codemedic.tools.context import RepositoryContext
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
3. Reference one precise source line in each evidence item. Set
   line_start == line_end; do not submit multi-line ranges.
4. If the error log is provided, parse it first to understand the failure.
5. If you cannot find enough evidence, note what information is missing.
   Do not use test files as root-cause evidence for implementation bugs.
6. Do NOT modify any files — you are read-only.
7. You have a maximum of {max_steps} tool-calling rounds — use them wisely.

When you are ready, output your diagnosis as a JSON object inside
a ```json ``` code block. Use this exact schema:

```json
{{
  "root_cause": "Concise root cause sentence",
  "suspected_files": ["relative/path/to/file.py"],
  "evidence": [
    {{
      "file_path": "relative/path/to/file.py",
      "line_start": 10,
      "line_end": 10,
      "excerpt": "the relevant code line",
      "reason": "Why this supports the root cause"
    }}
  ],
  "confidence": 0.85,
  "missing_information": []
}}
```

All file paths must be repository-relative. The excerpt must be copied from
the exact source line identified by line_start. Do not cite test files as the
root cause for an implementation bug.
Only output JSON — no extra commentary outside the code block.
"""


def _extract_json(text: str) -> dict | None:
    """Extract a JSON object from the model's text response.

    Tries: ```json block, top-level object, regex object match.
    """
    # Try ```json block first
    json_block = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if json_block:
        candidate = json_block.group(1).strip()
        try:
            return json_lib.loads(candidate)
        except json_lib.JSONDecodeError:
            pass

    # Try top-level JSON object
    text_stripped = text.strip()
    if text_stripped.startswith("{"):
        try:
            return json_lib.loads(text_stripped)
        except json_lib.JSONDecodeError:
            pass

    # Try any JSON-like object
    obj_match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if obj_match:
        candidate = obj_match.group(0)
        try:
            return json_lib.loads(candidate)
        except json_lib.JSONDecodeError:
            pass

    return None


def _parse_text_diagnosis(text: str, issue: str) -> DiagnosisResult:
    """Parse the agent's text response into a structured DiagnosisResult.

    Path 1: JSON extraction + Pydantic validation (primary).
    Path 2: Regex-based text extraction (fallback).
    """
    # ── Path 1: JSON + Pydantic ──────────────────────────────────
    json_obj = _extract_json(text)
    if json_obj is not None:
        try:
            result = DiagnosisResult.model_validate(json_obj)
            result.confidence = max(0.0, min(1.0, result.confidence))
            return result
        except Exception:
            pass

    # ── Path 2: regex fallback ───────────────────────────────────
    evidence_list: list[Evidence] = []
    lines = text.splitlines()

    file_paths = set(re.findall(
        r'(?:[\w/\\\-]+)?(?:src|tests|demo_repos|scripts|config|app|main|package'
        r'|services|nodes|utils)/?[\w/\\\-]*\.(?:py|yaml|yml|json|xml|md|txt|cpp|h|hpp|launch)',
        text,
    ))
    file_paths |= set(re.findall(r'(?<!\w)([\w\-]+\.py)(?!\w)', text))

    for fpath in file_paths:
        for i, line in enumerate(lines):
            if fpath in line:
                nearby = " ".join(lines[max(0, i - 1):min(len(lines), i + 3)])
                nums = re.findall(r'(?:line|L)\s*(\d+)', nearby, re.IGNORECASE)
                start = int(nums[0]) if nums else None
                evidence_list.append(Evidence(
                    file_path=fpath.strip().strip("`").strip("*"),
                    line_start=start, line_end=start,
                    excerpt="", reason="Identified during investigation",
                ))

    missing_info: list[str] = []
    for line in lines:
        lower = line.lower()
        if any(kw in lower for kw in (
            "missing information", "needed", "need more",
            "cannot determine", "not enough", "unable to",
        )):
            cleaned = line.strip().lstrip("-#* \t")
            if cleaned and len(cleaned) < 300:
                missing_info.append(cleaned)
        elif "missing" in lower and "?" in line:
            cleaned = line.strip().lstrip("-#* \t")
            if cleaned and len(cleaned) < 300:
                missing_info.append(cleaned)

    confidence = 0.0
    conf_match = re.search(r"(?:confidence|confident)[:\s]+(\d+\.?\d*)", text, re.IGNORECASE)
    if conf_match:
        try:
            confidence = max(0.0, min(1.0, float(conf_match.group(1))))
        except ValueError:
            pass

    root_cause = issue
    skip_prefixes = (
        "##", "#", "---", "===", "**", "* ", "- ", "diagnos", "investigat",
        "root cause", "analysis", "report", "summary", "result", "conclusion",
        "based on", "after examining", "here is", "i have",
    )
    for line in lines:
        stripped = line.strip()
        if not stripped or len(stripped) < 10:
            continue
        if any(stripped.lower().startswith(p) for p in skip_prefixes):
            continue
        if len(stripped) < 200:
            root_cause = stripped[:200]
            break

    return DiagnosisResult(
        suspected_files=sorted(set(e.file_path for e in evidence_list)),
        root_cause=root_cause,
        evidence=evidence_list,
        confidence=confidence,
        missing_information=missing_info,
    )


def build_investigator(repository_root: str | Path) -> Any:
    """Build and return a compiled Investigator agent.

    Tools are bound to the given repository root at construction time.
    The model cannot change the repository path through tool arguments.

    Args:
        repository_root: Path to the repository root directory.

    Returns:
        A callable CompiledStateGraph agent.
    """
    ctx = RepositoryContext(Path(repository_root))

    @tool
    def list_repo_tree(max_depth: int = 4) -> str:
        """List the directory tree of the repository (max depth 4)."""
        return _list_repo_tree(str(ctx.root), max_depth)

    @tool
    def search_code(pattern: str, file_pattern: str = "*.py") -> str:
        """Search for a regex pattern in Python source files inside the repository."""
        return _search_code(str(ctx.root), pattern, file_pattern)

    @tool
    def read_file(file_path: str) -> str:
        """Read the content of a file from the repository (with line numbers).

        Path traversal attempts are rejected.
        """
        if not ctx.resolve_path(file_path):
            return f"ERROR: path traversal detected: {file_path}"
        return _read_file(str(ctx.root), file_path)

    @tool
    def parse_log(log_content: str) -> str:
        """Parse an error log and extract key diagnostic information."""
        return _parse_log(log_content)

    api_key = SecretStr(settings.openai_api_key) if settings.openai_api_key else None
    llm = ChatOpenAI(
        model=settings.openai_model_name,
        temperature=settings.openai_temperature,
        api_key=api_key,
        base_url=settings.openai_api_base or None,
    )

    agent = create_agent(
        llm,
        tools=[list_repo_tree, search_code, read_file, parse_log],
        system_prompt=INVESTIGATOR_PROMPT.format(max_steps=settings.max_investigation_steps),
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
    """Run Investigator and return only its validated business result."""
    execution = run_investigator_execution(
        issue,
        repository_path,
        error_log,
        trace=trace,
    )
    return DiagnosisResult.model_validate(execution.parsed_result)


def run_investigator_execution(
    issue: str,
    repository_path: str,
    error_log: str | None = None,
    *,
    trace: LocalTracer | None = None,
) -> AgentExecutionResult:
    """Run the Investigator agent against a repository issue.

    Args:
        issue: Description of the issue to investigate.
        repository_path: Path to the repository root.
        error_log: Optional error log content.
        trace: Optional LocalTracer instance for recording steps.

    Returns:
        Parsed diagnosis plus raw execution metadata for Trajectory/Evaluation.
    """
    agent = build_investigator(repository_path)
    tracer = trace or LocalTracer(task_id="investigate_cli")

    msg_parts = [
        f"Issue: {issue}",
        "Repository tools are already bound to the task repository. "
        "Use repository-relative file paths only; do not include the host path.",
    ]
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
        recursion_limit = settings.max_investigation_steps * 2 + 5
        result = agent.invoke(
            {"messages": [{"role": "user", "content": user_message}]},
            {"recursion_limit": recursion_limit},
        )

        elapsed_ms = int((time.perf_counter() - start) * 1000)
        tracer.record_step(
            node_name="investigator",
            agent_name="investigator",
            model=settings.openai_model_name,
            latency_ms=elapsed_ms,
            state_transition="completed",
            final_status="success",
        )

        messages = result.get("messages", []) if isinstance(result, Mapping) else []
        final_text = ""
        for msg in reversed(messages):
            content = getattr(msg, "content", None)
            if getattr(msg, "type", None) == "ai" and content:
                final_text = content if isinstance(content, str) else str(content)
                break

        if final_text:
            diagnosis = _parse_text_diagnosis(final_text, issue)
        else:
            diagnosis = _fallback_diagnosis(issue, str(result))

        return extract_agent_execution(
            result if isinstance(result, Mapping) else {},
            parsed_result=diagnosis,
            model=settings.openai_model_name,
            provider="openai-compatible",
            latency_ms=elapsed_ms,
        )

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
        diagnosis = DiagnosisResult(
            suspected_files=[],
            root_cause=f"Investigator agent error: {exc}",
            evidence=[],
            confidence=0.0,
            missing_information=["Agent invocation failed — check API key and network"],
        )
        return AgentExecutionResult(
            parsed_result=diagnosis.model_dump(mode="json"),
            model=settings.openai_model_name,
            provider="openai-compatible",
            latency_ms=elapsed_ms,
            error=str(exc),
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
