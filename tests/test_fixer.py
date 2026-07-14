"""Fixer response parser regression tests."""

from __future__ import annotations

from codemedic.agents.fixer import _parse_fixer_response


def test_parser_keeps_summary_fields_in_their_own_sections() -> None:
    response = """--- a/src/example.py
+++ b/src/example.py
@@ -1,1 +1,1 @@
-old
+new
MODIFIED_FILES: src/example.py
RATIONALE: Rename the broken variable.
RISKS: None expected | Review the caller.
TEST_SUGGESTIONS: pytest -q | python -m ruff check .
"""

    proposal = _parse_fixer_response(response)

    assert proposal.modified_files == ["src/example.py"]
    assert proposal.rationale == "Rename the broken variable."
    assert proposal.risks == ["None expected", "Review the caller."]
    assert proposal.test_suggestions == [
        "pytest -q",
        "python -m ruff check .",
    ]
