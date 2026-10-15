"""Tests for POST /user/usersEmployees with DB-backed validation and TestRail mapping."""

from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import dataclass

import pytest
import requests


API_PATH = "user/usersEmployees"


@dataclass(frozen=True)
class UserEmployeesCase:
    case_id: str
    title: str


CASES: list[UserEmployeesCase] = [
    UserEmployeesCase("17775514", "Functional - Verify API returns active employees when includeMultiTenantUsers=false"),
    UserEmployeesCase("17775515", "Functional - Verify API returns active employees when includeMultiTenantUsers omitted"),
    UserEmployeesCase("17775516", "Functional - Verify API returns active employees and multi-tenant users when includeMultiTenantUsers=true"),
    UserEmployeesCase("17775517", "Functional - Filter by firstName"),
    UserEmployeesCase("17775518", "Functional - Filter by lastName"),
    UserEmployeesCase("17775519", "Functional - Filter by email"),
    UserEmployeesCase("17775520", "Functional - Filter by firstName, lastName and email combination"),
    UserEmployeesCase("17775521", "Functional - Verify pagination default values"),
    UserEmployeesCase("17775522", "Functional - Verify custom pagination values"),
    UserEmployeesCase("17775523", "Functional - Verify sorting by firstName then lastName"),
    UserEmployeesCase("17775524", "Functional - Verify employee response fields"),
    UserEmployeesCase("17775525", "Functional - Verify multi-tenant response fields"),
    UserEmployeesCase("17775526", "Functional - Verify inactive employees excluded"),
    UserEmployeesCase("17775527", "Functional - Verify inactive multi-tenant users excluded"),
    UserEmployeesCase("17775528", "Functional - Verify duplicate users deduplicated by userId"),
    UserEmployeesCase("17775529", "Negative - Invalid includeMultiTenantUsers value (string)"),
    UserEmployeesCase("17775530", "Negative - Negative pageIndex"),
    UserEmployeesCase("17775531", "Negative - Negative pageSize"),
    UserEmployeesCase("17775532", "Negative - pageSize=0"),
    UserEmployeesCase("17775533", "Negative - Invalid email format filter"),
    UserEmployeesCase("17775534", "Negative - Non-existent firstName filter"),
    UserEmployeesCase("17775535", "Negative - Non-existent email filter"),
    UserEmployeesCase("17775536", "Negative - Unsupported request attribute supplied"),
    UserEmployeesCase("17775537", "Edge Case - Single active employee exists"),
    UserEmployeesCase("17775538", "Edge Case - No active employees available"),
    UserEmployeesCase("17775539", "Edge Case - Very large pageSize at upper boundary"),
    UserEmployeesCase("17775540", "Edge Case - pageIndex beyond available pages"),
    UserEmployeesCase("17775541", "Edge Case - Names containing special characters"),
    UserEmployeesCase("17775542", "Edge Case - Names with leading/trailing spaces"),
    UserEmployeesCase("17775543", "Edge Case - Case-insensitive filtering"),
    UserEmployeesCase("17775544", "Edge Case - Multiple users with same firstName and lastName"),
    UserEmployeesCase("17775545", "Edge Case - Null optional filters"),
    UserEmployeesCase("17775546", "Security - Request without authentication token"),
    UserEmployeesCase("17775547", "Security - Request with invalid/expired token"),
    UserEmployeesCase("17775548", "Security - User lacks permission to endpoint"),
    UserEmployeesCase("17775549", "Security - SQL injection in firstName filter"),
    UserEmployeesCase("17775550", "Security - SQL injection in email filter"),
    UserEmployeesCase("17775551", "Security - Cross-site scripting payload in filters"),
    UserEmployeesCase("17775552", "Security - Excessively long string input"),
    UserEmployeesCase("17775553", "Security - Verify tenant data isolation"),
    UserEmployeesCase("17775554", "Security - Verify response exposure validation"),
]


def _params() -> list:
    out = []
    for case in CASES:
        out.append(
            pytest.param(
                case,
                id=f"C{case.case_id}",
                marks=[
                    pytest.mark.swagger_section("Users"),
                    pytest.mark.swagger_operation("POST /user/usersEmployees"),
                    pytest.mark.testrail_case(case.case_id),
                    pytest.mark.dashboard_title(case.title),
                ],
            )
        )
    return out


def _record_metrics(request, lines: list[str]):
    request.node.user_properties.append(("dashboard_metrics", "\n".join(lines)))


def _payload(
    *,
    page_index: int | None = 0,
    page_size: int | None = 100,
    first_name: str | None = None,
    last_name: str | None = None,
    email: str | None = None,
    include_multi_tenant_users: bool | None = None,
) -> dict:
    body: dict = {
        "firstName": first_name,
        "lastName": last_name,
        "email": email,
    }

    if page_index is not None:
        body["pageIndex"] = page_index
    if page_size is not None:
        body["pageSize"] = page_size
    if include_multi_tenant_users is not None:
        body["includeMultiTenantUsers"] = include_multi_tenant_users

    return body


def _post_users_employees(api_context, payload: dict):
    return api_context.post(
        API_PATH,
        data=json.dumps(payload, ensure_ascii=True),
        headers={"Content-Type": "application/json", "accept": "text/plain"},
    )


def _post_without_auth(payload: dict):
    base_url = os.environ["BASE_URL"].rstrip("/")
    url = f"{base_url}/{API_PATH}"
    return requests.post(
        url,
        json=payload,
        headers={"Content-Type": "application/json", "accept": "text/plain"},
        timeout=60,
    )


def _post_with_bearer(payload: dict, bearer: str):
    base_url = os.environ["BASE_URL"].rstrip("/")
    url = f"{base_url}/{API_PATH}"
    return requests.post(
        url,
        json=payload,
        headers={
            "Content-Type": "application/json",
            "accept": "text/plain",
            "Authorization": f"Bearer {bearer}",
        },
        timeout=60,
    )


def _safe_json(response) -> dict:
    try:
        parsed = response.json()
        return parsed if isinstance(parsed, dict) else {"raw": parsed}
    except Exception:  # noqa: BLE001
        return {}


def _norm_text(value) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def _db_employee_rows(db, filters: dict | None = None) -> list[dict]:
    where = ["e.EmployeeStatusId = 1"]
    params: list[str] = []
    filters = filters or {}

    if filters.get("firstName"):
        where.append("e.FirstName LIKE '%' + ? + '%' ")
        params.append(str(filters["firstName"]))
    if filters.get("lastName"):
        where.append("e.LastName LIKE '%' + ? + '%' ")
        params.append(str(filters["lastName"]))
    if filters.get("email"):
        where.append("e.EMail LIKE '%' + ? + '%' ")
        params.append(str(filters["email"]))

    query = """
        SELECT
            e.FirstName AS firstName,
            e.LastName AS lastName,
            e.EMail AS email,
            e.EmployeeId AS employeeId,
            u.UserId AS userId,
            u.UserName AS userName,
            CAST(0 AS bit) AS isCrossTenant
        FROM cimpl.Employee e
        INNER JOIN usr.Users u ON e.LogonUser = u.UserName
        WHERE
    """ + " AND ".join(where)

    return db.fetch_all(query, tuple(params))


def _db_cross_tenant_rows(db, filters: dict | None = None) -> list[dict]:
    where = ["u.UserTypeId = 2", "u.StatusId = 1"]
    params: list[str] = []
    filters = filters or {}

    if filters.get("firstName"):
        where.append("u.FirstName LIKE '%' + ? + '%' ")
        params.append(str(filters["firstName"]))
    if filters.get("lastName"):
        where.append("u.LastName LIKE '%' + ? + '%' ")
        params.append(str(filters["lastName"]))
    if filters.get("email"):
        where.append("u.UserName LIKE '%' + ? + '%' ")
        params.append(str(filters["email"]))

    query = """
        SELECT
            u.FirstName AS firstName,
            u.LastName AS lastName,
            CAST(NULL AS NVARCHAR(320)) AS email,
            CAST(NULL AS INT) AS employeeId,
            u.UserId AS userId,
            u.UserName AS userName,
            CAST(1 AS bit) AS isCrossTenant
        FROM usr.Users u
        WHERE
    """ + " AND ".join(where)

    return db.fetch_all(query, tuple(params))


def _expected_rows(db, filters: dict | None, include_multi_tenant_users: bool) -> list[dict]:
    employees = _db_employee_rows(db, filters)
    if not include_multi_tenant_users:
        return employees

    cross_users = _db_cross_tenant_rows(db, filters)
    merged: dict[int, dict] = {}

    for row in employees:
        if row.get("userId") is None:
            continue
        merged[int(row["userId"])] = row

    for row in cross_users:
        if row.get("userId") is None:
            continue
        user_id = int(row["userId"])
        if user_id not in merged:
            merged[user_id] = row

    return list(merged.values())


def _api_items(payload: dict) -> list[dict]:
    items = payload.get("items", [])
    return items if isinstance(items, list) else []


def _to_map_by_user_id(rows: list[dict], *, title: str, source_name: str) -> dict[int, dict]:
    out: dict[int, dict] = {}
    duplicates: list[int] = []
    for row in rows:
        if row.get("userId") is None:
            continue
        key = int(row["userId"])
        if key in out:
            duplicates.append(key)
        out[key] = row
    assert not duplicates, f"{title}: duplicate userId values in {source_name}: {duplicates[:20]}"
    return out


def _api_baseline_rows(api_context, include_multi_tenant_users: bool) -> list[dict]:
    body = _payload(page_index=0, page_size=5000, include_multi_tenant_users=include_multi_tenant_users)
    response = _post_users_employees(api_context, body)
    assert response.status == 200, (
        f"Unable to fetch baseline API rows for includeMultiTenantUsers={include_multi_tenant_users}. "
        f"Status={response.status}, body={response.text()}"
    )
    return _api_items(response.json())


def _assert_response_matches_db(
    *,
    title: str,
    payload: dict,
    response,
    expected_rows: list[dict],
    include_multi_tenant_users: bool,
    enforce_total_records: bool,
):
    assert response.status == 200, f"{title}: expected 200, got {response.status}. Response={response.text()}"

    body = response.json()
    pagination = body.get("pagination", {})
    items = _api_items(body)

    assert isinstance(pagination, dict), f"{title}: missing pagination object"
    assert int(pagination.get("index", -1)) >= 0, f"{title}: pagination.index must be >= 0"
    assert int(pagination.get("size", 0)) > 0, f"{title}: pagination.size must be > 0"

    expected_map = _to_map_by_user_id(expected_rows, title=title, source_name="DB rows")
    api_map = _to_map_by_user_id(items, title=title, source_name="API items")

    total_records = int(pagination.get("totalRecords", -1))
    if enforce_total_records:
        assert total_records == len(expected_map), (
            f"{title}: pagination.totalRecords mismatch. API={total_records} DB={len(expected_map)}"
        )
    else:
        assert total_records >= len(api_map), (
            f"{title}: pagination.totalRecords must be >= returned item count. "
            f"totalRecords={total_records}, returned={len(api_map)}"
        )

    unexpected_ids = sorted(set(api_map.keys()) - set(expected_map.keys()))
    assert not unexpected_ids, f"{title}: API returned userIds not in DB set: {unexpected_ids[:20]}"

    for user_id, api_row in api_map.items():
        expected = expected_map[user_id]
        assert _norm_text(api_row.get("firstName")) == _norm_text(expected.get("firstName")), (
            f"{title}: firstName mismatch for userId={user_id}. "
            f"API={api_row.get('firstName')} DB={expected.get('firstName')}"
        )
        assert _norm_text(api_row.get("lastName")) == _norm_text(expected.get("lastName")), (
            f"{title}: lastName mismatch for userId={user_id}. "
            f"API={api_row.get('lastName')} DB={expected.get('lastName')}"
        )
        assert _norm_text(api_row.get("userName")) == _norm_text(expected.get("userName")), (
            f"{title}: userName mismatch for userId={user_id}. "
            f"API={api_row.get('userName')} DB={expected.get('userName')}"
        )

        if expected.get("employeeId") is not None:
            assert api_row.get("employeeId") == expected.get("employeeId"), (
                f"{title}: employeeId mismatch for employee userId={user_id}. "
                f"API={api_row.get('employeeId')} DB={expected.get('employeeId')}"
            )
            assert _norm_text(api_row.get("email")) == _norm_text(expected.get("email")), (
                f"{title}: email mismatch for employee userId={user_id}. "
                f"API={api_row.get('email')} DB={expected.get('email')}"
            )
        elif include_multi_tenant_users:
            assert api_row.get("employeeId") is None, (
                f"{title}: expected employeeId to be null for cross-tenant userId={user_id}. "
                f"Actual={api_row.get('employeeId')}"
            )


def _find_value_with_count(rows: list[dict], field: str, *, target: str) -> str | None:
    buckets: dict[str, int] = defaultdict(int)
    original: dict[str, str] = {}

    for row in rows:
        raw = row.get(field)
        text = str(raw).strip() if raw is not None else ""
        if not text:
            continue
        key = text.lower()
        buckets[key] += 1
        original.setdefault(key, text)

    for key, count in buckets.items():
        if target == "single" and count == 1:
            return original[key]
        if target == "multiple" and count > 1:
            return original[key]

    return None


def _find_combo_with_count(rows: list[dict], fields: list[str], *, target: str) -> dict | None:
    buckets: dict[tuple[str, ...], int] = defaultdict(int)
    original: dict[tuple[str, ...], tuple[str, ...]] = {}

    for row in rows:
        values: list[str] = []
        keys: list[str] = []
        valid = True
        for field in fields:
            raw = row.get(field)
            text = str(raw).strip() if raw is not None else ""
            if not text:
                valid = False
                break
            values.append(text)
            keys.append(text.lower())

        if not valid:
            continue

        key_tuple = tuple(keys)
        buckets[key_tuple] += 1
        original.setdefault(key_tuple, tuple(values))

    for key_tuple, count in buckets.items():
        if target == "single" and count == 1:
            values = original[key_tuple]
            return {fields[idx]: values[idx] for idx in range(len(fields))}
        if target == "multiple" and count > 1:
            values = original[key_tuple]
            return {fields[idx]: values[idx] for idx in range(len(fields))}

    return None


def _run_filter_case_with_modes(case: UserEmployeesCase, field_names: list[str], api_context, db, request):
    include_modes = [
        ("omitted", None, False),
        ("false", False, False),
        ("true", True, True),
    ]

    metrics = [
        f"CaseId: {case.case_id}",
        f"Title: {case.title}",
        f"DB target: {os.getenv('DB_SERVER', '')} / {os.getenv('DB_NAME', '')}",
        f"Filter keys: {field_names}",
        "Validation includes includeMultiTenantUsers in omitted/false/true modes with zero/single/multiple datasets.",
    ]

    for mode_label, mode_payload_value, mode_db_include in include_modes:
        base_rows = _api_baseline_rows(api_context, mode_db_include)

        if len(field_names) == 1:
            single_value = _find_value_with_count(base_rows, field_names[0], target="single")
            multiple_value = _find_value_with_count(base_rows, field_names[0], target="multiple")
            assert single_value is not None, f"{case.title} [{mode_label}]: no single-record dataset found"
            assert multiple_value is not None, f"{case.title} [{mode_label}]: no multiple-record dataset found"

            dataset_filters = [
                ("zero-record", {field_names[0]: "__NO_MATCH_FILTER__"}, 0),
                ("single-record", {field_names[0]: single_value}, 1),
                ("multiple-record", {field_names[0]: multiple_value}, None),
            ]
        else:
            single_filters = _find_combo_with_count(base_rows, field_names, target="single")
            multiple_filters = _find_combo_with_count(base_rows, field_names, target="multiple")
            assert single_filters is not None, f"{case.title} [{mode_label}]: no single-record dataset found"
            assert multiple_filters is not None, f"{case.title} [{mode_label}]: no multiple-record dataset found"
            zero_filters = {name: f"__NO_MATCH_{name.upper()}__" for name in field_names}
            dataset_filters = [
                ("zero-record", zero_filters, 0),
                ("single-record", single_filters, 1),
                ("multiple-record", multiple_filters, None),
            ]

        for dataset_label, filters, expected in dataset_filters:
            body = _payload(
                page_index=0,
                page_size=5000,
                first_name=filters.get("firstName"),
                last_name=filters.get("lastName"),
                email=filters.get("email"),
                include_multi_tenant_users=mode_payload_value,
            )
            response = _post_users_employees(api_context, body)
            expected_rows = _expected_rows(db, filters, mode_db_include)

            _assert_response_matches_db(
                title=f"{case.title} [{mode_label}][{dataset_label}]",
                payload=body,
                response=response,
                expected_rows=expected_rows,
                include_multi_tenant_users=mode_db_include,
                enforce_total_records=False,
            )

            actual_count = len(_api_items(response.json()))
            if expected == 0:
                assert actual_count == 0, f"{case.title} [{mode_label}][{dataset_label}]: expected zero, got {actual_count}"
            elif expected == 1:
                assert actual_count == 1, f"{case.title} [{mode_label}][{dataset_label}]: expected one, got {actual_count}"
            else:
                assert actual_count > 1, f"{case.title} [{mode_label}][{dataset_label}]: expected multiple, got {actual_count}"

            payload_body = response.json()
            metrics.append(f"--- Mode={mode_label}, Dataset={dataset_label}")
            metrics.append(f"Request payload: {json.dumps(body, ensure_ascii=True)}")
            metrics.append(f"Pagination: {json.dumps(payload_body.get('pagination', {}), ensure_ascii=True)}")
            metrics.append(f"DB expected count: {len(expected_rows)}")

    _record_metrics(request, metrics)


def _run_case(case: UserEmployeesCase, api_context, db, request):
    title = case.title
    case_id = case.case_id

    if case_id == "17775514":
        body = _payload(include_multi_tenant_users=False)
        response = _post_users_employees(api_context, body)
        expected = _expected_rows(db, {}, False)
        _assert_response_matches_db(
            title=title,
            payload=body,
            response=response,
            expected_rows=expected,
            include_multi_tenant_users=False,
            enforce_total_records=False,
        )
        _record_metrics(
            request,
            [
                f"CaseId: {case_id}",
                f"Title: {title}",
                f"Request payload: {json.dumps(body, ensure_ascii=True)}",
                f"DB expected count (employee only): {len(expected)}",
            ],
        )
        return

    if case_id == "17775515":
        body = _payload(include_multi_tenant_users=None)
        response = _post_users_employees(api_context, body)
        expected = _expected_rows(db, {}, False)
        _assert_response_matches_db(
            title=title,
            payload=body,
            response=response,
            expected_rows=expected,
            include_multi_tenant_users=False,
            enforce_total_records=False,
        )
        _record_metrics(
            request,
            [
                f"CaseId: {case_id}",
                f"Title: {title}",
                "Validation: includeMultiTenantUsers omitted behaves like false.",
                f"DB expected count (employee only): {len(expected)}",
            ],
        )
        return

    if case_id == "17775516":
        body = _payload(include_multi_tenant_users=True)
        response = _post_users_employees(api_context, body)
        expected = _expected_rows(db, {}, True)
        _assert_response_matches_db(
            title=title,
            payload=body,
            response=response,
            expected_rows=expected,
            include_multi_tenant_users=True,
            enforce_total_records=False,
        )
        employee_count = len(_db_employee_rows(db, {}))
        cross_count = len(_db_cross_tenant_rows(db, {}))
        _record_metrics(
            request,
            [
                f"CaseId: {case_id}",
                f"Title: {title}",
                f"DB employee rows: {employee_count}",
                f"DB active cross-tenant user rows: {cross_count}",
                f"DB expected merged unique users: {len(expected)}",
            ],
        )
        return

    if case_id == "17775517":
        _run_filter_case_with_modes(case, ["firstName"], api_context, db, request)
        return

    if case_id == "17775518":
        _run_filter_case_with_modes(case, ["lastName"], api_context, db, request)
        return

    if case_id == "17775519":
        _run_filter_case_with_modes(case, ["email"], api_context, db, request)
        return

    if case_id == "17775520":
        _run_filter_case_with_modes(case, ["firstName", "lastName", "email"], api_context, db, request)
        return

    if case_id == "17775521":
        body = {
            "firstName": None,
            "lastName": None,
            "email": None,
        }
        response = _post_users_employees(api_context, body)
        assert response.status == 200, f"{title}: expected 200, got {response.status}. Response={response.text()}"

        payload = response.json()
        pagination = payload.get("pagination", {})
        items = _api_items(payload)

        assert int(pagination.get("index", -1)) == 0, f"{title}: expected pagination.index=0"
        assert int(pagination.get("size", -1)) == 10, f"{title}: expected default pagination.size=10"

        expected = _expected_rows(db, {}, False)
        assert int(pagination.get("totalRecords", -1)) == len(expected), (
            f"{title}: totalRecords mismatch. API={pagination.get('totalRecords')} DB={len(expected)}"
        )
        assert len(items) <= 10, f"{title}: expected <=10 returned items, got {len(items)}"

        _record_metrics(
            request,
            [
                f"CaseId: {case_id}",
                f"Title: {title}",
                f"Request payload: {json.dumps(body, ensure_ascii=True)}",
                f"Pagination: {json.dumps(pagination, ensure_ascii=True)}",
                f"DB expected total (employee only): {len(expected)}",
                f"API returned page item count: {len(items)}",
            ],
        )
        return

    if case_id == "17775522":
        body = _payload(page_index=1, page_size=25, include_multi_tenant_users=False)
        response = _post_users_employees(api_context, body)
        expected = _expected_rows(db, {}, False)

        _assert_response_matches_db(
            title=title,
            payload=body,
            response=response,
            expected_rows=expected,
            include_multi_tenant_users=False,
            enforce_total_records=False,
        )

        payload = response.json()
        pagination = payload.get("pagination", {})
        items = _api_items(payload)
        assert int(pagination.get("index", -1)) == 1, f"{title}: expected pagination.index=1"
        assert int(pagination.get("size", -1)) == 25, f"{title}: expected pagination.size=25"
        assert len(items) <= 25, f"{title}: expected <=25 items, got {len(items)}"

        _record_metrics(
            request,
            [
                f"CaseId: {case_id}",
                f"Title: {title}",
                f"Request payload: {json.dumps(body, ensure_ascii=True)}",
                f"Pagination: {json.dumps(pagination, ensure_ascii=True)}",
                f"API returned page item count: {len(items)}",
                f"DB expected total (employee only): {len(expected)}",
            ],
        )
        return

    if case_id == "17775523":
        body = _payload(page_index=0, page_size=5000, include_multi_tenant_users=False)
        response = _post_users_employees(api_context, body)
        expected = _expected_rows(db, {}, False)
        _assert_response_matches_db(
            title=title,
            payload=body,
            response=response,
            expected_rows=expected,
            include_multi_tenant_users=False,
            enforce_total_records=False,
        )

        items = _api_items(response.json())
        names = [(_norm_text(item.get("firstName")), _norm_text(item.get("lastName"))) for item in items]
        assert names == sorted(names), f"{title}: results are not sorted by firstName then lastName"

        _record_metrics(
            request,
            [
                f"CaseId: {case_id}",
                f"Title: {title}",
                f"Request payload: {json.dumps(body, ensure_ascii=True)}",
                f"API returned count: {len(items)}",
                "Validation: sort order ascending by firstName then lastName.",
            ],
        )
        return

    if case_id == "17775524":
        body = _payload(include_multi_tenant_users=False)
        response = _post_users_employees(api_context, body)
        expected = _expected_rows(db, {}, False)
        _assert_response_matches_db(
            title=title,
            payload=body,
            response=response,
            expected_rows=expected,
            include_multi_tenant_users=False,
            enforce_total_records=False,
        )

        for item in _api_items(response.json()):
            assert item.get("employeeId") is not None, f"{title}: employeeId must be present for employee rows"
            assert item.get("userId") is not None, f"{title}: userId must be present"
            assert _norm_text(item.get("userName")), f"{title}: userName must be populated"

        _record_metrics(
            request,
            [
                f"CaseId: {case_id}",
                f"Title: {title}",
                "Validation: employee fields present and mapped to joined employee/users tables.",
            ],
        )
        return

    if case_id == "17775525":
        body = _payload(include_multi_tenant_users=True)
        response = _post_users_employees(api_context, body)
        expected = _expected_rows(db, {}, True)
        _assert_response_matches_db(
            title=title,
            payload=body,
            response=response,
            expected_rows=expected,
            include_multi_tenant_users=True,
            enforce_total_records=False,
        )

        items = _api_items(response.json())
        cross_rows = [row for row in items if row.get("employeeId") is None]
        assert cross_rows, f"{title}: expected at least one cross-tenant user row with employeeId=null"
        for row in cross_rows:
            assert row.get("userId") is not None, f"{title}: cross-tenant row missing userId"
            assert _norm_text(row.get("userName")), f"{title}: cross-tenant row missing userName"
            assert _norm_text(row.get("firstName")), f"{title}: cross-tenant row missing firstName"
            assert _norm_text(row.get("lastName")), f"{title}: cross-tenant row missing lastName"

        _record_metrics(
            request,
            [
                f"CaseId: {case_id}",
                f"Title: {title}",
                f"Cross-tenant rows in API response: {len(cross_rows)}",
            ],
        )
        return

    if case_id == "17775526":
        body = _payload(include_multi_tenant_users=False)
        response = _post_users_employees(api_context, body)
        expected = _expected_rows(db, {}, False)
        _assert_response_matches_db(
            title=title,
            payload=body,
            response=response,
            expected_rows=expected,
            include_multi_tenant_users=False,
            enforce_total_records=False,
        )

        inactive_count = int(
            db.fetch_scalar("SELECT COUNT(*) FROM cimpl.Employee WHERE EmployeeStatusId <> 1") or 0
        )
        assert inactive_count >= 0, f"{title}: inactive employee count query failed"

        _record_metrics(
            request,
            [
                f"CaseId: {case_id}",
                f"Title: {title}",
                f"Inactive employees in DB (not expected in result): {inactive_count}",
                f"API returned employee rows: {len(_api_items(response.json()))}",
            ],
        )
        return

    if case_id == "17775527":
        body = _payload(include_multi_tenant_users=True)
        response = _post_users_employees(api_context, body)
        expected = _expected_rows(db, {}, True)
        _assert_response_matches_db(
            title=title,
            payload=body,
            response=response,
            expected_rows=expected,
            include_multi_tenant_users=True,
            enforce_total_records=False,
        )

        inactive_cross = int(
            db.fetch_scalar("SELECT COUNT(*) FROM usr.Users WHERE UserTypeId = 2 AND StatusId <> 1") or 0
        )
        assert inactive_cross >= 0, f"{title}: inactive multi-tenant user count query failed"

        _record_metrics(
            request,
            [
                f"CaseId: {case_id}",
                f"Title: {title}",
                f"Inactive cross-tenant users in DB (not expected in result): {inactive_cross}",
                f"API returned total rows: {len(_api_items(response.json()))}",
            ],
        )
        return

    if case_id == "17775528":
        body = _payload(include_multi_tenant_users=True)
        response = _post_users_employees(api_context, body)
        assert response.status == 200, f"{title}: expected 200, got {response.status}. Response={response.text()}"
        items = _api_items(response.json())

        user_ids = [int(item["userId"]) for item in items if item.get("userId") is not None]
        assert len(user_ids) == len(set(user_ids)), f"{title}: duplicate userId values found in API response"

        expected = _expected_rows(db, {}, True)
        _assert_response_matches_db(
            title=title,
            payload=body,
            response=response,
            expected_rows=expected,
            include_multi_tenant_users=True,
            enforce_total_records=False,
        )

        _record_metrics(
            request,
            [
                f"CaseId: {case_id}",
                f"Title: {title}",
                f"API unique user count: {len(set(user_ids))}",
                f"API total row count: {len(user_ids)}",
            ],
        )
        return

    if case_id == "17775529":
        body = _payload(include_multi_tenant_users=None)
        body["includeMultiTenantUsers"] = "true"
        response = _post_users_employees(api_context, body)
        assert response.status == 400, f"{title}: expected 400, got {response.status}. Response={response.text()}"

        _record_metrics(
            request,
            [
                f"CaseId: {case_id}",
                f"Title: {title}",
                f"Request payload: {json.dumps(body, ensure_ascii=True)}",
                f"Response status: {response.status}",
                f"Response body: {response.text()}",
            ],
        )
        return

    if case_id == "17775530":
        body = _payload(page_index=-1, page_size=100, include_multi_tenant_users=False)
        response = _post_users_employees(api_context, body)
        assert response.status == 400, f"{title}: expected 400, got {response.status}. Response={response.text()}"
        _record_metrics(request, [f"CaseId: {case_id}", f"Title: {title}", f"Response: {response.text()}"])
        return

    if case_id == "17775531":
        body = _payload(page_index=0, page_size=-1, include_multi_tenant_users=False)
        response = _post_users_employees(api_context, body)
        assert response.status == 400, f"{title}: expected 400, got {response.status}. Response={response.text()}"
        _record_metrics(request, [f"CaseId: {case_id}", f"Title: {title}", f"Response: {response.text()}"])
        return

    if case_id == "17775532":
        body = _payload(page_index=0, page_size=0, include_multi_tenant_users=False)
        response = _post_users_employees(api_context, body)
        assert response.status in {200, 400}, (
            f"{title}: expected 200 or 400 for pageSize=0 handling, got {response.status}. "
            f"Response={response.text()}"
        )
        if response.status == 200:
            payload = response.json()
            assert int(payload.get("pagination", {}).get("size", -1)) == 0, (
                f"{title}: expected pagination.size=0 when API accepts pageSize=0"
            )
            assert len(_api_items(payload)) == 0, f"{title}: expected empty item list when pagination size is 0"
        _record_metrics(request, [f"CaseId: {case_id}", f"Title: {title}", f"Response status: {response.status}"])
        return

    if case_id == "17775533":
        body = _payload(email="not-an-email", include_multi_tenant_users=False)
        response = _post_users_employees(api_context, body)
        assert response.status in {200, 400}, (
            f"{title}: expected 200 or 400 for invalid email filter handling, got {response.status}. "
            f"Response={response.text()}"
        )
        if response.status == 200:
            expected = _expected_rows(db, {"email": "not-an-email"}, False)
            _assert_response_matches_db(
                title=title,
                payload=body,
                response=response,
                expected_rows=expected,
                include_multi_tenant_users=False,
                enforce_total_records=False,
            )
        _record_metrics(request, [f"CaseId: {case_id}", f"Title: {title}", f"Response status: {response.status}"])
        return

    if case_id == "17775534":
        body = _payload(first_name="__NO_FIRST_NAME_MATCH__", include_multi_tenant_users=False)
        response = _post_users_employees(api_context, body)
        expected = _expected_rows(db, {"firstName": "__NO_FIRST_NAME_MATCH__"}, False)
        _assert_response_matches_db(
            title=title,
            payload=body,
            response=response,
            expected_rows=expected,
            include_multi_tenant_users=False,
            enforce_total_records=False,
        )
        assert len(expected) == 0, f"{title}: expected DB count 0, got {len(expected)}"
        _record_metrics(request, [f"CaseId: {case_id}", f"Title: {title}", "Validation: API and DB both returned zero rows."])
        return

    if case_id == "17775535":
        body = _payload(email="__NO_EMAIL_MATCH__", include_multi_tenant_users=False)
        response = _post_users_employees(api_context, body)
        expected = _expected_rows(db, {"email": "__NO_EMAIL_MATCH__"}, False)
        _assert_response_matches_db(
            title=title,
            payload=body,
            response=response,
            expected_rows=expected,
            include_multi_tenant_users=False,
            enforce_total_records=False,
        )
        assert len(expected) == 0, f"{title}: expected DB count 0, got {len(expected)}"
        _record_metrics(request, [f"CaseId: {case_id}", f"Title: {title}", "Validation: API and DB both returned zero rows."])
        return

    if case_id == "17775536":
        body = _payload(include_multi_tenant_users=False)
        body["unsupportedField"] = "unexpected"
        response = _post_users_employees(api_context, body)
        assert response.status in {200, 400}, (
            f"{title}: expected 200 or 400 when unsupported field supplied, got {response.status}. "
            f"Response={response.text()}"
        )

        if response.status == 200:
            expected = _expected_rows(db, {}, False)
            _assert_response_matches_db(
                title=title,
                payload=body,
                response=response,
                expected_rows=expected,
                include_multi_tenant_users=False,
                enforce_total_records=False,
            )

        _record_metrics(
            request,
            [
                f"CaseId: {case_id}",
                f"Title: {title}",
                f"Request payload: {json.dumps(body, ensure_ascii=True)}",
                f"Response status: {response.status}",
                f"Response body: {_safe_json(response) if response.status == 200 else response.text()}",
            ],
        )
        return

    if case_id == "17775537":
        base_rows = _expected_rows(db, {}, False)
        first_name = _find_value_with_count(base_rows, "firstName", target="single")
        assert first_name is not None, f"{title}: no firstName found that returns one active employee"

        body = _payload(first_name=first_name, include_multi_tenant_users=False)
        response = _post_users_employees(api_context, body)
        expected = _expected_rows(db, {"firstName": first_name}, False)
        _assert_response_matches_db(
            title=title,
            payload=body,
            response=response,
            expected_rows=expected,
            include_multi_tenant_users=False,
            enforce_total_records=False,
        )
        assert len(expected) == 1, f"{title}: expected exactly one row, got {len(expected)}"

        _record_metrics(
            request,
            [
                f"CaseId: {case_id}",
                f"Title: {title}",
                f"Selected firstName dataset value: {first_name}",
                "Validation: exactly one record returned and matched DB.",
            ],
        )
        return

    if case_id == "17775538":
        body = _payload(first_name="__NO_ACTIVE_EMPLOYEE__", include_multi_tenant_users=False)
        response = _post_users_employees(api_context, body)
        expected = _expected_rows(db, {"firstName": "__NO_ACTIVE_EMPLOYEE__"}, False)
        _assert_response_matches_db(
            title=title,
            payload=body,
            response=response,
            expected_rows=expected,
            include_multi_tenant_users=False,
            enforce_total_records=False,
        )
        assert len(expected) == 0, f"{title}: expected zero rows, got {len(expected)}"
        _record_metrics(request, [f"CaseId: {case_id}", f"Title: {title}", "Validation: no active employees for filter criteria."])
        return

    if case_id == "17775539":
        body = _payload(page_index=0, page_size=1000, include_multi_tenant_users=False)
        response = _post_users_employees(api_context, body)
        expected = _expected_rows(db, {}, False)

        assert response.status in {200, 400}, (
            f"{title}: expected 200 or validation error for high pageSize, got {response.status}. Response={response.text()}"
        )

        if response.status == 200:
            _assert_response_matches_db(
                title=title,
                payload=body,
                response=response,
                expected_rows=expected,
                include_multi_tenant_users=False,
                enforce_total_records=False,
            )

        _record_metrics(request, [f"CaseId: {case_id}", f"Title: {title}", f"Response status: {response.status}"])
        return

    if case_id == "17775540":
        baseline_response = _post_users_employees(
            api_context,
            _payload(page_index=0, page_size=10, include_multi_tenant_users=False),
        )
        assert baseline_response.status == 200, (
            f"{title}: baseline request failed with {baseline_response.status}. "
            f"Response={baseline_response.text()}"
        )
        total = int(baseline_response.json().get("pagination", {}).get("totalRecords", 0))
        body = _payload(page_index=max(total, 1000), page_size=10, include_multi_tenant_users=False)
        response = _post_users_employees(api_context, body)
        assert response.status == 200, f"{title}: expected 200, got {response.status}. Response={response.text()}"

        payload = response.json()
        items = _api_items(payload)
        assert len(items) == 0, f"{title}: expected empty page for high pageIndex, got {len(items)} rows"
        assert int(payload.get("pagination", {}).get("totalRecords", -1)) == total, f"{title}: totalRecords changed unexpectedly"

        _record_metrics(
            request,
            [
                f"CaseId: {case_id}",
                f"Title: {title}",
                f"Request payload: {json.dumps(body, ensure_ascii=True)}",
                f"Total employee records: {total}",
                "Validation: beyond-page request returned empty items without error.",
            ],
        )
        return

    if case_id == "17775541":
        baseline = _api_baseline_rows(api_context, False)
        special = next(
            (item for item in baseline if item.get("firstName") and any(not ch.isalnum() and ch != " " for ch in str(item["firstName"]))),
            None,
        )
        if not special:
            pytest.skip(f"{title}: no active employee firstName with special characters found in DB")

        first_name = str(special.get("firstName"))
        body = _payload(first_name=first_name, include_multi_tenant_users=False)
        response = _post_users_employees(api_context, body)
        expected = _expected_rows(db, {"firstName": first_name}, False)
        _assert_response_matches_db(
            title=title,
            payload=body,
            response=response,
            expected_rows=expected,
            include_multi_tenant_users=False,
            enforce_total_records=False,
        )
        _record_metrics(request, [f"CaseId: {case_id}", f"Title: {title}", f"Filter value: {first_name}"])
        return

    if case_id == "17775542":
        candidate = db.fetch_one(
            """
            SELECT TOP 1 e.FirstName AS value
            FROM cimpl.Employee e
            WHERE e.EmployeeStatusId = 1
              AND e.FirstName IS NOT NULL
              AND (e.FirstName LIKE ' %' OR e.FirstName LIKE '% ')
            """
        )
        if not candidate:
            pytest.skip(f"{title}: no active employee firstName with leading/trailing spaces found in DB")

        raw_value = str(candidate["value"])
        body = _payload(first_name=raw_value.strip(), include_multi_tenant_users=False)
        response = _post_users_employees(api_context, body)
        expected = _expected_rows(db, {"firstName": raw_value.strip()}, False)
        _assert_response_matches_db(
            title=title,
            payload=body,
            response=response,
            expected_rows=expected,
            include_multi_tenant_users=False,
            enforce_total_records=False,
        )
        _record_metrics(request, [f"CaseId: {case_id}", f"Title: {title}", f"DB raw value: {raw_value}"])
        return

    if case_id == "17775543":
        row = next((item for item in _api_baseline_rows(api_context, False) if item.get("firstName")), None)
        assert row and row.get("firstName"), f"{title}: no active employee firstName found"

        first_name = str(row["firstName"]).strip()
        mixed_case = first_name.swapcase()
        body = _payload(first_name=mixed_case, include_multi_tenant_users=False)
        response = _post_users_employees(api_context, body)
        expected = _expected_rows(db, {"firstName": mixed_case}, False)
        _assert_response_matches_db(
            title=title,
            payload=body,
            response=response,
            expected_rows=expected,
            include_multi_tenant_users=False,
            enforce_total_records=False,
        )
        _record_metrics(
            request,
            [
                f"CaseId: {case_id}",
                f"Title: {title}",
                f"Original DB firstName: {first_name}",
                f"Case-swapped filter value: {mixed_case}",
            ],
        )
        return

    if case_id == "17775544":
        baseline = _api_baseline_rows(api_context, False)
        buckets: dict[tuple[str, str], int] = defaultdict(int)
        originals: dict[tuple[str, str], tuple[str, str]] = {}
        for item in baseline:
            first_name = str(item.get("firstName") or "").strip()
            last_name = str(item.get("lastName") or "").strip()
            if not first_name or not last_name:
                continue
            key = (first_name.lower(), last_name.lower())
            buckets[key] += 1
            originals.setdefault(key, (first_name, last_name))

        selected = None
        for key, count in buckets.items():
            if count > 1:
                selected = originals[key]
                break

        if not selected:
            pytest.skip(f"{title}: no active employee duplicate firstName/lastName pair found")

        filters = {"firstName": selected[0], "lastName": selected[1]}
        body = _payload(
            first_name=filters["firstName"],
            last_name=filters["lastName"],
            include_multi_tenant_users=False,
        )
        response = _post_users_employees(api_context, body)
        expected = _expected_rows(db, filters, False)
        _assert_response_matches_db(
            title=title,
            payload=body,
            response=response,
            expected_rows=expected,
            include_multi_tenant_users=False,
            enforce_total_records=False,
        )
        assert len(_api_items(response.json())) > 1, f"{title}: expected multiple matched rows in API response"
        _record_metrics(request, [f"CaseId: {case_id}", f"Title: {title}", f"Filters: {filters}"])
        return

    if case_id == "17775545":
        body = _payload(first_name=None, last_name=None, email=None, include_multi_tenant_users=False)
        response = _post_users_employees(api_context, body)
        expected = _expected_rows(db, {}, False)
        _assert_response_matches_db(
            title=title,
            payload=body,
            response=response,
            expected_rows=expected,
            include_multi_tenant_users=False,
            enforce_total_records=False,
        )
        _record_metrics(request, [f"CaseId: {case_id}", f"Title: {title}", "Validation: null optional filters return full eligible employee set."])
        return

    if case_id == "17775546":
        body = _payload(include_multi_tenant_users=False)
        response = _post_without_auth(body)
        assert response.status_code == 401, (
            f"{title}: expected 401 without auth token, got {response.status_code}. Response={response.text}"
        )
        _record_metrics(request, [f"CaseId: {case_id}", f"Title: {title}", f"Response status: {response.status_code}"])
        return

    if case_id == "17775547":
        body = _payload(include_multi_tenant_users=False)
        response = _post_with_bearer(body, "invalid-expired-token")
        assert response.status_code == 401, (
            f"{title}: expected 401 for invalid token, got {response.status_code}. Response={response.text}"
        )
        _record_metrics(request, [f"CaseId: {case_id}", f"Title: {title}", f"Response status: {response.status_code}"])
        return

    if case_id == "17775548":
        bearer = os.getenv("API_NO_PERMISSION_TOKEN", "").strip()
        if not bearer:
            pytest.skip(
                f"{title}: API_NO_PERMISSION_TOKEN not configured. Provide a token for a user without permission to validate 403."
            )

        body = _payload(include_multi_tenant_users=False)
        response = _post_with_bearer(body, bearer)
        assert response.status_code == 403, (
            f"{title}: expected 403 for no-permission user token, got {response.status_code}. Response={response.text}"
        )
        _record_metrics(request, [f"CaseId: {case_id}", f"Title: {title}", f"Response status: {response.status_code}"])
        return

    if case_id == "17775549":
        body = _payload(first_name="' OR 1=1 --", include_multi_tenant_users=False)
        response = _post_users_employees(api_context, body)
        assert response.status in {200, 400}, (
            f"{title}: expected 200 or 400 for SQLi payload handling, got {response.status}. Response={response.text()}"
        )
        if response.status == 200:
            expected = _expected_rows(db, {"firstName": "' OR 1=1 --"}, False)
            _assert_response_matches_db(
                title=title,
                payload=body,
                response=response,
                expected_rows=expected,
                include_multi_tenant_users=False,
                enforce_total_records=False,
            )
        _record_metrics(request, [f"CaseId: {case_id}", f"Title: {title}", f"Response status: {response.status}"])
        return

    if case_id == "17775550":
        body = _payload(email="' OR 1=1 --", include_multi_tenant_users=False)
        response = _post_users_employees(api_context, body)
        assert response.status in {200, 400}, (
            f"{title}: expected 200 or 400 for SQLi payload handling, got {response.status}. Response={response.text()}"
        )
        if response.status == 200:
            expected = _expected_rows(db, {"email": "' OR 1=1 --"}, False)
            _assert_response_matches_db(
                title=title,
                payload=body,
                response=response,
                expected_rows=expected,
                include_multi_tenant_users=False,
                enforce_total_records=False,
            )
        _record_metrics(request, [f"CaseId: {case_id}", f"Title: {title}", f"Response status: {response.status}"])
        return

    if case_id == "17775551":
        body = _payload(first_name="<script>alert(1)</script>", include_multi_tenant_users=False)
        response = _post_users_employees(api_context, body)
        assert response.status in {200, 400}, (
            f"{title}: expected 200 or 400 for XSS payload handling, got {response.status}. Response={response.text()}"
        )
        if response.status == 200:
            expected = _expected_rows(db, {"firstName": "<script>alert(1)</script>"}, False)
            _assert_response_matches_db(
                title=title,
                payload=body,
                response=response,
                expected_rows=expected,
                include_multi_tenant_users=False,
                enforce_total_records=False,
            )

            # Ensure script payload does not leak in returned names as executable content.
            for item in _api_items(response.json()):
                assert "<script>" not in _norm_text(item.get("firstName")), (
                    f"{title}: script markup should not appear unsanitized in firstName"
                )

        _record_metrics(request, [f"CaseId: {case_id}", f"Title: {title}", f"Response status: {response.status}"])
        return

    if case_id == "17775552":
        long_value = "A" * 5000
        body = _payload(first_name=long_value, include_multi_tenant_users=False)
        response = _post_users_employees(api_context, body)
        assert response.status in {200, 400}, (
            f"{title}: expected 200 or 400 for long input handling, got {response.status}. Response={response.text()}"
        )
        if response.status == 200:
            expected = _expected_rows(db, {"firstName": long_value}, False)
            _assert_response_matches_db(
                title=title,
                payload=body,
                response=response,
                expected_rows=expected,
                include_multi_tenant_users=False,
                enforce_total_records=False,
            )
        _record_metrics(request, [f"CaseId: {case_id}", f"Title: {title}", f"Response status: {response.status}"])
        return

    if case_id == "17775553":
        body = _payload(include_multi_tenant_users=True)
        response = _post_users_employees(api_context, body)
        expected = _expected_rows(db, {}, True)
        _assert_response_matches_db(
            title=title,
            payload=body,
            response=response,
            expected_rows=expected,
            include_multi_tenant_users=True,
            enforce_total_records=False,
        )

        db_user_ids = {int(row["userId"]) for row in expected if row.get("userId") is not None}
        api_user_ids = {int(row["userId"]) for row in _api_items(response.json()) if row.get("userId") is not None}
        assert api_user_ids.issubset(db_user_ids), f"{title}: API returned userIds not in DB tenant-eligible set"

        _record_metrics(
            request,
            [
                f"CaseId: {case_id}",
                f"Title: {title}",
                f"Tenant-scoped DB user count: {len(db_user_ids)}",
                f"API user count: {len(api_user_ids)}",
            ],
        )
        return

    if case_id == "17775554":
        body = _payload(include_multi_tenant_users=True)
        response = _post_users_employees(api_context, body)
        expected = _expected_rows(db, {}, True)
        _assert_response_matches_db(
            title=title,
            payload=body,
            response=response,
            expected_rows=expected,
            include_multi_tenant_users=True,
            enforce_total_records=False,
        )

        items = _api_items(response.json())
        allowed_keys = {"firstName", "lastName", "email", "employeeId", "userId", "userName"}
        leaked_keys = []
        for item in items:
            extra = set(item.keys()) - allowed_keys
            if extra:
                leaked_keys.append(sorted(extra))

        assert not leaked_keys, f"{title}: unexpected sensitive/extra fields returned: {leaked_keys[:20]}"

        _record_metrics(
            request,
            [
                f"CaseId: {case_id}",
                f"Title: {title}",
                f"Allowed keys enforced: {sorted(allowed_keys)}",
                f"Rows validated: {len(items)}",
            ],
        )
        return

    raise AssertionError(f"No case handler implemented for TestRail case {case_id}")


class TestPostUsersEmployees:

    @pytest.mark.parametrize("case", _params())
    def test_post_users_employees_cases(self, case, api_context, db, request):
        _run_case(case, api_context, db, request)
