"""WOR03 coverage for POST /v1/workorders/attachable."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass

import pytest
import requests

ENDPOINT = "workorders/attachable"


@dataclass(frozen=True)
class AttachableCase:
    case_id: str
    title: str
    label: str


CASES = [
    AttachableCase("17864667", "WOR03 - Default attachable workorders", "Functional"),
    AttachableCase("17864668", "WOR03 - Verify response body fields", "ResponseSchema"),
    AttachableCase("17864669", "WOR03 - CreatedBy not provided", "Functional"),
    AttachableCase("17864670", "WOR03 - CreatedBy partial text", "Compatibility"),
    AttachableCase("17864671", "WOR03 - CreatedBy exact text", "Compatibility"),
    AttachableCase("17864672", "WOR03 - WorkorderNumber not provided", "Functional"),
    AttachableCase("17864673", "WOR03 - WorkorderNumber partial text", "Compatibility"),
    AttachableCase("17864674", "WOR03 - WorkorderNumber exact text", "Compatibility"),
    AttachableCase("17864675", "WOR03 - Both filters not provided", "Functional"),
    AttachableCase("17864676", "WOR03 - Both filters partial text", "Compatibility"),
    AttachableCase("17864677", "WOR03 - Both filters exact text", "Compatibility"),
    AttachableCase("17864678", "WOR03 - Invalid filter returns empty result", "Validation"),
    AttachableCase("17864679", "WOR03 - Unauthorized request", "Security"),
    AttachableCase("17864680", "WOR03 - Response performance", "Non-Functional"),
]

CASE_PARAMS = [
    pytest.param(
        case,
        id=f"C{case.case_id}",
        marks=[
            pytest.mark.swagger_section("Workorders"),
            pytest.mark.swagger_operation("POST /v1/workorders/attachable"),
            pytest.mark.testrail_case(case.case_id),
            pytest.mark.dashboard_title(case.title),
        ],
    )
    for case in CASES
]


def _payload(response):
    value = response.json()
    return value if isinstance(value, list) else value.get("items", value.get("data", []))


def _db_candidates(db):
    return db.fetch_all(
        """
        SELECT w.WorkorderId AS workorderId, w.Number AS workorderNumber,
               COALESCE(NULLIF(LTRIM(RTRIM(u.FirstName + ' ' + u.LastName)), ''), u.UserName) AS createdBy,
               u.UserName AS createdByUserName
        FROM cimpl.Workorder w
        INNER JOIN usr.Users u ON u.UserId=w.UserId
        WHERE w.SourceId=1
          AND NOT EXISTS (
              SELECT 1 FROM cimpl.WorkorderSummary ws
              WHERE ws.WorkorderId=w.WorkorderId AND ws.WorkorderStatusId<>1
          )
        ORDER BY w.WorkorderId DESC
        """
    )


def _record(request, items, db_rows, extra=None):
    lines = [f"API rows: {len(items)}", f"DB attachable rows: {len(db_rows)}"]
    if extra:
        lines.extend(extra)
    request.node.user_properties.append(("dashboard_metrics", "\n".join(lines)))


def _assert_rows(response, db, request, *, filters=None):
    assert response.ok, f"WOR03 returned {response.status}: {response.text}"
    items = _payload(response)
    db_rows = _db_candidates(db)
    filters = filters or {}
    expected = db_rows
    if filters.get("workorderNumber"):
        expected = [row for row in expected if filters["workorderNumber"].lower() in str(row["workorderNumber"]).lower()]
    if filters.get("createdBy"):
        value = filters["createdBy"].lower()
        expected = [row for row in expected if value in str(row["createdBy"]).lower() or value in str(row["createdByUserName"]).lower()]
    db_by_id = {int(row["workorderId"]): row for row in db_rows}
    for item in items:
        assert isinstance(item.get("workorderId"), int), f"Invalid workorderId: {item}"
        assert isinstance(item.get("workorderNumber"), str), f"Invalid workorderNumber: {item}"
        assert isinstance(item.get("createdBy"), str), f"Invalid createdBy: {item}"
        workorder_id = int(item["workorderId"])
        assert workorder_id in db_by_id, f"API returned workorder not eligible in DB: {workorder_id}"
        row = db_by_id[workorder_id]
        assert str(item["workorderNumber"]) == str(row["workorderNumber"])
        assert str(item["createdBy"]) == str(row["createdBy"])
        if filters.get("workorderNumber"):
            assert filters["workorderNumber"].lower() in str(item["workorderNumber"]).lower()
        if filters.get("createdBy"):
            assert filters["createdBy"].lower() in str(item["createdBy"]).lower()
    _record(request, items, db_rows, [f"Filters: {filters or 'none'}"])
    return items


class TestPostWorkordersAttachable:
    @pytest.mark.parametrize("case", CASE_PARAMS)
    def test_post_workorders_attachable(self, case, api_context, db, request):
        candidates = _db_candidates(db)
        assert candidates, "No MACD workorders with all Pending summaries are available"
        sample = candidates[0]
        body = {}
        filters = {}
        if case.case_id in {"17864667", "17864669", "17864672", "17864675"}:
            body = {}
        elif case.case_id == "17864670":
            filters = {"createdBy": str(sample["createdBy"]).split()[-1]}
            body = filters.copy()
        elif case.case_id == "17864671":
            filters = {"createdBy": str(sample["createdBy"])}
            body = filters.copy()
        elif case.case_id == "17864673":
            filters = {"workorderNumber": str(sample["workorderNumber"])[-2:]}
            body = filters.copy()
        elif case.case_id == "17864674":
            filters = {"workorderNumber": str(sample["workorderNumber"])}
            body = filters.copy()
        elif case.case_id == "17864676":
            filters = {"workorderNumber": str(sample["workorderNumber"])[-2:], "createdBy": str(sample["createdBy"]).split()[-1]}
            body = filters.copy()
        elif case.case_id == "17864677":
            filters = {"workorderNumber": str(sample["workorderNumber"]), "createdBy": str(sample["createdBy"])}
            body = filters.copy()
        elif case.case_id == "17864678":
            filters = {"workorderNumber": "NO_SUCH_WOR03_999", "createdBy": "NO_SUCH_CREATOR_999"}
            body = filters.copy()
        elif case.case_id == "17864679":
            response = requests.post(
                f"{os.environ['BASE_URL'].rstrip('/')}/{ENDPOINT}",
                json={},
                headers={"accept": "application/json"},
                timeout=120,
            )
            assert response.status_code in {401, 403}, f"Expected 401/403, got {response.status_code}: {response.text}"
            request.node.user_properties.append(("dashboard_metrics", f"HTTP status: {response.status_code}\nUnauthorized request rejected."))
            return
        elif case.case_id == "17864680":
            started = time.perf_counter()
            response = api_context.post(ENDPOINT, data="{}", headers={"Content-Type": "application/json", "accept": "application/json"})
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            _assert_rows(response, db, request)
            assert elapsed_ms < 15000, f"Response took too long: {elapsed_ms} ms"
            request.node.user_properties.append(("dashboard_metrics", f"Response time ms: {elapsed_ms}"))
            return
        response = api_context.post(ENDPOINT, data=json.dumps(body), headers={"Content-Type": "application/json", "accept": "application/json"})
        _assert_rows(response, db, request, filters=filters)
