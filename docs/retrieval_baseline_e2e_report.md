# Retrieval Baseline End-to-End Comparison

## Scope

- Provider: configured OpenAI-compatible endpoint
- Model: `deepseek-v4-flash`
- Tasks: five repairable Evaluation tasks
- Repeats: three per task, 15 runs per baseline
- Conditions: same task catalog, model, prompt, token budget, workspace isolation, and workflow
- Outputs: local-only `runtime/evaluations/retrieval-baselines/{baseline_a,baseline_b,baseline_c}/`

The raw trajectories and run records remain local and are not committed.

## Results

| Metric | Baseline A | Baseline B | Baseline C |
| --- | ---: | ---: | ---: |
| Runs | 15 | 15 | 15 |
| Evidence Validation | 100.0% | 100.0% | 93.3% |
| Correct File | 100.0% | 100.0% | 100.0% |
| Evidence Line Accuracy | 73.3% | 73.3% | 66.7% |
| Patch Apply | 46.7% | 86.7% | 46.7% |
| Final Test Pass | 46.7% | 86.7% | 46.7% |
| End-to-End Pass | 46.7% | 86.7% | 46.7% |
| Unauthorized Modification | 0.0% | 0.0% | 0.0% |
| False Pass | 0.0% | 0.0% | 0.0% |
| Average Tool Calls | 4.73 | 4.13 | 4.80 |
| Average Tokens | 3827.9 | 3529.8 | 3328.1 |
| Average Latency | 22442 ms | 23555 ms | 19705 ms |

Failure categories:

- Baseline A: `PATCH_APPLY_FAILURE` 4, `TOOL_CALL_FAILURE` 4
- Baseline B: `PATCH_APPLY_FAILURE` 1, `TOOL_CALL_FAILURE` 1
- Baseline C: `PATCH_APPLY_FAILURE` 2, `TOOL_CALL_FAILURE` 6

## Decision

Retain Baseline B as the current Retrieval implementation candidate. In this
controlled 15-run batch it improved Patch Apply, Final Test, and End-to-End
Pass from 46.7% to 86.7% while keeping Unauthorized Modification and False Pass
at 0%.

This is an experiment result, not a statistical production guarantee. Keep
Baseline A as a simple fallback and do not add embeddings, a vector database,
Neo4j, a complex reranker, or a Retrieval Agent based on this result alone.
