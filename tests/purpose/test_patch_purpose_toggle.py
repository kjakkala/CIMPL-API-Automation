"""Data-driven tests for PATCH /v1/purpose/{purposeId}/toggle with DB validation."""

from __future__ import annotations

import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import pytest
import requests

from utils.post_api_db_scripts import PostApiDbScripts


@dataclass(frozen=True)
class PurposeToggleCase:
    case_id: str
    title: str
    expected: str


CASE_MATRIX: list[PurposeToggleCase] = [
    PurposeToggleCase("17777284", "Toggle Active -> Inactive", "Status 200/201. Active becomes false."),
    PurposeToggleCase("17777285", "Toggle Inactive -> Active", "Status 200/201. Active becomes true."),
    PurposeToggleCase("17777286", "Verify double toggle returns original state", "Status 200 for both requests. Final state equals original state."),
    PurposeToggleCase("17777287", "Validate purposeId = 0", "Status 400 Bad Request. Validation error returned."),
    PurposeToggleCase("17777288", "Validate negative purposeId", "Status 400 Bad Request. Validation error returned."),
    PurposeToggleCase("17777289", "Validate non-numeric purposeId", "Status 400 or framework validation error."),
    PurposeToggleCase("17777290", "Validate decimal purposeId", "Status 400 Bad Request."),
    PurposeToggleCase("17777291", "Purpose does not exist", "Appropriate error response returned (400/404 as implemented)."),
    PurposeToggleCase("17777292", "Missing purposeId", "Endpoint validation failure."),
    PurposeToggleCase("17777293", "Special characters in purposeId", "Validation error returned."),
    PurposeToggleCase("17777294", "Verify response schema", "Response matches Generic POST response schema"),
    PurposeToggleCase("17777295", "Verify database update", "Database Active value updated correctly"),
    PurposeToggleCase("17777296", "Concurrent toggle requests", "No data inconsistency. Final state predictable."),
    PurposeToggleCase("17777297", "Authorization validation", "401 Unauthorized (if API secured)."),
    PurposeToggleCase("17777298", "Invalid authorization token", "401/403 returned."),
    PurposeToggleCase("17777299", "Verify response time", "Response within API SLA."),
    PurposeToggleCase("17777300", "Idempotency validation", "Each call flips state; endpoint is not idempotent."),
    PurposeToggleCase("17777301", "Large integer purposeId", "Proper validation or entity not found response."),
]


CASE_PARAMS = [
    pytest.param(
        case,
        id=f"C{case.case_id}",
        marks=[
            pytest.mark.swagger_section("Purpose"),
            pytest.mark.swagger_operation("PATCH /v1/purpose/{purposeId}/toggle"),
            pytest.mark.testrail_case(case.case_id),
            pytest.mark.dashboard_title(case.title),
        ],
    )
    for case in CASE_MATRIX
]


def _record_dashboard_metrics(request, lines: list[str]):
    request.node.user_properties.append(("dashboard_metrics", "\n".join(lines)))


def _safe_json(response) -> dict:
    try:
        payload = response.json()
        return payload if isinstance(payload, dict) else {"raw": payload}
    except Exception:  # noqa: BLE001
        return {}


def _extract_response_message(response, payload: dict) -> str:
    for key in ("Message", "message", "error", "error_description", "ErrorCode", "errorCode"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    text = response.text() if hasattr(response, "text") and callable(response.text) else getattr(response, "text", "")
    return str(text).strip()[:500]


def _append_response_details(metrics: list[str], response):
    payload = _safe_json(response)
    message = _extract_response_message(response, payload)
    metrics.append(f"Validation message: {message or 'n/a'}")
    if payload:
        metrics.append(f"Response payload: {json.dumps(payload, ensure_ascii=True)}")


def _patch_toggle(api_context, purpose_id):
    return api_context.patch(f"purpose/{purpose_id}/toggle", headers={"accept": "text/plain"})


def _post_save(api_context, body: dict):
    return api_context.post(
        "purpose/save",
        data=json.dumps(body, ensure_ascii=True),
        headers={"Content-Type": "application/json", "accept": "text/plain"},
    )


def _fetch_purpose(db, purpose_id: int):
    return db.fetch_one(
        "SELECT PurposeId AS purposeId, Name AS name, Active AS active FROM cimpl.Purpose WHERE PurposeId = ?",
        (int(purpose_id),),
    )


def _create_seed_purpose(api_context, db, *, active: bool, prefix: str = "ToggleSeed") -> dict:
    name = f"{prefix}{uuid.uuid4().hex[:10]}"
    response = _post_save(api_context, {"name": name, "active": active})
    assert response.status == 200, f"Failed to seed purpose. status={response.status} body={response.text()}"
    row = db.fetch_one(
        "SELECT TOP 1 PurposeId AS purposeId, Name AS name, Active AS active FROM cimpl.Purpose WHERE Name = ? ORDER BY PurposeId DESC",
        (name,),
    )
    assert row, f"Seed purpose not found in DB for {name}"
    return row


def _toggle_without_auth(path: str, *, bad_token: bool = False):
    base = os.environ["BASE_URL"].rstrip("/")
    url = f"{base}/{path.lstrip('/')}"
    headers = {"accept": "text/plain"}
    if bad_token:
        headers["Authorization"] = "Bearer invalid-token"
    return requests.patch(url, headers=headers, timeout=60)


def _toggle_with_fresh_token(path: str):
    token_url = os.environ["TOKEN_URL"]
    grant_type = os.getenv("GRANT_TYPE", "password")
    payload = {
        "grant_type": grant_type,
        "client_id": os.environ["CLIENT_ID"],
        "client_secret": os.environ["CLIENT_SECRET"],
    }
    if grant_type == "password":
        payload["username"] = os.environ["API_USERNAME"]
        payload["password"] = os.environ["API_PASSWORD"]

    token_resp = requests.post(token_url, data=payload, timeout=30)
    assert token_resp.ok, f"Token request failed: {token_resp.status_code} {token_resp.text}"
    token = token_resp.json().get("access_token")
    assert token, "Token response missing access_token"

    base = os.environ["BASE_URL"].rstrip("/")
    url = f"{base}/{path.lstrip('/')}"
    headers = {"Authorization": f"Bearer {token}", "accept": "text/plain"}
    return requests.patch(url, headers=headers, timeout=60)


def _assert_flipped(db, purpose_id: int, expected_active: bool, title: str):
    after = _fetch_purpose(db, purpose_id)
    assert after is not None, f"{title}: purpose row missing after toggle for PurposeId={purpose_id}"
    assert bool(after["active"]) == expected_active, (
        f"{title}: expected Active={expected_active}, actual={after['active']} for PurposeId={purpose_id}"
    )
    return after


def _run_case(case: PurposeToggleCase, api_context, db, request):
    title = case.title.lower()
    db_scripts = PostApiDbScripts(db)
    metrics = [
        f"CaseId: {case.case_id}",
        f"Title: {case.title}",
        f"Expected: {case.expected}",
        f"DB target: {os.getenv('DB_SERVER', '')} / {os.getenv('DB_NAME', '')}",
    ]

    if "authorization validation" in title:
        resp = _toggle_without_auth("purpose/1/toggle")
        assert resp.status_code in {401, 403}
        metrics.append(f"Request: PATCH /v1/purpose/1/toggle without token")
        metrics.append(f"Response status: {resp.status_code}")
        _append_response_details(metrics, resp)
        _record_dashboard_metrics(request, metrics)
        return

    if "invalid authorization token" in title:
        resp = _toggle_without_auth("purpose/1/toggle", bad_token=True)
        assert resp.status_code in {401, 403}
        metrics.append(f"Request: PATCH /v1/purpose/1/toggle with invalid token")
        metrics.append(f"Response status: {resp.status_code}")
        _append_response_details(metrics, resp)
        _record_dashboard_metrics(request, metrics)
        return

    if "purposeid = 0" in title:
        resp = _patch_toggle(api_context, 0)
        assert resp.status in {400, 404}
        metrics.append("Request: PATCH /v1/purpose/0/toggle")
        metrics.append(f"Response status: {resp.status}")
        _append_response_details(metrics, resp)
        _record_dashboard_metrics(request, metrics)
        return

    if "negative purposeid" in title:
        resp = _patch_toggle(api_context, -1)
        assert resp.status in {400, 404}
        metrics.append("Request: PATCH /v1/purpose/-1/toggle")
        metrics.append(f"Response status: {resp.status}")
        _append_response_details(metrics, resp)
        _record_dashboard_metrics(request, metrics)
        return

    if "non-numeric purposeid" in title:
        resp = _patch_toggle(api_context, "abc")
        assert resp.status in {400, 404}
        metrics.append("Request: PATCH /v1/purpose/abc/toggle")
        metrics.append(f"Response status: {resp.status}")
        _append_response_details(metrics, resp)
        _record_dashboard_metrics(request, metrics)
        return

    if "decimal purposeid" in title:
        resp = _patch_toggle(api_context, "10.5")
        assert resp.status in {400, 404}
        metrics.append("Request: PATCH /v1/purpose/10.5/toggle")
        metrics.append(f"Response status: {resp.status}")
        _append_response_details(metrics, resp)
        _record_dashboard_metrics(request, metrics)
        return

    if "special characters in purposeid" in title:
        resp = _patch_toggle(api_context, "@#")
        assert resp.status in {400, 404, 405}
        metrics.append("Request: PATCH /v1/purpose/@#/toggle")
        metrics.append(f"Response status: {resp.status}")
        _append_response_details(metrics, resp)
        _record_dashboard_metrics(request, metrics)
        return

    if "missing purposeid" in title:
        resp = api_context.patch("purpose//toggle", headers={"accept": "text/plain"})
        assert resp.status in {400, 404, 405}
        metrics.append("Request: PATCH /v1/purpose//toggle")
        metrics.append(f"Response status: {resp.status}")
        _append_response_details(metrics, resp)
        _record_dashboard_metrics(request, metrics)
        return

    if "purpose does not exist" in title:
        max_id = db_scripts.max_id(table="cimpl.Purpose", id_column="PurposeId")
        missing_id = max_id + 999999
        resp = _patch_toggle(api_context, missing_id)
        assert resp.status in {400, 404}
        metrics.append(f"Request: PATCH /v1/purpose/{missing_id}/toggle")
        metrics.append(f"Response status: {resp.status}")
        _append_response_details(metrics, resp)
        _record_dashboard_metrics(request, metrics)
        return

    if "large integer purposeid" in title:
        huge_id = 2147483647
        resp = _patch_toggle(api_context, huge_id)
        assert resp.status in {400, 404}
        metrics.append(f"Request: PATCH /v1/purpose/{huge_id}/toggle")
        metrics.append(f"Response status: {resp.status}")
        _append_response_details(metrics, resp)
        _record_dashboard_metrics(request, metrics)
        return

    if "toggle active -> inactive" in title:
        seed = _create_seed_purpose(api_context, db, active=True, prefix="ToggleTrue")
        purpose_id = int(seed["purposeId"])
        before = _fetch_purpose(db, purpose_id)
        assert before and bool(before["active"]) is True

        resp = _patch_toggle(api_context, purpose_id)
        assert resp.status in {200, 201}
        after = _assert_flipped(db, purpose_id, False, case.title)

        metrics.append(f"Request: PATCH /v1/purpose/{purpose_id}/toggle")
        metrics.append(f"Response status: {resp.status}")
        metrics.append(f"DB validation: Active {bool(before['active'])} -> {bool(after['active'])}")
        _record_dashboard_metrics(request, metrics)
        return

    if "toggle inactive -> active" in title:
        seed = _create_seed_purpose(api_context, db, active=False, prefix="ToggleFalse")
        purpose_id = int(seed["purposeId"])
        before = _fetch_purpose(db, purpose_id)
        assert before and bool(before["active"]) is False

        resp = _patch_toggle(api_context, purpose_id)
        assert resp.status in {200, 201}
        after = _assert_flipped(db, purpose_id, True, case.title)

        metrics.append(f"Request: PATCH /v1/purpose/{purpose_id}/toggle")
        metrics.append(f"Response status: {resp.status}")
        metrics.append(f"DB validation: Active {bool(before['active'])} -> {bool(after['active'])}")
        _record_dashboard_metrics(request, metrics)
        return

    if "verify database update" in title:
        seed = _create_seed_purpose(api_context, db, active=True, prefix="ToggleDb")
        purpose_id = int(seed["purposeId"])

        before_row_count = db_scripts.count_rows(
            table="cimpl.Purpose",
            where_sql="PurposeId = ?",
            params=(purpose_id,),
        )
        before = _fetch_purpose(db, purpose_id)
        assert before is not None

        resp = _patch_toggle(api_context, purpose_id)
        assert resp.status in {200, 201}

        after_row_count = db_scripts.count_rows(
            table="cimpl.Purpose",
            where_sql="PurposeId = ?",
            params=(purpose_id,),
        )
        db_scripts.assert_count_delta(
            before=before_row_count,
            after=after_row_count,
            expected_delta=0,
            context=f"{case.title}: row count must remain stable on toggle",
        )

        after = _fetch_purpose(db, purpose_id)
        assert after is not None
        assert bool(after["active"]) != bool(before["active"]), (
            f"{case.title}: Active was not flipped. before={before['active']} after={after['active']}"
        )

        metrics.append(f"Request: PATCH /v1/purpose/{purpose_id}/toggle")
        metrics.append(f"Response status: {resp.status}")
        metrics.append(f"DB validation: row count stable ({before_row_count}); Active flipped.")
        _record_dashboard_metrics(request, metrics)
        return

    if "verify response schema" in title:
        seed = _create_seed_purpose(api_context, db, active=True, prefix="ToggleSchema")
        purpose_id = int(seed["purposeId"])
        resp = _patch_toggle(api_context, purpose_id)
        assert resp.status in {200, 201}
        payload = _safe_json(resp)

        assert payload or resp.text().strip(), (
            f"{case.title}: expected JSON payload or non-empty response text. status={resp.status}"
        )
        if payload:
            assert isinstance(payload, dict)
            assert any(k in payload for k in ("requestId", "message", "Message", "errorCode", "ErrorCode")), (
                f"{case.title}: payload missing expected generic envelope keys. payload={payload}"
            )

        metrics.append(f"Request: PATCH /v1/purpose/{purpose_id}/toggle")
        metrics.append(f"Response status: {resp.status}")
        metrics.append(f"Response payload keys: {sorted(payload.keys()) if payload else 'non-json-response'}")
        _record_dashboard_metrics(request, metrics)
        return

    if "verify response time" in title:
        seed = _create_seed_purpose(api_context, db, active=True, prefix="TogglePerf")
        purpose_id = int(seed["purposeId"])

        started = time.perf_counter()
        resp = _patch_toggle(api_context, purpose_id)
        elapsed_ms = (time.perf_counter() - started) * 1000

        assert resp.status in {200, 201}
        assert elapsed_ms < 5000, f"{case.title}: response time {elapsed_ms:.2f} ms exceeded 5000 ms target"

        metrics.append(f"Request: PATCH /v1/purpose/{purpose_id}/toggle")
        metrics.append(f"Response status: {resp.status}")
        metrics.append(f"Response time: {elapsed_ms:.2f} ms")
        _record_dashboard_metrics(request, metrics)
        return

    if "concurrent toggle requests" in title:
        seed = _create_seed_purpose(api_context, db, active=True, prefix="ToggleConcurrent")
        purpose_id = int(seed["purposeId"])
        start_active = bool(seed["active"])

        def _call_once():
            return _toggle_with_fresh_token(f"purpose/{purpose_id}/toggle").status_code

        with ThreadPoolExecutor(max_workers=4) as ex:
            statuses = list(ex.map(lambda _: _call_once(), range(4)))

        assert all(status in {200, 201} for status in statuses), (
            f"{case.title}: expected all 200/201 statuses, got {statuses}"
        )

        final_row = _fetch_purpose(db, purpose_id)
        assert final_row is not None
        expected_final = start_active if len(statuses) % 2 == 0 else (not start_active)
        assert bool(final_row["active"]) == expected_final, (
            f"{case.title}: unexpected final state after {len(statuses)} toggles. "
            f"Expected={expected_final}, Actual={bool(final_row['active'])}"
        )

        metrics.append(f"Request: PATCH /v1/purpose/{purpose_id}/toggle x{len(statuses)} concurrent")
        metrics.append(f"Statuses: {statuses}")
        metrics.append(f"DB final active: {bool(final_row['active'])}")
        _record_dashboard_metrics(request, metrics)
        return

    if "double toggle returns original state" in title or "idempotency validation" in title:
        seed = _create_seed_purpose(api_context, db, active=True, prefix="ToggleDouble")
        purpose_id = int(seed["purposeId"])
        original = bool(seed["active"])

        r1 = _patch_toggle(api_context, purpose_id)
        assert r1.status in {200, 201}
        mid = _fetch_purpose(db, purpose_id)
        assert mid is not None and bool(mid["active"]) != original

        r2 = _patch_toggle(api_context, purpose_id)
        assert r2.status in {200, 201}
        final = _fetch_purpose(db, purpose_id)
        assert final is not None and bool(final["active"]) == original

        metrics.append(f"Request: PATCH /v1/purpose/{purpose_id}/toggle twice")
        metrics.append(f"Statuses: first={r1.status}, second={r2.status}")
        metrics.append(f"DB states: original={original}, after1={bool(mid['active'])}, after2={bool(final['active'])}")
        _record_dashboard_metrics(request, metrics)
        return

    raise AssertionError(f"No case logic mapped for title: {case.title}")


class TestPatchPurposeToggle:

    @pytest.mark.parametrize("case", CASE_PARAMS)
    def test_patch_purpose_toggle_cases(self, case, api_context, db, request):
        _run_case(case, api_context, db, request)
