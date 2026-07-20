# Known Limitations

## Security & Isolation

1. **Temporary Workspace, not a security sandbox**
   The default execution backend copies the repository to a temporary directory
   and applies patches there. Tests run on the host OS — they can access
   the network, filesystem, environment variables, and other processes.
   This is appropriate for trusted demo repositories only.

2. **Docker is experimental**
   The optional Docker backend provides better isolation (no network,
   read-only root filesystem, dropped capabilities) but is not a hardened
   production sandbox. It requires a pre-built local image and manual setup.

3. **No protection against malicious repository content**
   CodeMedic reads and passes repository content to the configured LLM
   Provider. Do not run untrusted third-party code through the system
   without additional review.

## Model & Diagnosis

4. **Non-deterministic model output**
   LLM responses vary across runs. The same issue may produce different
   diagnoses or patches on repeated invocations. Evidence validation,
   diff validation, and fail-closed routing mitigate this but do not
   guarantee identical outcomes.

5. **Controlled Evaluation includes human decisions**
   The Evaluation Harness uses `controlled_auto` review policy, which
   automatically accepts diagnosis and patch decisions. Results are
   marked as `human_assisted`, not fully autonomous.

6. **Limited evaluation task set**
   The current task catalog has 7 tasks (5 repairable, 1 unrepairable,
   1 unauthorized-prompt). This is not a statistically significant
   benchmark for general code repair capability.

## Testing & Verification

7. **Verifier is advisory only**
   The Verifier agent summarizes test results in natural language.
   Final status is determined by `derive_final_status()` — a deterministic
   function that checks diff validity, patch application, test outcomes,
   and human decisions. The Verifier's output does not affect the final
   pass/fail decision.

8. **Real model calls are gated**
   Real LLM evaluation runs require a configured API key and are not
   part of CI. The 15-run proof-of-concept was executed manually.

## Scope

9. **Python only**
   CodeMedic currently supports Python repositories as diagnosis and
   repair targets. Other languages are not supported.

10. **git apply required**
    Patch application depends on `git apply`, which must be available
    in the execution environment.

11. **No advanced retrieval**
    Retrieval is limited to deterministic baselines (file tree + keyword,
    optional Python AST). No embedding, vector database, or ML-based
    retrieval is included.
