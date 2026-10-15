"""Fetch Notes cases split into dedicated file with TestRail case mapping."""

from __future__ import annotations

import pytest

from tests.notes._notes_common import case_params, fetch_case_runner, load_cases


FETCH_CASES = load_cases("fetch_notes.csv")
FETCH_PARAMS = case_params(FETCH_CASES, section="Notes", operation="GET /Notes")


class TestGetNotes:
    @pytest.mark.parametrize("case", FETCH_PARAMS)
    def test_get_notes_cases(self, case, api_context, db, request):
        fetch_case_runner(case, api_context, db, request)
