"""Data-driven tests for GET /workorders with DB-backed validation and TestRail mapping."""

from __future__ import annotations

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import pytest
import requests


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class WorkorderCase:
    case_id: str
    title: str


CASES: list[WorkorderCase] = [
    WorkorderCase("17775846", "Get all workorders with default pagination"),
    WorkorderCase("17775847", "Get workorder by WorkorderNumber"),
    WorkorderCase("17775848", "Get Workorder by RequesterId"),
    WorkorderCase("17775849", "Get Workorders by Status Filter"),
    WorkorderCase("17775851", "Get workorders by Source filter"),
    WorkorderCase("17775852", "Get work order by Pagination first page"),
    WorkorderCase("17775853", "Get Work order by Pagination middle page"),
    WorkorderCase("17775854", "Get work order by Pagination last page"),
    WorkorderCase("17775855", "Workorder not found"),
    WorkorderCase("17775856", "RequesterId not found"),
    WorkorderCase("17775857", "Status not found"),
    WorkorderCase("17775922", "Verify workorders can be filtered using Source only"),
    WorkorderCase("17775923", "Verify workorders can be filtered using Status and RequesterId"),
    WorkorderCase("17775924", "Verify workorders can be filtered using Status and WorkorderNumber"),
    WorkorderCase("17775925", "Verify workorders can be filtered using Status and Source"),
    WorkorderCase("17775926", "Verify workorders can be filtered using RequesterId and WorkorderNumber"),
    WorkorderCase("17775927", "Verify workorders can be filtered using RequesterId and Source"),
    WorkorderCase("17775928", "Verify workorders can be filtered using WorkorderNumber and Source"),
    WorkorderCase("17775929", "Verify workorders can be filtered using Status, RequesterId, and WorkorderNumber"),
    WorkorderCase("17775930", "Verify workorders can be filtered using Status, RequesterId, and Source"),
    WorkorderCase("17775931", "Verify workorders can be filtered using Status, WorkorderNumber, and Source"),
    WorkorderCase("17775932", "Verify workorders can be filtered using RequesterId, WorkorderNumber, and Source"),
    WorkorderCase("17775933", "Verify workorders can be filtered using Status, RequesterId, WorkorderNumber, and Source"),
    WorkorderCase("17775858", "Response Validation Test Case - Verify response contains pagination object"),
    WorkorderCase("17775859", "Response validation test case - Verify response contains items array"),
    WorkorderCase("17775860", "Response Validation Test Cases - Verify pagination.index is integer"),
    WorkorderCase("17775861", "Response Validation Test Cases - Verify pagination.size is integer"),
    WorkorderCase("17775862", "Response Validation Test Cases - Verify pagination.totalRecords is integer"),
    WorkorderCase("17775863", "Response Validation Test Cases - Verify number field exists and is string"),
    WorkorderCase("17775864", "Response Validation Test Cases - Verify providerSummary array exists"),
    WorkorderCase("17775865", "Verify statusId is numeric"),
    WorkorderCase("17775866", "Verify statusName is string"),
    WorkorderCase("17775867", "Verify serviceIds is array"),
    WorkorderCase("17775868", "Verify providerId is integer"),
    WorkorderCase("17775869", "Verify dateCompleted follows ISO-8601 format"),
    WorkorderCase("17775870", "Verify dateModified follows ISO-8601 format"),
    WorkorderCase("17775871", "Verify requestor object exists"),
    WorkorderCase("17775872", "Verify requestor.id is integer"),
    WorkorderCase("17775873", "Verify requestor.number is string"),
    WorkorderCase("17775874", "Verify requestor.email contains valid email format"),
    WorkorderCase("17775875", "Verify workorderOwner object exists"),
    WorkorderCase("17775876", "Verify workorderOwner.userName is populated"),
    WorkorderCase("17775877", "Verify workorderOwner.email contains valid email"),
    WorkorderCase("17775878", "Verify workorderOwner.email contains valid email"),
    WorkorderCase("17775879", "PageSize = 1"),
    WorkorderCase("17775880", "PageSize = 10"),
    WorkorderCase("17775881", "PageSize = 100"),
    WorkorderCase("17775882", "PageSize exceeds API limit"),
    WorkorderCase("17775883", "PageIndex beyond available pages"),
    WorkorderCase("17775884", "Negative PageIndex"),
    WorkorderCase("17775885", "Negative PageSize"),
    WorkorderCase("17775886", "PageSize = 0"),
    WorkorderCase("17775888", "Invalid Status value"),
    WorkorderCase("17775889", "Status special characters (@#$)"),
    WorkorderCase("17775890", "RequesterId as string"),
    WorkorderCase("17775891", "RequesterId with negative value"),
    WorkorderCase("17775892", "WorkorderNumber contains special characters"),
    WorkorderCase("17775893", "PageSize as string"),
    WorkorderCase("17775894", "PageIndex as string"),
    WorkorderCase("17775895", "SQL injection in WorkorderNumber"),
    WorkorderCase("17775896", "Script injection (<script>)"),
    WorkorderCase("17775897", "Extremely long WorkorderNumber"),
    WorkorderCase("17775898", "Request without authentication"),
    WorkorderCase("17775899", "Expired access token"),
    WorkorderCase("17775900", "Invalid token"),
    WorkorderCase("17775901", "User with insufficient permissions"),
    WorkorderCase("17775902", "Verify sensitive information not exposed"),
    WorkorderCase("17775903", "SQL Injection attempt"),
    WorkorderCase("17775904", "XSS attack input"),
    WorkorderCase("17775905", "Response time for default request"),
    WorkorderCase("17775906", "Maximum PageSize request"),
    WorkorderCase("17775907", "Concurrent users access API"),
    WorkorderCase("17775908", "High-volume requests"),
    WorkorderCase("17775909", "Validate totalRecords count"),
    WorkorderCase("17775910", "Verify no duplicate workorders"),
    WorkorderCase("17775911", "Validate dateCreated values"),
    WorkorderCase("17775912", "Validate dateModified  dateCreated"),
    WorkorderCase("17775913", "Verify email formats"),
    WorkorderCase("17775914", "Validate providerSummary data"),
    WorkorderCase("17775915", "Validate owner information"),
]


def _params() -> list:
    return [
        pytest.param(
            case,
            id=f"C{case.case_id}",
            marks=[
                pytest.mark.swagger_section("Workorders"),
                pytest.mark.swagger_operation("GET /workorders"),
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


def _normalize_name(value) -> str:
    return _normalize_text(value).replace(" ", "").lower()


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


def _workorders_get(api_context, params: dict | None = None):
    return api_context.get("workorders", params=params or {}, headers={"accept": "text/plain"})


def _requests_get(path: str, *, params: dict | None = None, bearer: str | None = None):
    base = os.environ["BASE_URL"].rstrip("/")
    url = f"{base}/{path.lstrip('/')}"
    headers = {"accept": "text/plain"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    return requests.get(url, params=params or {}, headers=headers, timeout=120)


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


def _extract(payload: dict) -> tuple[dict, list[dict]]:
    pagination = payload.get("pagination", {}) if isinstance(payload, dict) else {}
    items = payload.get("items", []) if isinstance(payload, dict) else []
    return pagination if isinstance(pagination, dict) else {}, items if isinstance(items, list) else []


def _assert_common_shape(payload: dict, *, title: str):
    pagination, items = _extract(payload)
    assert isinstance(pagination, dict), f"{title}: pagination object is missing"
    assert isinstance(items, list), f"{title}: items must be an array"
    assert isinstance(pagination.get("index"), int), f"{title}: pagination.index must be integer"
    assert isinstance(pagination.get("size"), int), f"{title}: pagination.size must be integer"
    assert isinstance(pagination.get("totalRecords"), int), f"{title}: pagination.totalRecords must be integer"
    return pagination, items


def _assert_item_fields(item: dict, *, title: str):
    assert isinstance(item.get("number"), str), f"{title}: number must be string"
    assert isinstance(item.get("providerSummary"), list), f"{title}: providerSummary must be array"
    assert isinstance(item.get("workorderOwner"), dict), f"{title}: workorderOwner object missing"


def _validate_provider_summary_types(item: dict, *, title: str):
    for summary in item.get("providerSummary", []):
        assert isinstance(summary.get("statusId"), int), f"{title}: statusId must be integer"
        assert isinstance(summary.get("statusName"), str), f"{title}: statusName must be string"
        assert isinstance(summary.get("serviceIds"), list), f"{title}: serviceIds must be list"
        assert isinstance(summary.get("providerId"), int), f"{title}: providerId must be integer"
        if summary.get("dateCompleted") is not None:
            assert "T" in str(summary.get("dateCompleted")), f"{title}: dateCompleted not ISO-8601"
        if summary.get("dateModified") is not None:
            assert "T" in str(summary.get("dateModified")), f"{title}: dateModified not ISO-8601"


def _db_count(db, filters: dict | None = None) -> int:
    filters = filters or {}
    where = ["1=1"]
    params: list = []

    if filters.get("workorderNumber") is not None:
        where.append("w.Number = ?")
        params.append(str(filters["workorderNumber"]))
    if filters.get("source") is not None:
        where.append("w.SourceId = ?")
        params.append(int(filters["source"]))
    if filters.get("status") is not None:
        where.append("ws.WorkorderStatusId = ?")
        params.append(int(filters["status"]))
    if filters.get("requesterId") is not None:
        where.append("wr.EmployeeId = ?")
        params.append(int(filters["requesterId"]))

    query = (
        "SELECT COUNT(DISTINCT w.Number) AS cnt "
        "FROM cimpl.Workorder w "
        "LEFT JOIN cimpl.WorkorderSummary ws ON w.WorkorderId = ws.WorkorderId "
        "LEFT JOIN cimpl.WorkorderRequester wr ON wr.WorkorderRequesterId = ws.WorkorderRequesterId "
        "WHERE " + " AND ".join(where)
    )
    return int(db.fetch_scalar(query, tuple(params)) or 0)


def _db_sample_values(db) -> dict:
    row = db.fetch_one(
        """
        SELECT TOP 1
            w.Number AS workorderNumber,
            ws.WorkorderStatusId AS statusId,
            wr.EmployeeId AS requesterId,
            w.SourceId AS sourceId
        FROM cimpl.Workorder w
        INNER JOIN cimpl.WorkorderSummary ws ON w.WorkorderId = ws.WorkorderId
        INNER JOIN cimpl.WorkorderRequester wr ON wr.WorkorderRequesterId = ws.WorkorderRequesterId
        WHERE ws.WorkorderRequesterId IS NOT NULL
          AND wr.EmployeeId IS NOT NULL
        ORDER BY ws.ModifiedDate DESC
        """
    )
    assert row, "Unable to find sample workorder row for filter tests"
    return {
        "workorderNumber": _normalize_text(row["workorderNumber"]),
        "status": int(row["statusId"]),
        "requesterId": int(row["requesterId"]),
        "source": int(row["sourceId"]),
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
            u.UserName AS workOrderOwnerUserName,
            u.FirstName + u.LastName AS workOrderOwnerName,
            u.Email AS workOrderOwnerEmail,
            wr.Email AS requesterEmail,
            wr.FirstName AS requesterFirstName,
            wr.LastName AS requesterLastName
        FROM cimpl.Workorder w
        INNER JOIN cimpl.WorkorderSummary ws ON w.WorkorderId = ws.WorkorderId
        INNER JOIN cimpl.WorkorderStatus wss ON ws.WorkorderStatusId = wss.WorkorderStatusId
        INNER JOIN cimpl.WorkorderInventory wi ON wi.WorkorderSummaryId = ws.WorkorderSummaryId
        INNER JOIN dict.OperationSource os ON w.SourceId = os.OperationSourceId
        INNER JOIN usr.Users u ON u.UserId = w.UserId
        LEFT JOIN cimpl.WorkorderRequester wr ON wr.WorkorderRequesterId = ws.WorkorderRequesterId
        INNER JOIN cimpl.TransactServiceInventory tsi
            ON tsi.ServiceInventoryId = wi.ServiceInventoryId
           AND tsi.TransactId = wi.TransactId
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
            int(row["requesterId"]) if row.get("requesterId") is not None else None,
            _normalize_date(row.get("dueDate")),
            _normalize_date(row.get("modifiedDate")),
        )
        grouped.setdefault(key, set())
        if row.get("primaryNumber") is not None:
            grouped[key].add(_normalize_text(row["primaryNumber"]))
    return grouped


def _group_api_provider_summary(provider_summary: list[dict]) -> dict[tuple, set[str]]:
    grouped: dict[tuple, set[str]] = {}
    for summary in provider_summary:
        requestor = summary.get("requestor")
        if isinstance(requestor, dict):
            requestor_id = int(requestor.get("id")) if requestor.get("id") is not None else None
        elif requestor is not None:
            requestor_id = int(requestor)
        else:
            requestor_id = None

        key = (
            int(summary["providerId"]) if summary.get("providerId") is not None else None,
            int(summary["statusId"]) if summary.get("statusId") is not None else None,
            _normalize_text(summary.get("statusName")),
            _normalize_date(summary.get("dateCompleted")),
            requestor_id,
            _normalize_date(summary.get("dateDue")),
            _normalize_date(summary.get("dateModified")),
        )
        grouped.setdefault(key, set())
        for service_id in summary.get("serviceIds", []) or []:
            grouped[key].add(_normalize_text(service_id))
    return grouped


def _validate_number_db_match(api_item: dict, db_rows: list[dict], *, title: str):
    assert db_rows, f"{title}: no DB rows for workorder {api_item.get('number')}"
    first_row = db_rows[0]

    owner = api_item.get("workorderOwner", {}) or {}
    assert _normalize_text(owner.get("userName")).lower() == _normalize_text(
        first_row.get("workOrderOwnerUserName")
    ).lower(), f"{title}: owner userName mismatch"
    assert _normalize_name(owner.get("name")) == _normalize_name(
        first_row.get("workOrderOwnerName")
    ), f"{title}: owner name mismatch"
    assert _normalize_text(owner.get("email")).lower() == _normalize_text(
        first_row.get("workOrderOwnerEmail")
    ).lower(), f"{title}: owner email mismatch"

    api_grouped = _group_api_provider_summary(api_item.get("providerSummary", []) or [])
    db_grouped = _group_expected_provider_summary(db_rows)
    assert set(api_grouped.keys()) == set(db_grouped.keys()), (
        f"{title}: providerSummary group mismatch. "
        f"API-only={list(set(api_grouped.keys()) - set(db_grouped.keys()))[:10]}, "
        f"DB-only={list(set(db_grouped.keys()) - set(api_grouped.keys()))[:10]}"
    )
    for key in db_grouped:
        assert api_grouped[key] == db_grouped[key], f"{title}: serviceIds mismatch for key={key}"


def _run_case(case: WorkorderCase, api_context, db, request):
    cid = case.case_id
    title = case.title
    sample = _db_sample_values(db)
    metrics = [
        f"CaseId: {cid}",
        f"Title: {title}",
        f"DB target: {os.getenv('DB_SERVER', '')} / {os.getenv('DB_NAME', '')}",
    ]

    response = None
    params: dict = {}

    # Security/auth cases using raw requests so we can control auth headers.
    if cid == "17775898":
        response = _requests_get("workorders")
        assert response.status_code == 401, f"{title}: expected 401, got {response.status_code}"
        metrics += [f"Response status: {response.status_code}", "Validation: request without auth is rejected."]
        _record_dashboard_metrics(request, metrics)
        return

    if cid in {"17775899", "17775900"}:
        response = _requests_get("workorders", bearer="invalid-or-expired-token")
        assert response.status_code == 401, f"{title}: expected 401, got {response.status_code}"
        metrics += [f"Response status: {response.status_code}", "Validation: invalid/expired token rejected."]
        _record_dashboard_metrics(request, metrics)
        return

    if cid == "17775901":
        no_perm_token = os.getenv("API_NO_PERMISSION_TOKEN", "").strip()
        if not no_perm_token:
            pytest.skip("API_NO_PERMISSION_TOKEN is not configured for 403 validation")
        response = _requests_get("workorders", bearer=no_perm_token)
        assert response.status_code == 403, f"{title}: expected 403, got {response.status_code}"
        metrics += [f"Response status: {response.status_code}", "Validation: insufficient permission token rejected."]
        _record_dashboard_metrics(request, metrics)
        return

    # Performance and concurrency checks.
    if cid == "17775905":
        start = time.perf_counter()
        response = _workorders_get(api_context)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert response.status == 200, f"{title}: expected 200, got {response.status}. Response={response.text()}"
        assert elapsed_ms < 15000, f"{title}: response time too high ({elapsed_ms:.2f} ms)"
        metrics += [f"Response status: {response.status}", f"Elapsed ms: {elapsed_ms:.2f}"]
        _record_dashboard_metrics(request, metrics)
        return

    if cid == "17775907":
        token = _auth_token()

        def _call_once():
            return _requests_get("workorders", bearer=token, params={"pageIndex": 0, "pageSize": 10}).status_code

        with ThreadPoolExecutor(max_workers=5) as ex:
            statuses = list(ex.map(lambda _: _call_once(), range(5)))
        assert all(code == 200 for code in statuses), f"{title}: expected all 200, got {statuses}"
        metrics += [f"Statuses: {statuses}", "Validation: concurrent requests returned success."]
        _record_dashboard_metrics(request, metrics)
        return

    if cid == "17775908":
        statuses = []
        for _ in range(20):
            statuses.append(_workorders_get(api_context, {"pageIndex": 0, "pageSize": 10}).status)
        assert all(code == 200 for code in statuses), f"{title}: expected all 200, got {statuses}"
        metrics += [f"Status sample: {statuses[:10]}", "Validation: high-volume sequential requests passed."]
        _record_dashboard_metrics(request, metrics)
        return

    # Main parameter mapping.
    if cid == "17775847":
        params = {"workorderNumber": sample["workorderNumber"]}
    elif cid == "17775848":
        params = {"requesterId": sample["requesterId"]}
    elif cid == "17775849":
        params = {"status": sample["status"]}
    elif cid == "17775851":
        params = {"source": sample["source"]}
    elif cid in {"17775855"}:
        params = {"workorderNumber": "__NO_SUCH_WORKORDER__"}
    elif cid == "17775856":
        params = {"requesterId": 999999999}
    elif cid == "17775857":
        params = {"status": 999999999}
    elif cid == "17775922":
        params = {"source": sample["source"]}
    elif cid == "17775923":
        params = {"status": sample["status"], "requesterId": sample["requesterId"]}
    elif cid == "17775924":
        params = {"status": sample["status"], "workorderNumber": sample["workorderNumber"]}
    elif cid == "17775925":
        params = {"status": sample["status"], "source": sample["source"]}
    elif cid == "17775926":
        params = {"requesterId": sample["requesterId"], "workorderNumber": sample["workorderNumber"]}
    elif cid == "17775927":
        params = {"requesterId": sample["requesterId"], "source": sample["source"]}
    elif cid == "17775928":
        params = {"workorderNumber": sample["workorderNumber"], "source": sample["source"]}
    elif cid == "17775929":
        params = {
            "status": sample["status"],
            "requesterId": sample["requesterId"],
            "workorderNumber": sample["workorderNumber"],
        }
    elif cid == "17775930":
        params = {"status": sample["status"], "requesterId": sample["requesterId"], "source": sample["source"]}
    elif cid == "17775931":
        params = {"status": sample["status"], "workorderNumber": sample["workorderNumber"], "source": sample["source"]}
    elif cid == "17775932":
        params = {
            "requesterId": sample["requesterId"],
            "workorderNumber": sample["workorderNumber"],
            "source": sample["source"],
        }
    elif cid == "17775933":
        params = {
            "status": sample["status"],
            "requesterId": sample["requesterId"],
            "workorderNumber": sample["workorderNumber"],
            "source": sample["source"],
        }
    elif cid == "17775879":
        params = {"pageSize": 1, "pageIndex": 0}
    elif cid == "17775880":
        params = {"pageSize": 10, "pageIndex": 0}
    elif cid == "17775881":
        params = {"pageSize": 100, "pageIndex": 0}
    elif cid == "17775882":
        params = {"pageSize": 1000, "pageIndex": 0}
    elif cid == "17775884":
        params = {"pageIndex": -1, "pageSize": 10}
    elif cid == "17775885":
        params = {"pageIndex": 0, "pageSize": -1}
    elif cid == "17775886":
        params = {"pageIndex": 0, "pageSize": 0}
    elif cid == "17775888":
        params = {"status": "invalid"}
    elif cid == "17775889":
        params = {"status": "@#$"}
    elif cid == "17775890":
        params = {"requesterId": "abc"}
    elif cid == "17775891":
        params = {"requesterId": -999}
    elif cid == "17775892":
        params = {"workorderNumber": "##$$"}
    elif cid == "17775893":
        params = {"pageSize": "ten"}
    elif cid == "17775894":
        params = {"pageIndex": "one"}
    elif cid == "17775895":
        params = {"workorderNumber": "' OR 1=1 --"}
    elif cid == "17775896":
        params = {"workorderNumber": "<script>alert(1)</script>"}
    elif cid == "17775897":
        params = {"workorderNumber": "X" * 5000}
    elif cid == "17775903":
        params = {"status": "1 OR 1=1"}
    elif cid == "17775904":
        params = {"workorderNumber": "<img src=x onerror=alert(1)>"}
    elif cid == "17775906":
        params = {"pageSize": 100, "pageIndex": 0}
    elif cid in {"17775914", "17775915"}:
        params = {"workorderNumber": "8888"}

    # Dynamic pagination page tests.
    if cid in {"17775852", "17775853", "17775854", "17775883"}:
        baseline = _workorders_get(api_context, {"pageIndex": 0, "pageSize": 10})
        assert baseline.status == 200, f"{title}: baseline call failed. Response={baseline.text()}"
        baseline_payload = baseline.json()
        total = int(baseline_payload.get("pagination", {}).get("totalRecords", 0))
        size = 10
        if cid == "17775852":
            params = {"pageIndex": 0, "pageSize": size}
        elif cid == "17775853":
            params = {"pageIndex": max((total // size) // 2, 1), "pageSize": size}
        elif cid == "17775854":
            params = {"pageIndex": max(((total - 1) // size), 0), "pageSize": size}
        else:
            params = {"pageIndex": max((total // size) + 10, 1000), "pageSize": size}

    response = _workorders_get(api_context, params)
    payload = _safe_json(response)
    pagination, items = _extract(payload)

    metrics += [
        f"Request params: {json.dumps(params, ensure_ascii=True)}",
        f"Response status: {response.status}",
        f"Pagination: {json.dumps(pagination, ensure_ascii=True)}",
        f"API item count: {len(items)}",
    ]

    # Negative cases with expected validation/empty behavior.
    if cid in {"17775857", "17775884", "17775885", "17775888", "17775889", "17775890", "17775893", "17775894"}:
        assert response.status == 400, f"{title}: expected 400, got {response.status}. Response={response.text()}"
        _record_dashboard_metrics(request, metrics)
        return

    if cid in {"17775882", "17775886", "17775891", "17775892", "17775895", "17775896", "17775897", "17775903", "17775904"}:
        assert response.status in {200, 400, 404}, (
            f"{title}: expected 200/400, got {response.status}. Response={response.text()}"
        )
        _record_dashboard_metrics(request, metrics)
        return

    assert response.status == 200, f"{title}: expected 200, got {response.status}. Response={response.text()}"
    pagination, items = _assert_common_shape(payload, title=title)

    if items:
        _assert_item_fields(items[0], title=title)

    # Count checks for filter and data-integrity cases.
    if cid in {
        "17775846", "17775847", "17775848", "17775849", "17775851", "17775855", "17775856",
        "17775922", "17775923", "17775924", "17775925", "17775926", "17775927", "17775928", "17775929",
        "17775930", "17775931", "17775932", "17775933", "17775909",
    }:
        expected_total = _db_count(db, {k: v for k, v in params.items() if k in {"workorderNumber", "status", "requesterId", "source"}})
        actual_total = int(pagination.get("totalRecords", -1))
        if cid in {"17775846", "17775851", "17775909", "17775922"}:
            assert abs(actual_total - expected_total) <= 1, (
                f"{title}: totalRecords mismatch beyond tolerance API={actual_total} DB={expected_total}"
            )
        else:
            assert actual_total == expected_total, (
                f"{title}: totalRecords mismatch API={actual_total} DB={expected_total}"
            )
        metrics.append(f"DB expected totalRecords: {expected_total}")

    # Pagination-specific checks.
    if cid in {"17775879", "17775880", "17775881", "17775906"}:
        expected_size = int(params["pageSize"])
        assert int(pagination.get("size", -1)) == expected_size, f"{title}: pagination.size mismatch"
        assert len(items) <= expected_size, f"{title}: API returned more than page size"

    if cid == "17775883":
        assert len(items) == 0, f"{title}: expected empty items for pageIndex beyond range"

    # Response/schema validation case checks.
    if cid in {"17775865", "17775866", "17775867", "17775868", "17775869", "17775870"} and items:
        _validate_provider_summary_types(items[0], title=title)

    if cid in {"17775871", "17775872", "17775873", "17775874"}:
        # Use a known dataset that includes providerSummary.requestor details.
        params = {"workorderNumber": "8888"}
        response = _workorders_get(api_context, params)
        payload = _safe_json(response)
        pagination, items = _extract(payload)
        metrics += [
            f"Requester validation params: {json.dumps(params, ensure_ascii=True)}",
            f"Requester validation status: {response.status}",
            f"Requester validation item count: {len(items)}",
        ]
        assert response.status == 200, (
            f"{title}: expected 200 for requester-backed dataset, got {response.status}. "
            f"Response={response.text()}"
        )

        found_requestor = None
        for item in items:
            for summary in item.get("providerSummary", []):
                if summary.get("requestor") is not None:
                    found_requestor = summary.get("requestor")
                    break
            if found_requestor is not None:
                break
        assert found_requestor is not None, f"{title}: no providerSummary.requestor object found"
        assert isinstance(found_requestor, dict), f"{title}: requestor must be object"
        if cid == "17775872":
            assert isinstance(found_requestor.get("id"), int), f"{title}: requestor.id must be integer"
        if cid == "17775873":
            assert isinstance(found_requestor.get("number"), str), f"{title}: requestor.number must be string"
        if cid == "17775874":
            assert EMAIL_RE.match(_normalize_text(found_requestor.get("email"))), (
                f"{title}: requestor.email invalid -> {found_requestor.get('email')}"
            )

    if cid in {"17775875", "17775876", "17775877", "17775878", "17775913"} and items:
        owner = items[0].get("workorderOwner", {})
        assert isinstance(owner, dict), f"{title}: workorderOwner object missing"
        assert _normalize_text(owner.get("userName")), f"{title}: workorderOwner.userName not populated"
        assert EMAIL_RE.match(_normalize_text(owner.get("email"))), f"{title}: invalid owner email"

    if cid == "17775910":
        numbers = [_normalize_text(item.get("number")) for item in items]
        assert len(numbers) == len(set(numbers)), f"{title}: duplicate workorders found in page"

    if cid == "17775911":
        for item in items:
            created = _normalize_text(item.get("dateCreated"))
            assert "T" in created, f"{title}: dateCreated not ISO-like -> {created}"

    if cid == "17775912":
        for item in items:
            created = _normalize_date(item.get("dateCreated"))
            for summary in item.get("providerSummary", []):
                modified = _normalize_date(summary.get("dateModified"))
                # Upstream uses sentinel min date for not-modified records.
                if created and modified and modified[:4] > "1900":
                    assert modified >= created, (
                        f"{title}: dateModified {modified} earlier than dateCreated {created}"
                    )

    if cid == "17775902":
        text = response.text().lower()
        for sensitive in ["password", "client_secret", "token="]:
            assert sensitive not in text, f"{title}: sensitive text '{sensitive}' exposed in response"

    if cid in {"17775914", "17775915"}:
        assert len(items) == 1, f"{title}: expected one record for workorderNumber filter"
        db_rows = _expected_workorder_rows(db, "8888")
        _validate_number_db_match(items[0], db_rows, title=title)
        metrics.append(f"DB raw joined rows for 8888: {len(db_rows)}")

    _record_dashboard_metrics(request, metrics)


class TestGetWorkorders:

    @pytest.mark.parametrize("case", _params())
    def test_get_workorders_cases(self, case, api_context, db, request):
        _run_case(case, api_context, db, request)
