# CodeMedic Real Model Proof Report

## Run metadata

- Date: 2026-07-17
- Provider: OpenAI-compatible endpoint configured through `.env`
- Model: `deepseek-v4-flash`
- Tasks: 5 controlled repairable Demo tasks
- Repeats: 3 per task, 15 total runs
- Final result directory: `runtime/evaluations/real-model-proof-rerun/`
- Earlier baseline directories remain local under `runtime/`

No API key, Authorization header, environment variable, raw model message, or absolute local path is included in this report.

## Aggregate results

| Metric | Result | Threshold | Status |
| --- | ---: | ---: | --- |
| Diagnosis valid rate | 100.0% | — | observed |
| Evidence Validation rate | 100.0% | >= 70% | meets threshold |
| Evidence file accuracy | 80.0% | — | observed |
| Evidence line accuracy | 73.3% | — | observed |
| Evidence excerpt accuracy | 73.3% | — | observed |
| Correct file rate | 93.3% | — | observed |
| Diff valid rate | 93.3% | — | observed |
| Patch Apply rate | 80.0% | >= 70% | meets threshold |
| Final test pass rate | 80.0% | >= 60% | meets threshold |
| End-to-end pass rate | 80.0% | — | observed |
| Unauthorized modification rate | 0.0% | 0% | meets threshold |
| False pass rate | 0.0% | 0% | meets threshold |
| Average tool calls | 4.53 | — | observed |
| Average token usage | 4117.40 | — | observed |
| Average latency | 29783 ms | — | observed |
| Average cost | unknown | — | Provider did not report cost |

Failure categories:

```text
PATCH_APPLY_FAILURE: 1
TOOL_CALL_FAILURE: 1
WRONG_FILE: 1
```

## Findings

1. The single-line repository-relative Evidence contract raised Evidence Validation to 100%.
2. Gold Evidence file, line, and excerpt accuracy were 80%, 73.3%, and 73.3%.
3. Three runs failed after diagnosis: one patch-apply failure, one tool-call failure, and one wrong-file result.
4. No run produced an unauthorized file modification or a false final pass.

## Decision

Stage 1 real-model proof is accepted. Continue with Evaluation data closure and deterministic Retrieval comparison; do not introduce complex Retrieval or new Agents.
