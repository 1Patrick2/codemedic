#!/usr/bin/env python
"""CLI entry point for CodeMedic.

Usage:
    python -m codemedic.cli --issue "..." --repo-path ./demo_repos/sample_project [--log "..."]

Stage 1: Investigator single-agent baseline.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from codemedic.agents.investigator import run_investigator
from codemedic.config import settings
from codemedic.tracing.local_trace import LocalTracer


def _read_log_file(path: str) -> str:
    """Read log content from a file path."""
    log_path = Path(path)
    if not log_path.exists():
        print(f"ERROR: log file not found: {path}", file=sys.stderr)
        sys.exit(1)
    return log_path.read_text(encoding="utf-8", errors="replace")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="CodeMedic — Multi-Agent Code Diagnosis and Repair System",
    )
    sub = parser.add_subparsers(dest="command", help="Available commands")

    # ── investigate command ──────────────────────────────────────────
    inv = sub.add_parser("investigate", help="Run the Investigator agent")
    inv.add_argument("--issue", required=True, help="Issue description to investigate")
    inv.add_argument("--repo-path", required=True, help="Path to the repository root")
    inv.add_argument("--log", help="Error log content or path to log file")
    inv.add_argument("--trace-dir", help="Directory to write trace files")

    args = parser.parse_args()

    if args.command != "investigate":
        parser.print_help()
        sys.exit(1)

    # Resolve repository path
    repo_path = Path(args.repo_path).resolve()
    if not repo_path.is_dir():
        print(f"ERROR: repository path not found: {repo_path}", file=sys.stderr)
        sys.exit(1)

    # Handle error log
    error_log: str | None = args.log
    if error_log and Path(error_log).exists():
        error_log = _read_log_file(error_log)
    elif error_log and Path(error_log).is_file():
        error_log = _read_log_file(error_log)

    # Create tracer
    trace = LocalTracer(
        task_id="investigate_cli",
        trace_dir=args.trace_dir,
    )

    # Verify API key is configured
    if not settings.openai_api_key:
        print(
            "ERROR: OPENAI_API_KEY is not set. "
            "Please create a .env file or set the environment variable.",
            file=sys.stderr,
        )
        env_file = settings.model_config.get("env_file", ".env")
        print(f"  Config loaded from: {env_file}", file=sys.stderr)
        print("  Trace written to:", trace.path, file=sys.stderr)
        trace.close()
        sys.exit(1)

    print("🔍 Investigator: Investigating issue...")
    print(f"   Issue:       {args.issue}")
    print(f"   Repository:  {repo_path}")
    print(f"   Model:       {settings.openai_model_name}")
    print(f"   Trace:       {trace.path}")
    print()

    result = run_investigator(
        issue=args.issue,
        repository_path=str(repo_path),
        error_log=error_log,
        trace=trace,
    )

    print(f"\n{'=' * 60}")
    print("📋 Diagnosis Result")
    print(f"{'=' * 60}")
    print(f"\nRoot Cause: {result.root_cause}")
    print(f"Confidence: {result.confidence:.2f}")
    print(f"\nSuspected Files ({len(result.suspected_files)}):")
    for f in result.suspected_files:
        print(f"  • {f}")
    print(f"\nEvidence ({len(result.evidence)}):")
    for ev in result.evidence:
        loc = f"{ev.file_path}"
        if ev.line_start:
            loc += f":L{ev.line_start}"
            if ev.line_end:
                loc += f"-L{ev.line_end}"
        print(f"\n  [{loc}]")
        print(f"   {ev.reason}")
    print(f"\nMissing Information ({len(result.missing_information)}):")
    for m in result.missing_information:
        print(f"  • {m}")

    print(f"\nTrace saved to: {trace.path}")


if __name__ == "__main__":
    main()
