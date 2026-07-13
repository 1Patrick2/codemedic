# Sample Project — CodeMedic Demo Repository

A deliberately buggy Python project used to validate the CodeMedic Investigator Agent.

## Issues to investigate

1. `src/utils/math_helpers.py` — typo in function name causes `NameError`
2. `src/services/data_service.py` — wrong import path, `Config` class has type mismatch
3. `tests/test_utils.py` — test expects correct behavior but the implementation is broken

## Running

```bash
cd demo_repos/sample_project
python -m pytest tests/ -v
```
