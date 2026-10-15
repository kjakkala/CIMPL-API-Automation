"""Data-driven tests for WOR02: GET /workorders/{number} with DB-backed validation."""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from urllib.parse import quote

import pytest
import requests


UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class Wor02Case:
    case_id: str
    title: str


CASES: list[Wor02Case] = [
    Wor02Case("17776486", "Retrieve workorder with valid number"),
    Wor02Case("17776488", "Verify providerSummary array exists"),
    Wor02Case("17776489", "Verify single provider record fields"),
    Wor02Case("17776493", "Verify multiple provider summaries"),
    Wor02Case("17776494", "Verify status mapping"),
    Wor02Case("17776495", "Verify completed workorder"),
    Wor02Case("17776496", "Verify date format"),
    Wor02Case("17776497", "Verify serviceIds values"),
    Wor02Case("17776498", "Verify response time"),
    Wor02Case("17776476", "Workorder number does not exist"),
    Wor02Case("17776477", "Empty workorder number"),
    Wor02Case("17776478", "Null workorder number"),
    Wor02Case("17776479", "Alphabetic workorder number"),
    Wor02Case("17776480", "Special characters in number"),
    Wor02Case("17776481", "Very long workorder number"),
    Wor02Case("17776482", "SQL Injection attempt"),
    Wor02Case("17776483", "XSS payload"),
    Wor02Case("17776484", "Invalid UUID in response"),
    Wor02Case("17776485", "Missing required response field"),
    Wor02Case("17776471", "Request without authentication token"),
    Wor02Case("17776472", "Request with expired token"),
    Wor02Case("17776473", "Request with invalid token"),
    Wor02Case("17776474", "Access unauthorized workorder"),
    Wor02Case("17776475", "Verify sensitive data exposure"),
]


def _params() -> list:
    return [
        pytest.param(
            case,
            id=f"C{case.case_id}",
            marks=[
                pytest.mark.swagger_section("Workorders"),
                pytest.mark.swagger_operation("GET /workorders/{number}"),
                pytest.mark.testrail_case(case.case_id),
                pytest.mark.dashboard_title(case.title),
            ],
        )
        for case in CASES
    ]


def _record_dashboard_metrics(request, lines: list[str]):
    request.node.user_properties.append(("dashboard_metrics", "\n".join(lines)))


def _normalize_text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_date(value):
    if value is None:
        return None
    text = str(value).replace(" ", "T")
    if len(text) >= 19:
        return text[:19]
    if len(text) >= 10:
        return text[:10]
    return text


def _safe_json(response):
    try:
        payload = response.json()
        return payload if isinstance(payload, dict) else {"raw": payload}
    except Exception:  # noqa: BLE001
        return {}


def _response_text(response) -> str:
    text_attr = getattr(response, "text", "")
    if callable(text_attr):
        return text_attr()
    return str(text_attr)


def _workorder_by_number_get(api_context, number: str):
    safe_number = quote(str(number), safe="")
    return api_context.get(f"workorders/{safe_number}", headers={"accept": "text/plain"})


def _requests_get(path: str, *, bearer: str | None = None):
    base = os.environ["BASE_URL"].rstrip("/")
    url = f"{base}/{path.lstrip('/')}"
    headers = {"accept": "text/plain"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    return requests.get(url, headers=headers, timeout=120)


def _auth_token() -> str:
    response = requests.post(
        os.environ["TOKEN_URL"],
        data={
            "grant_type": os.getenv("GRANT_TYPE", "password"),
            "client_id": os.environ["CLIENT_ID"],
            "client_secret": os.environ["CLIENT_SECRET"],
            "username": os.environ["API_USERNAME"],
            "password": os.environ["API_PASSWORD"],
        },
        timeout=60,
    )
    payload = response.json()
    token = payload.get("access_token", "")
    assert token, f"Unable to obtain token. Response={payload}"
    return token


def _db_sample_numbers(db) -> dict:
    base_query = """
        SELECT TOP 1 w.Number AS workorderNumber
        FROM cimpl.Workorder w
        INNER JOIN cimpl.WorkorderSummary ws ON ws.WorkorderId = w.WorkorderId
        ORDER BY ws.ModifiedDate DESC
    """

    valid_row = db.fetch_one(base_query)
    assert valid_row and valid_row.get("workorderNumber"), "Unable to find a valid workorder number"

    multi_row = db.fetch_one(
        """
        SELECT TOP 1 w.Number AS workorderNumber
        FROM cimpl.Workorder w
        INNER JOIN cimpl.WorkorderSummary ws ON ws.WorkorderId = w.WorkorderId
        GROUP BY w.Number
        HAVING COUNT(*) > 1
        ORDER BY MAX(ws.ModifiedDate) DESC
        """
    )

    completed_row = db.fetch_one(
        """
        SELECT TOP 1 w.Number AS workorderNumber
        FROM cimpl.Workorder w
        INNER JOIN cimpl.WorkorderSummary ws ON ws.WorkorderId = w.WorkorderId
        INNER JOIN cimpl.WorkorderStatus wss ON wss.WorkorderStatusId = ws.WorkorderStatusId
        WHERE wss.Name = 'Completed' AND ws.CompletedDate IS NOT NULL
        ORDER BY ws.ModifiedDate DESC
        """
    )

    single_row = db.fetch_one(
        """
        SELECT TOP 1 w.Number AS workorderNumber
        FROM cimpl.Workorder w
        INNER JOIN cimpl.WorkorderSummary ws ON ws.WorkorderId = w.WorkorderId
        GROUP BY w.Number
        HAVING COUNT(*) = 1
        ORDER BY MAX(ws.ModifiedDate) DESC
        """
    )

    return {
        "valid": _normalize_text(valid_row["workorderNumber"]),
        "single": _normalize_text((single_row or valid_row)["workorderNumber"]),
        "multi": _normalize_text((multi_row or valid_row)["workorderNumber"]),
        "completed": _normalize_text((completed_row or valid_row)["workorderNumber"]),
        "nonexistent": "__NO_SUCH_WORKORDER_999999__",
    }


def _expected_workorder_rows(db, workorder_number: str) -> list[dict]:
    return db.fetch_all(
        """
        SELECT
            w.Number AS workOrderNumber,
            ws.ProviderId AS providerId,
            wss.Name AS statusName,
            ws.WorkorderStatusId AS statusId,
            tsi.PrimaryNumber AS primaryNumber,
            ws.CompletedDate AS completedDate,
            wr.EmployeeId AS requesterId,
            ws.DueDate AS dueDate,
            ws.ModifiedDate AS modifiedDate,
            wr.Email AS requesterEmail,
            wr.FirstName AS requesterFirstName,
            wr.LastName AS requesterLastName
        FROM cimpl.Workorder w
        INNER JOIN cimpl.WorkorderSummary ws ON w.WorkorderId = ws.WorkorderId
        INNER JOIN cimpl.WorkorderStatus wss ON ws.WorkorderStatusId = wss.WorkorderStatusId
        INNER JOIN cimpl.WorkorderInventory wi ON wi.WorkorderSummaryId = ws.WorkorderSummaryId
        INNER JOIN cimpl.TransactServiceInventory tsi
            ON tsi.ServiceInventoryId = wi.ServiceInventoryId
           AND tsi.TransactId = wi.TransactId
        LEFT JOIN cimpl.WorkorderRequester wr ON wr.WorkorderRequesterId = ws.WorkorderRequesterId
        WHERE w.Number = ?
        """,
        (workorder_number,),
    )


def _group_expected_provider_summary(rows: list[dict]) -> dict[tuple, set[str]]:
    grouped: dict[tuple, set[str]] = {}
    for row in rows:
        key = (
            int(row["providerId"]) if row.get("providerId") is not None else None,
            int(row["statusId"]) if row.get("statusId") is not None else None,
            _normalize_text(row.get("statusName")),
            _normalize_date(row.get("completedDate")),
        )
        grouped.setdefault(key, set())
        if row.get("primaryNumber") is not None:
            grouped[key].add(_normalize_text(row["primaryNumber"]))
    return grouped


def _group_api_provider_summary(provider_summary: list[dict]) -> dict[tuple, set[str]]:
    grouped: dict[tuple, set[str]] = {}
    for summary in provider_summary:
        key = (
            int(summary["providerId"]) if summary.get("providerId") is not None else None,
            int(summary["statusId"]) if summary.get("statusId") is not None else None,
            _normalize_text(summary.get("statusName")),
            _normalize_date(summary.get("dateCompleted")),
        )
        grouped.setdefault(key, set())
        for service_id in summary.get("serviceIds", []) or []:
            grouped[key].add(_normalize_text(service_id))
    return grouped


def _assert_common_shape(payload: dict, *, title: str):
    assert isinstance(payload, dict), f"{title}: response body must be an object"
    assert isinstance(payload.get("providerSummary"), list), f"{title}: providerSummary must be an array"
    assert "requestId" in payload, f"{title}: requestId is required"
    request_id = _normalize_text(payload.get("requestId"))
    assert UUID_RE.match(request_id), f"{title}: requestId is not a valid UUID -> {request_id}"


def _assert_provider_record_fields(summary: dict, *, title: str):
    assert isinstance(summary.get("statusId"), int), f"{title}: statusId must be integer"
    assert isinstance(summary.get("statusName"), str), f"{title}: statusName must be string"
    assert isinstance(summary.get("serviceIds"), list), f"{title}: serviceIds must be array"
    assert isinstance(summary.get("providerId"), int), f"{title}: providerId must be integer"

    if summary.get("dateCompleted") is not None:
        assert "T" in str(summary.get("dateCompleted")), f"{title}: dateCompleted should be ISO-like"
    if summary.get("dateModified") is not None:
        assert "T" in str(summary.get("dateModified")), f"{title}: dateModified should be ISO-like"

    requestor = summary.get("requestor")
    if requestor is not None:
        assert isinstance(requestor, dict), f"{title}: requestor must be object when provided"
        if requestor.get("id") is not None:
            assert isinstance(requestor.get("id"), int), f"{title}: requestor.id must be integer"
        if requestor.get("number") is not None:
            assert isinstance(requestor.get("number"), str), f"{title}: requestor.number must be string"
        if requestor.get("email") is not None:
            assert EMAIL_RE.match(_normalize_text(requestor.get("email"))), (
                f"{title}: requestor.email invalid -> {requestor.get('email')}"
            )


def _run_case(case: Wor02Case, api_context, db, request):
    cid = case.case_id
    title = case.title
    sample = _db_sample_numbers(db)

    metrics = [
        f"CaseId: {cid}",
        f"Title: {title}",
        f"DB target: {os.getenv('DB_SERVER', '')} / {os.getenv('DB_NAME', '')}",
        f"Sample dataset: {json.dumps(sample, ensure_ascii=True)}",
    ]

    # Security/auth cases via raw requests.
    if cid == "17776471":
        response = _requests_get(f"workorders/{quote(sample['valid'], safe='')}")
        assert response.status_code == 401, f"{title}: expected 401, got {response.status_code}"
        metrics += [f"Response status: {response.status_code}", "Validation: request without auth is rejected."]
        _record_dashboard_metrics(request, metrics)
        return

    if cid in {"17776472", "17776473"}:
        response = _requests_get(f"workorders/{quote(sample['valid'], safe='')}", bearer="invalid-or-expired-token")
        assert response.status_code == 401, f"{title}: expected 401, got {response.status_code}"
        metrics += [f"Response status: {response.status_code}", "Validation: invalid/expired token rejected."]
        _record_dashboard_metrics(request, metrics)
        return

    if cid == "17776474":
        no_perm_token = os.getenv("API_NO_PERMISSION_TOKEN", "").strip()
        if not no_perm_token:
            pytest.skip("API_NO_PERMISSION_TOKEN is not configured for 403 validation")
        response = _requests_get(f"workorders/{quote(sample['valid'], safe='')}", bearer=no_perm_token)
        assert response.status_code in {401, 403}, f"{title}: expected 401/403, got {response.status_code}"
        metrics += [f"Response status: {response.status_code}", "Validation: unauthorized access is rejected."]
        _record_dashboard_metrics(request, metrics)
        return

    if cid == "17776475":
        response = _workorder_by_number_get(api_context, sample["valid"])
        assert response.status == 200, f"{title}: expected 200, got {response.status}. Response={response.text()}"
        text = response.text().lower()
        for sensitive in ["password", "client_secret", "token="]:
            assert sensitive not in text, f"{title}: sensitive text '{sensitive}' exposed in response"
        metrics += [f"Response status: {response.status}", "Validation: sensitive text not exposed."]
        _record_dashboard_metrics(request, metrics)
        return

    # Map case IDs to target numbers.
    if cid in {"17776486", "17776488", "17776494", "17776497", "17776498", "17776484", "17776485"}:
        target_number = sample["valid"]
    elif cid == "17776489":
        target_number = sample["single"]
    elif cid == "17776493":
        target_number = sample["multi"]
    elif cid == "17776495":
        target_number = sample["completed"]
    elif cid == "17776496":
        target_number = sample["valid"]
    elif cid == "17776476":
        target_number = sample["nonexistent"]
    elif cid == "17776477":
        target_number = ""
    elif cid == "17776478":
        target_number = "null"
    elif cid == "17776479":
        target_number = "ABC"
    elif cid == "17776480":
        target_number = "@@@"
    elif cid == "17776481":
        target_number = "X" * 5000
    elif cid == "17776482":
        target_number = "' OR 1=1 --"
    elif cid == "17776483":
        target_number = "<script>alert(1)</script>"
    else:
        target_number = sample["valid"]

    # Execute request.
    if cid == "17776477":
        # Empty path falls back to collection endpoint in this API gateway setup.
        response = api_context.get("workorders/", headers={"accept": "text/plain"})
        payload = _safe_json(response)
    else:
        response = _workorder_by_number_get(api_context, target_number)
        payload = _safe_json(response)

    metrics += [
        f"Target number: {target_number}",
        f"Response status: {getattr(response, 'status', getattr(response, 'status_code', 'n/a'))}",
        f"Response preview: {_response_text(response)[:400]}",
    ]

    # Negative status expectations.
    if cid == "17776476":
        assert response.status == 404, f"{title}: expected 404, got {response.status}. Response={response.text()}"
        _record_dashboard_metrics(request, metrics)
        return

    if cid in {"17776479", "17776480", "17776481", "17776482", "17776483"}:
        assert response.status in {400, 404}, (
            f"{title}: expected 400/404, got {response.status}. Response={response.text()}"
        )
        _record_dashboard_metrics(request, metrics)
        return

    if cid == "17776477":
        assert response.status in {200, 404, 405}, (
            f"{title}: expected 200/404/405, got {response.status}. Response={_response_text(response)}"
        )
        _record_dashboard_metrics(request, metrics)
        return

    if cid == "17776478":
        assert response.status in {400, 404}, f"{title}: expected 400/404, got {response.status}. Response={response.text()}"
        _record_dashboard_metrics(request, metrics)
        return

    # Positive and schema cases.
    assert response.status == 200, f"{title}: expected 200, got {response.status}. Response={response.text()}"
    _assert_common_shape(payload, title=title)

    provider_summary = payload.get("providerSummary", [])

    if cid == "17776488":
        assert isinstance(provider_summary, list), f"{title}: providerSummary must exist and be array"

    if cid == "17776489":
        assert len(provider_summary) == 1, f"{title}: expected exactly one provider summary row"
        _assert_provider_record_fields(provider_summary[0], title=title)

    if cid == "17776493":
        assert len(provider_summary) >= 2, f"{title}: expected multiple provider summaries"

    if cid == "17776494":
        db_rows = _expected_workorder_rows(db, target_number)
        api_grouped = _group_api_provider_summary(provider_summary)
        db_grouped = _group_expected_provider_summary(db_rows)
        assert set(api_grouped.keys()) == set(db_grouped.keys()), (
            f"{title}: status/provider mapping mismatch. "
            f"API-only={list(set(api_grouped.keys()) - set(db_grouped.keys()))[:10]}, "
            f"DB-only={list(set(db_grouped.keys()) - set(api_grouped.keys()))[:10]}"
        )

    if cid == "17776495":
        assert any(_normalize_date(x.get("dateCompleted")) for x in provider_summary), (
            f"{title}: expected at least one completed provider summary"
        )

    if cid == "17776496":
        for summary in provider_summary:
            if summary.get("dateCompleted") is not None:
                assert "T" in str(summary.get("dateCompleted")), f"{title}: dateCompleted not ISO-like"
            if summary.get("dateModified") is not None:
                assert "T" in str(summary.get("dateModified")), f"{title}: dateModified not ISO-like"

    if cid == "17776497":
        db_rows = _expected_workorder_rows(db, target_number)
        expected_ids = {
            _normalize_text(row["primaryNumber"])
            for row in db_rows
            if row.get("primaryNumber") is not None
        }
        actual_ids = {
            _normalize_text(sid)
            for summary in provider_summary
            for sid in (summary.get("serviceIds") or [])
        }
        assert expected_ids == actual_ids, f"{title}: serviceIds mismatch API={sorted(actual_ids)} DB={sorted(expected_ids)}"

    if cid == "17776498":
        start = time.perf_counter()
        response2 = _workorder_by_number_get(api_context, target_number)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert response2.status == 200, f"{title}: timed call failed with {response2.status}"
        assert elapsed_ms < 15000, f"{title}: response time too high ({elapsed_ms:.2f} ms)"
        metrics.append(f"Elapsed ms: {elapsed_ms:.2f}")

    if cid == "17776484":
        request_id = _normalize_text(payload.get("requestId"))
        assert UUID_RE.match(request_id), f"{title}: invalid UUID -> {request_id}"

    if cid == "17776485":
        assert "providerSummary" in payload, f"{title}: providerSummary missing"
        assert "requestId" in payload, f"{title}: requestId missing"

    for summary in provider_summary:
        _assert_provider_record_fields(summary, title=title)

    _record_dashboard_metrics(request, metrics)


class TestGetWorkorderByNumber:

    @pytest.mark.parametrize("case", _params())
    def test_get_workorder_by_number_cases(self, case, api_context, db, request):
        _run_case(case, api_context, db, request)

    @pytest.mark.swagger_section("Workorders")
    @pytest.mark.swagger_operation("GET /workorders/{number}")
    @pytest.mark.dashboard_title("RequestId uniqueness across repeated calls")
    def test_request_id_uniqueness_across_repeated_calls(self, api_context, db, request):
        sample = _db_sample_numbers(db)
        target_number = sample["valid"]

        request_ids: list[str] = []
        statuses: list[int] = []
        for _ in range(10):
            response = _workorder_by_number_get(api_context, target_number)
            statuses.append(int(response.status))
            assert response.status == 200, (
                "RequestId uniqueness across repeated calls: expected 200, "
                f"got {response.status}. Response={response.text()}"
            )
            payload = _safe_json(response)
            request_id = _normalize_text(payload.get("requestId"))
            assert request_id, "RequestId uniqueness across repeated calls: requestId is missing"
            assert UUID_RE.match(request_id), (
                "RequestId uniqueness across repeated calls: requestId is not a valid UUID -> "
                f"{request_id}"
            )
            request_ids.append(request_id)

        unique_ids = set(request_ids)
        assert len(unique_ids) == len(request_ids), (
            "RequestId uniqueness across repeated calls: duplicate requestId values found. "
            f"all={request_ids}"
        )

        _record_dashboard_metrics(
            request,
            [
                f"Target number: {target_number}",
                f"Call count: {len(request_ids)}",
                f"Statuses: {statuses}",
                f"Unique requestId count: {len(unique_ids)}",
                f"RequestIds: {json.dumps(request_ids, ensure_ascii=True)}",
            ],
        )
