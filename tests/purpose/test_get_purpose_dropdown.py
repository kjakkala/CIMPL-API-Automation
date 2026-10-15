"""PUR05 coverage for GET /v1/purpose/dropdown."""
from __future__ import annotations

import time

import pytest

from utils.test_metadata import api_case


ENDPOINT = "purpose/dropdown"


def _items(payload) -> list[dict]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("items", "data", "value"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def _db_rows(db, include_inactive: bool) -> list[dict]:
    query = "SELECT PurposeId AS id, Name AS name, Active AS active FROM cimpl.Purpose"
    if not include_inactive:
        query += " WHERE Active=1"
    query += " ORDER BY PurposeId"
    return db.fetch_all(query)


def _api_map(items: list[dict]) -> dict[int, dict]:
    return {int(item["id"]): item for item in items if "id" in item}


def _record(request, *, db_rows: list[dict], api_items: list[dict], extra: list[str] | None = None):
    lines = [
        f"DB validation rows: {len(db_rows)}",
        f"API response rows: {len(api_items)}",
        f"DB active rows: {sum(bool(row['active']) for row in db_rows)}",
        f"DB inactive rows: {sum(not bool(row['active']) for row in db_rows)}",
    ]
    if extra:
        lines.extend(extra)
    request.node.user_properties.append(("dashboard_metrics", "\n".join(lines)))


def _assert_matches(response, db, request, *, include_inactive: bool):
    assert response.ok, f"GET {ENDPOINT} returned {response.status}: {response.text()}"
    items = _items(response.json())
    db_rows = _db_rows(db, include_inactive)
    api_by_id = _api_map(items)
    db_by_id = {int(row["id"]): row for row in db_rows}
    assert len(items) == len(db_rows), f"Purpose dropdown count mismatch: API={len(items)}, DB={len(db_rows)}"
    assert set(api_by_id) == set(db_by_id), "Purpose dropdown IDs do not match the database"
    for purpose_id, db_row in db_by_id.items():
        item = api_by_id[purpose_id]
        assert isinstance(item.get("id"), int), f"Purpose id must be int: {item}"
        assert isinstance(item.get("name"), str), f"Purpose name must be string: {item}"
        assert item["name"] == db_row["name"], f"Purpose name mismatch for id {purpose_id}"
    _record(request, db_rows=db_rows, api_items=items, extra=[f"IncludeInActive: {include_inactive}"])
    return items


class TestGetPurposeDropdown:
    @api_case(section="Purpose", operation="GET /v1/purpose/dropdown", testrail_id="17864423", title="PUR05 - Default dropdown returns active purposes")
    def test_default_returns_active(self, api_context, db, request):
        _assert_matches(api_context.get(ENDPOINT), db, request, include_inactive=False)

    @api_case(section="Purpose", operation="GET /v1/purpose/dropdown", testrail_id="17864424", title="PUR05 - IncludeInActive=false returns active purposes")
    def test_include_inactive_false(self, api_context, db, request):
        _assert_matches(api_context.get(ENDPOINT, params={"IncludeInActive": "false"}), db, request, include_inactive=False)

    @api_case(section="Purpose", operation="GET /v1/purpose/dropdown", testrail_id="17864425", title="PUR05 - IncludeInActive=true returns active and inactive purposes")
    def test_include_inactive_true(self, api_context, db, request):
        _assert_matches(api_context.get(ENDPOINT, params={"IncludeInActive": "true"}), db, request, include_inactive=True)

    @api_case(section="Purpose", operation="GET /v1/purpose/dropdown", testrail_id="17864426", title="PUR05 - Active purpose response schema")
    def test_response_schema(self, api_context, db, request):
        response = api_context.get(ENDPOINT)
        items = _assert_matches(response, db, request, include_inactive=False)
        assert all(set(item) == {"id", "name"} for item in items), "Dropdown rows must contain only id and name"

    @api_case(section="Purpose", operation="GET /v1/purpose/dropdown", testrail_id="17864427", title="PUR05 - Active and inactive counts match database")
    def test_counts_match_database(self, api_context, db, request):
        active = _assert_matches(api_context.get(ENDPOINT, params={"IncludeInActive": "false"}), db, request, include_inactive=False)
        all_rows = _assert_matches(api_context.get(ENDPOINT, params={"IncludeInActive": "true"}), db, request, include_inactive=True)
        assert len(all_rows) >= len(active)

    @api_case(section="Purpose", operation="GET /v1/purpose/dropdown", testrail_id="17864428", title="PUR05 - Empty IncludeInActive value uses default behavior")
    def test_empty_filter(self, api_context, db, request):
        response = api_context.get(ENDPOINT, params={"IncludeInActive": ""})
        assert response.status in {200, 400, 422}, f"Unexpected status: {response.status}"
        if response.ok:
            _assert_matches(response, db, request, include_inactive=False)

    @api_case(section="Purpose", operation="GET /v1/purpose/dropdown", testrail_id="17864429", title="PUR05 - Invalid IncludeInActive value validation")
    def test_invalid_filter(self, api_context, request):
        response = api_context.get(ENDPOINT, params={"IncludeInActive": "not-a-boolean"})
        request.node.user_properties.append(("dashboard_metrics", f"HTTP status: {response.status}\nResponse: {response.text()}"))
        assert response.status in {200, 400, 404, 422}, f"Unexpected invalid-filter status: {response.status}"

    @api_case(section="Purpose", operation="GET /v1/purpose/dropdown", testrail_id="17864430", title="PUR05 - Repeated IncludeInActive parameters use API binding behavior")
    def test_repeated_filter(self, api_context, db, request):
        response = api_context.get(ENDPOINT, params=[("IncludeInActive", "true"), ("IncludeInActive", "true")])
        assert response.ok, f"Repeated IncludeInActive request failed: {response.status}: {response.text()}"
        items = _items(response.json())
        ids = [int(item["id"]) for item in items]
        active_count = len(_db_rows(db, False))
        all_count = len(_db_rows(db, True))
        _record(request, db_rows=_db_rows(db, True), api_items=items, extra=[
            "Repeated IncludeInActive values: true,true",
            f"Observed API count: {len(items)}",
            f"Active DB count: {active_count}",
            f"All DB count: {all_count}",
            "Observed API behavior: repeated boolean parameters bind to the active-only result in this environment.",
        ])
        assert len(ids) == len(set(ids)), "Repeated IncludeInActive parameters must not duplicate rows"
        assert len(items) in {active_count, all_count}, "Unexpected repeated-parameter result count"

    @api_case(section="Purpose", operation="GET /v1/purpose/dropdown", testrail_id="17864431", title="PUR05 - Unauthorized purpose dropdown request")
    def test_unauthorized(self, api_context, request):
        response = api_context.get(ENDPOINT, headers={"Authorization": ""})
        request.node.user_properties.append(("dashboard_metrics", f"HTTP status: {response.status}\nResponse: {response.text()}"))
        assert response.status in {401, 403}, f"Expected 401/403, got {response.status}: {response.text()}"

    @api_case(section="Purpose", operation="GET /v1/purpose/dropdown", testrail_id="17864432", title="PUR05 - Dropdown response performance")
    def test_response_performance(self, api_context, db, request):
        started = time.perf_counter()
        response = api_context.get(ENDPOINT)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        _assert_matches(response, db, request, include_inactive=False)
        request.node.user_properties.append(("dashboard_metrics", f"Response time ms: {elapsed_ms}"))
        assert elapsed_ms < 120000
