"""Save Notes cases split into dedicated file with TestRail case mapping."""

from __future__ import annotations

import pytest

from tests.notes._notes_common import case_params, load_cases, save_case_runner


SAVE_CASES = load_cases("save_notes.csv")
SAVE_PARAMS = case_params(SAVE_CASES, section="Notes", operation="POST /Notes/save")


class TestPostNotesSave:
    @pytest.mark.parametrize("case", SAVE_PARAMS)
    def test_post_notes_save_cases(self, case, api_context, db, request):
        save_case_runner(case, api_context, db, request)
