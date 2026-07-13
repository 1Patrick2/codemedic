# CodeMedic Baseline Audit — 2026-07-13

## Environment

| Item | Value |
|------|-------|
| Python | 3.11.15 |
| Conda env | codemedic |
| OS | Windows 11 |
| Git branch | `fix/stabilize-core-workflow` |
| Base commit | `13834c1` — Stage 0–4 skeleton |

## Dependencies

```
pip check: No broken requirements ✅
```

| Package | Version |
|---------|---------|
| langchain | 1.3.13 |
| langgraph | 1.2.9 |
| langchain-openai | 1.3.5 |
| langgraph-checkpoint-sqlite | 3.1.0 |
| pydantic | 2.13.4 |
| pydantic-settings | 2.14.2 |

## Test Summary

```
tests/test_environment.py: 16  ✅  (env verification)
tests/test_investigator.py: 23  ✅  (tool functions + CLI)
tests/test_workflow.py:     20  ✅  (graph + nodes + routers)
                            1  ⏭️  (skipped - real LLM smoke test)
─────────────────────────────────────────────
Total: 59 passed, 1 skipped
```

### Test Coverage Gaps

| Gap | Detail |
|-----|--------|
| Agent coverage | All Agent tests use Mocks — no real LLM calls |
| Security boundary | No tests for path traversal, absolute path, UNC path |
| Structured output | No test for `DiagnosisResult` via `response_format` |
| Evidence validation | No test for invalid evidence paths/lines |
| Tool call limit | No test enforcing max tool calls |
| Fixer | `fixer_node` not tested directly |
| Diff validation | No test for patch boundary / allowed_files |
| Human Review | No test for interrupt → resume → reject flow |
| Sandbox | No test for git apply failure / temp copy isolation |
| Test Runner | No test for timeout / whitelist rejection / truncation |
| Verify router | Only stub — always returns "sufficient" |
| E2E | No end-to-end test covering the full workflow |

## Lint & Type

| Tool | Result |
|------|--------|
| ruff | All checks passed ✅ |
| mypy | Success — 22 source files ✅ |
| pip check | No broken requirements ✅ |

## Known Issues

### 1. Repository path exposed to model (A1)
Tool functions accept `repository_path` as a model-controlled parameter. A malicious or misaligned model could ask tools to read outside the target repo.

### 2. No real structured output (A2)
Uses `create_react_agent` from `langgraph.prebuilt` (deprecated path) with text-based parsing of `DiagnosisResult`. Default confidence hardcoded at 0.7 when parsing fails, which lets low-quality diagnoses pass the Evidence Gate.

### 3. No tool call limit enforcement (A2)
Max steps are only in the system prompt — no programmatic enforcement.

### 4. No Evidence validation (A2)
Evidence paths/line numbers from the model are accepted without verification against the actual file system.

### 5. Double interrupt on human review (A4)
Both `interrupt_before=["human_review"]` in compile options and `interrupt()` inside `human_review_node`. This causes two interrupts per review cycle.

### 6. Hand-written patch fallback (A5)
`_apply_with_unidiff()` doesn't respect hunk line positions — it removes lines by content match and appends new lines at the end, risking incorrect modifications.

### 7. Verify router always returns "sufficient" (A5)
`verify_router` in routers.py is a stub that always returns `SUFFICIENT`, so test failures are ignored.

### 8. allowed_files never populated (A3)
`allowed_files` is initialized empty but never populated from diagnosis evidence, so any patch boundary check is effectively skipped.

### 9. No CI pipeline
No GitHub Actions workflow for automated testing.

## Current Stage Assessment

| Stage | Status | Notes |
|-------|--------|-------|
| Stage 0 | ✅ | Environment/dev/project skeleton solid |
| Stage 1 | ⚠️ | Tool functions work but security hole (A1) |
| Stage 2 | ⚠️ | Graph structure correct but Evidence Gate too simple |
| Stage 3 | ⚠️ | Fixer + Human Review present but incomplete (A3, A4) |
| Stage 4 | ⚠️ | Sandbox + Verifier present but verify router is stub (A5) |

Overall: **Skeleton is valid but not production-ready.** All four stages need hardening before Stage 5.
