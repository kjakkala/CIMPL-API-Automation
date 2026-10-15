"""Delete Notes cases split into dedicated file with TestRail case mapping."""

from __future__ import annotations

import pytest

from tests.notes._notes_common import case_params, delete_case_runner, load_cases


DELETE_CASES = load_cases("delete_notes.csv")
DELETE_PARAMS = case_params(DELETE_CASES, section="Notes", operation="DELETE /Notes")


class TestDeleteNotes:
    @pytest.mark.parametrize("case", DELETE_PARAMS)
    def test_delete_notes_cases(self, case, api_context, db, request):
        delete_case_runner(case, api_context, db, request)
