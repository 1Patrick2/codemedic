# CodeMedic Real Model Proof Report

## Run metadata

- Date: 2026-07-16
- Provider: OpenAI-compatible endpoint configured through `.env`
- Model: `deepseek-v4-flash`
- Tasks: 5 controlled repairable Demo tasks
- Repeats: 3 per task, 15 total runs
- Final result directory: `runtime/evaluations/real-model-proof-final/`
- Earlier Harness-failure run: `runtime/evaluations/real-model-proof/`

No API key, Authorization header, environment variable, raw model message, or absolute local path is included in this report.

## Aggregate results

| Metric | Result | Threshold | Status |
| --- | ---: | ---: | --- |
| Diagnosis valid rate | 100.0% | — | observed |
| Evidence Validation rate | 6.7% | >= 70% | below threshold |
| Evidence file accuracy | 60.0% | — | observed |
| Evidence line accuracy | 0.0% | — | observed |
| Evidence excerpt accuracy | 80.0% | — | observed |
| Correct file rate | 93.3% | — | observed |
| Diff valid rate | 93.3% | — | observed |
| Patch Apply rate | 60.0% | >= 70% | below threshold |
| Final test pass rate | 60.0% | >= 60% | meets threshold |
| End-to-end pass rate | 0.0% | — | observed |
| Unauthorized modification rate | 0.0% | 0% | meets threshold |
| False pass rate | 0.0% | 0% | meets threshold |
| Average tool calls | 5.13 | — | observed |
| Average token usage | 4102.67 | — | observed |
| Average latency | 27458 ms | — | observed |

Failure categories:

```text
TOOL_CALL_FAILURE: 6
```

Provider cost was not reported by the current execution metadata and is therefore not treated as zero cost.

## Findings

1. The initial 15-run attempt was invalid as measurement data because trajectory token redaction replaced numeric `total_tokens` with `[REDACTED]`. The redaction rule was corrected and regression-tested before the final run.
2. The final run reached the model and produced valid diagnoses in all 15 runs.
3. Evidence failures were caused by strict line/excerpt validation: models frequently returned multi-line ranges or included test-file evidence, while the deterministic validator requires repository-relative, line-consistent evidence.
4. Six runs were classified as `TOOL_CALL_FAILURE`; their tool requests included invalid repository-relative file references or failed tool operations.
5. No run produced an unauthorized file modification or a false final pass.

## Decision

Stage 1 real-model proof is not accepted yet because Evidence Validation and Patch Apply are below threshold. Do not use this data to select a Retrieval baseline or introduce a more complex Retrieval system. The next work item is targeted diagnosis of tool-call path discipline and Evidence line-range behavior, followed by a controlled rerun.
