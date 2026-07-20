# V1 Smoke Test Report

> Sanitized record of the accepted real-model smoke test.
> Run on 2026-07-17 by a human via Streamlit Inspector / Evaluation CLI.

## Run metadata

- **Branch**: `test/real-model-proof`
- **Commit**: `3c08a00`
- **Provider**: OpenAI-compatible endpoint
- **Model**: `deepseek-v4-flash`
- **Task**: factorial NameError in `demo_repos/sample_project`

## Diagnosis

```
Root cause: variable name typo — 'resut' should be 'result'
File:       src/utils/math_helpers.py
Confidence: 0.95
```

Evidence validation passed. The model correctly identified the
single-character typo on line 14.

## First Patch (problematic)

```diff
--- a/src/math_helpers.py
+++ b/src/math_helpers.py
@@ -4,7 +4,7 @@
     result = 1
     for i in range(1, n + 1):
         result *= i
-    return resut  # deliberate NameError
+    return result  # deliberate NameError
```

**Issues detected:**
1. Trailing Markdown fence (`` ``` ``) present
2. Hunk header line count incorrect (4,7 instead of actual range)
3. `diff_validation.valid` was `true` despite these issues — **FIXED IN V1**

## Human Retry

The human reviewer identified the formatting issues and requested a retry
with the following feedback:

> "Patch has markdown fence and wrong hunk header"

## Second Patch (accepted)

```diff
--- a/src/math_helpers.py
+++ b/src/math_helpers.py
@@ -13,7 +13,7 @@
     result = 1
     for i in range(1, n + 1):
         result *= i
-    return resut  # deliberate NameError
+    return result
```

- Static diff validation: passed
- `git apply --check`: passed
- Patch Apply: success
- pytest: passed
- Sandbox cleanup: completed

## Validation

- Original repository unmodified: ✅ verified (git status clean)
- Temporary workspace cleaned: ✅
- Final status: `通过`
- False pass: 0
- Unauthorized modification: 0

## Lessons learned

1. The Fixer model sometimes wraps diffs in Markdown fences —
   this is now stripped by the parser and rejected if residual fences remain.
2. Hunk headers with incorrect line counts are now validated by
   `_check_hunk_headers()` in `diff.py`.
3. `git apply --check` now runs in a temporary sandbox _before_ Patch Review,
   not just before the final Apply.
