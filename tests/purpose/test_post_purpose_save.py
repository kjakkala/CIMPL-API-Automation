"""Data-driven tests for POST /purpose/save with DB validation and dashboard history."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from utils.post_api_db_scripts import PostApiDbScripts


@dataclass(frozen=True)
class PurposeCase:
    case_id: str
    title: str


CASE_MATRIX: list[PurposeCase] = [
    PurposeCase("17767903", "Create new Purpose with valid name and active=true"),
    PurposeCase("17767904", "Create new Purpose with valid name and active=false"),
    PurposeCase("17767905", "Create new Purpose with alphanumeric name"),
    PurposeCase("17767906", "Create new Purpose with numeric-only name"),
    PurposeCase("17767907", "Update existing active Purpose name"),
    PurposeCase("17767908", "Update existing inactive Purpose name"),
    PurposeCase("17767909", "Update existing active Purpose to inactive"),
    PurposeCase("17767910", "Update existing inactive Purpose to active"),
    PurposeCase("17767911", "Update existing Purpose without changing name"),
    PurposeCase("17767912", "Update existing Purpose changing name to another unique name"),
    PurposeCase("17767913", "Create Purpose with missing Name"),
    PurposeCase("17767914", "Create Purpose with null Name"),
    PurposeCase("17767915", "Create Purpose with empty Name"),
    PurposeCase("17767916", "Create Purpose with whitespace-only Name"),
    PurposeCase("17767917", "Create Purpose with missing Active"),
    PurposeCase("17767918", "Create Purpose with null Active"),
    PurposeCase("17767919", "Update Purpose with missing Active"),
    PurposeCase("17767920", "Update Purpose with missing Name"),
    PurposeCase("17767921", "Create Purpose with duplicate name"),
    PurposeCase("17767922", "Update Purpose with duplicate name"),
    PurposeCase("17767923", "Update existing Purpose keeping same name (should succeed)"),
    PurposeCase("17767924", "Create Purpose with duplicate name containing leading/trailing spaces"),
    PurposeCase("17767925", "Name contains special character (!)"),
    PurposeCase("17767926", "Name contains special character (@)"),
    PurposeCase("17767927", "Name contains underscore (_)"),
    PurposeCase("17767928", "Name contains hyphen (-)"),
    PurposeCase("17767929", "Name contains period (.)"),
    PurposeCase("17767930", "Name contains slash (/)"),
    PurposeCase("17767931", "Name contains unicode characters"),
    PurposeCase("17767932", "Name contains spaces between words"),
    PurposeCase("17767933", "Name contains leading spaces"),
    PurposeCase("17767934", "Name contains trailing spaces"),
    PurposeCase("17767935", "Update with valid PurposeId"),
    PurposeCase("17767936", "Update with non-existing PurposeId"),
    PurposeCase("17767937", "Update with null PurposeId"),
    PurposeCase("17767938", "Update with PurposeId = 0"),
    PurposeCase("17767939", "Update with negative PurposeId"),
    PurposeCase("17767940", "Update with invalid PurposeId datatype"),
    PurposeCase("17767941", "Active as string value"),
    PurposeCase("17767942", "Active as numeric value"),
    PurposeCase("17767943", "Name as numeric datatype"),
    PurposeCase("17767944", "Name as boolean datatype"),
    PurposeCase("17767945", "PurposeId as string datatype"),
    PurposeCase("17767946", "Verify 200 response for successful create"),
    PurposeCase("17767947", "Verify 200 response for successful update"),
    PurposeCase("17767948", "Verify 400 response for missing Name"),
    PurposeCase("17767949", "Verify 400 response for missing Active"),
    PurposeCase("17767950", "Verify 400 response for duplicate Name"),
    PurposeCase("17767951", "Verify 400 response for invalid Name format"),
    PurposeCase("17767952", "Verify correct error message for missing Name"),
    PurposeCase("17767953", "Verify correct error message for duplicate Name"),
    PurposeCase("17767954", "Verify correct error message for invalid Name format"),
    PurposeCase("17767955", "Verify created Purpose saved in database"),
    PurposeCase("17767956", "Verify updated Purpose saved in database"),
    PurposeCase("17767957", "Verify Active=true persisted correctly"),
    PurposeCase("17767958", "Verify Active=false persisted correctly"),
    PurposeCase("17767959", "Verify updated Name persisted correctly"),
    PurposeCase("17767960", "SQL injection string in Name"),
    PurposeCase("17767961", "Script tag in Name"),
    PurposeCase("17767962", "Very long Name exceeding max length"),
    PurposeCase("17767963", "Special character combination in Name"),
    PurposeCase("17767964", "Multiple simultaneous create requests with same Name"),
]


CASE_PARAMS = [
    pytest.param(
        case,
        id=f"C{case.case_id}",
        marks=[
            pytest.mark.swagger_section("Purpose"),
            pytest.mark.swagger_operation("POST /purpose/save"),
            pytest.mark.testrail_case(case.case_id),
            pytest.mark.dashboard_title(case.title),
        ],
    )
    for case in CASE_MATRIX
]


def _now_suffix() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")


def _unique_name(prefix: str = "AutoPurpose") -> str:
    return f"{prefix}{_now_suffix()}"


def _json_body(data: dict) -> str:
    return json.dumps(data, ensure_ascii=True)


def _safe_json(response) -> dict:
    try:
        payload = response.json()
        return payload if isinstance(payload, dict) else {"raw": payload}
    except Exception:  # noqa: BLE001
        return {}


def _post_save(api_context, body: dict):
    attempts = 2
    for attempt in range(1, attempts + 1):
        try:
            return api_context.post(
                "purpose/save",
                data=_json_body(body),
                headers={"Content-Type": "application/json", "accept": "text/plain"},
            )
        except PlaywrightTimeoutError:
            if attempt == attempts:
                raise
            time.sleep(1)


def _record_dashboard_metrics(request, lines: list[str]):
    request.node.user_properties.append(("dashboard_metrics", "\n".join(lines)))


def _get_existing_purpose(db, *, active: bool | None = None) -> dict | None:
    if active is None:
        query = """
            SELECT TOP 1 PurposeId AS purposeId, Name AS name, Active AS active
            FROM cimpl.Purpose
            ORDER BY PurposeId DESC
        """
        return db.fetch_one(query)

    query = """
        SELECT TOP 1 PurposeId AS purposeId, Name AS name, Active AS active
        FROM cimpl.Purpose
        WHERE Active = ?
        ORDER BY PurposeId DESC
    """
    return db.fetch_one(query, (1 if active else 0,))


def _get_two_distinct_purposes(db) -> tuple[dict, dict] | None:
    rows = db.fetch_all(
        """
        SELECT TOP 2 PurposeId AS purposeId, Name AS name, Active AS active
        FROM cimpl.Purpose
        ORDER BY PurposeId DESC
        """
    )
    if len(rows) < 2:
        return None
    return rows[0], rows[1]


def _fetch_by_name(db, name: str) -> dict | None:
    return db.fetch_one(
        """
        SELECT TOP 1 PurposeId AS purposeId, Name AS name, Active AS active
        FROM cimpl.Purpose
        WHERE Name = ?
        ORDER BY PurposeId DESC
        """,
        (name,),
    )


def _fetch_by_id(db, purpose_id: int) -> dict | None:
    return db.fetch_one(
        """
        SELECT PurposeId AS purposeId, Name AS name, Active AS active
        FROM cimpl.Purpose
        WHERE PurposeId = ?
        """,
        (purpose_id,),
    )


def _error_code(payload: dict) -> str:
    return str(payload.get("ErrorCode", "")).strip()


def _error_message(payload: dict, response_text: str) -> str:
    msg = payload.get("Message")
    if isinstance(msg, str) and msg.strip():
        return msg
    return response_text


def _assert_status(response, allowed_statuses: set[int], title: str):
    assert response.status in allowed_statuses, (
        f"{title}: expected status in {sorted(allowed_statuses)}, got {response.status}. "
        f"Response={response.text()}"
    )


def _assert_db_create(
    db,
    expected_name: str,
    expected_active: bool,
    title: str,
    db_scripts: PostApiDbScripts | None = None,
    before_name_count: int | None = None,
):
    row = _fetch_by_name(db, expected_name)
    assert row, f"{title}: created record not found in DB for Name='{expected_name}'"
    if db_scripts is not None and before_name_count is not None:
        after_name_count = db_scripts.count_rows(
            table="cimpl.Purpose",
            where_sql="Name = ?",
            params=(expected_name,),
        )
        db_scripts.assert_count_delta(
            before=before_name_count,
            after=after_name_count,
            expected_delta=1,
            context=f"{title}: created row count validation",
        )
    assert bool(row["active"]) == expected_active, (
        f"{title}: DB Active mismatch for Name='{expected_name}'. "
        f"Expected={expected_active}, Actual={row['active']}"
    )
    return row


def _assert_db_update(
    db,
    purpose_id: int,
    expected_name: str,
    expected_active: bool,
    title: str,
    db_scripts: PostApiDbScripts | None = None,
):
    row = _fetch_by_id(db, purpose_id)
    assert row, f"{title}: updated record not found in DB for PurposeId={purpose_id}"
    if db_scripts is not None:
        row_count = db_scripts.count_rows(
            table="cimpl.Purpose",
            where_sql="PurposeId = ?",
            params=(purpose_id,),
        )
        assert row_count == 1, (
            f"{title}: expected exactly one row for PurposeId={purpose_id}, got {row_count}"
        )
    assert row["name"] == expected_name, (
        f"{title}: DB Name mismatch for PurposeId={purpose_id}. "
        f"Expected='{expected_name}', Actual='{row['name']}'"
    )
    assert bool(row["active"]) == expected_active, (
        f"{title}: DB Active mismatch for PurposeId={purpose_id}. "
        f"Expected={expected_active}, Actual={row['active']}"
    )
    return row


def _db_snapshot(row: dict) -> str:
    return (
        f"DB row after API: PurposeId={row['purposeId']}, "
        f"Name='{row['name']}', Active={bool(row['active'])}"
    )


def _create_seed_purpose(api_context, db, *, active: bool, prefix: str, title: str) -> dict:
    seed_name = _unique_name(prefix)
    response = _post_save(api_context, {"name": seed_name, "active": active})
    assert response.status == 200, (
        f"{title}: failed to create seed purpose for update test. "
        f"Status={response.status}, Response={response.text()}"
    )
    row = _fetch_by_name(db, seed_name)
    assert row, f"{title}: seed purpose not found in DB for Name='{seed_name}'"
    return row


def _case_logic(case: PurposeCase, api_context, db, request):
    title = case.title
    title_l = title.lower()
    db_scripts = PostApiDbScripts(db)
    history_lines = [
        f"CaseId: {case.case_id}",
        f"Title: {title}",
        f"DB target: {os.getenv('DB_SERVER', '')} / {os.getenv('DB_NAME', '')}",
    ]

    def execute(body: dict, allowed_statuses: set[int], expected_error_contains: str | None = None):
        history_lines.append(f"Request body: {json.dumps(body, ensure_ascii=True)}")
        response = _post_save(api_context, body)
        payload = _safe_json(response)
        history_lines.append(f"Response status: {response.status}")
        if payload:
            history_lines.append(f"Response payload: {json.dumps(payload, ensure_ascii=True)}")
        else:
            history_lines.append(f"Response text: {response.text()}")
        _assert_status(response, allowed_statuses, title)
        if expected_error_contains:
            message = _error_message(payload, response.text())
            assert expected_error_contains.lower() in message.lower(), (
                f"{title}: expected error message containing '{expected_error_contains}', got '{message}'"
            )
        return response, payload

    if title_l == "multiple simultaneous create requests with same name":
        name = _unique_name("RacePurpose")
        body = {"name": name, "active": True}
        history_lines.append("Action: submit same create payload twice to validate uniqueness under contention")
        response_1, payload_1 = execute(body, {200, 400})
        response_2, payload_2 = execute(body, {200, 400})
        statuses = {response_1.status, response_2.status}
        assert 200 in statuses, f"{title}: expected at least one success, got statuses={statuses}"
        assert any(status != 200 for status in statuses), (
            f"{title}: expected one duplicate failure, got statuses={statuses}"
        )
        history_lines.append(
            f"Verification: call1={response_1.status} (code={_error_code(payload_1)}), "
            f"call2={response_2.status} (code={_error_code(payload_2)})"
        )
        _record_dashboard_metrics(request, history_lines)
        return

    if "update existing active purpose name" in title_l:
        row = _create_seed_purpose(
            api_context, db, active=True, prefix="SeedUpdActive", title=title
        )
        new_name = _unique_name("UpdActive")
        body = {"purposeId": int(row["purposeId"]), "name": new_name, "active": True}
        execute(body, {200})
        updated = _assert_db_update(db, int(row["purposeId"]), new_name, True, title)
        history_lines.append(f"DB validation: Purpose updated with new name. {_db_snapshot(updated)}")
        _record_dashboard_metrics(request, history_lines)
        return

    if "update existing inactive purpose name" in title_l:
        row = _create_seed_purpose(
            api_context, db, active=False, prefix="SeedUpdInactive", title=title
        )
        new_name = _unique_name("UpdInactive")
        body = {"purposeId": int(row["purposeId"]), "name": new_name, "active": False}
        execute(body, {200})
        updated = _assert_db_update(db, int(row["purposeId"]), new_name, False, title)
        history_lines.append(f"DB validation: inactive purpose name updated. {_db_snapshot(updated)}")
        _record_dashboard_metrics(request, history_lines)
        return

    if "update existing active purpose to inactive" in title_l:
        row = _create_seed_purpose(
            api_context, db, active=True, prefix="SeedActiveToInactive", title=title
        )
        body = {"purposeId": int(row["purposeId"]), "name": row["name"], "active": False}
        execute(body, {200})
        updated = _assert_db_update(db, int(row["purposeId"]), row["name"], False, title)
        history_lines.append(f"DB validation: active flag toggled to false. {_db_snapshot(updated)}")
        _record_dashboard_metrics(request, history_lines)
        return

    if "update existing inactive purpose to active" in title_l:
        row = _create_seed_purpose(
            api_context, db, active=False, prefix="SeedInactiveToActive", title=title
        )
        body = {"purposeId": int(row["purposeId"]), "name": row["name"], "active": True}
        execute(body, {200})
        updated = _assert_db_update(db, int(row["purposeId"]), row["name"], True, title)
        history_lines.append(f"DB validation: active flag toggled to true. {_db_snapshot(updated)}")
        _record_dashboard_metrics(request, history_lines)
        return

    if "update existing purpose without changing name" in title_l or "keeping same name" in title_l:
        row = _create_seed_purpose(
            api_context, db, active=True, prefix="SeedKeepName", title=title
        )
        body = {
            "purposeId": int(row["purposeId"]),
            "name": row["name"],
            "active": bool(row["active"]),
        }
        execute(body, {200})
        updated = _assert_db_update(
            db, int(row["purposeId"]), row["name"], bool(row["active"]), title
        )
        history_lines.append(f"DB validation: unchanged-name update succeeded. {_db_snapshot(updated)}")
        _record_dashboard_metrics(request, history_lines)
        return

    if "update existing purpose changing name to another unique name" in title_l:
        row = _create_seed_purpose(
            api_context, db, active=True, prefix="SeedUpdUnique", title=title
        )
        new_name = _unique_name("UpdUnique")
        body = {"purposeId": int(row["purposeId"]), "name": new_name, "active": bool(row["active"])}
        execute(body, {200})
        updated = _assert_db_update(db, int(row["purposeId"]), new_name, bool(row["active"]), title)
        history_lines.append(f"DB validation: renamed to unique value. {_db_snapshot(updated)}")
        _record_dashboard_metrics(request, history_lines)
        return

    if "update with valid purposeid" in title_l or "verify 200 response for successful update" in title_l:
        row = _create_seed_purpose(
            api_context, db, active=True, prefix="SeedValidUpdate", title=title
        )
        new_name = _unique_name("UpdValid")
        body = {"purposeId": int(row["purposeId"]), "name": new_name, "active": bool(row["active"])}
        execute(body, {200})
        updated = _assert_db_update(db, int(row["purposeId"]), new_name, bool(row["active"]), title)
        history_lines.append(f"DB validation: update with valid PurposeId persisted. {_db_snapshot(updated)}")
        _record_dashboard_metrics(request, history_lines)
        return

    if "update with non-existing purposeid" in title_l:
        max_id = db_scripts.max_id(table="cimpl.Purpose", id_column="PurposeId")
        body = {"purposeId": max_id + 100000, "name": _unique_name("MissingId"), "active": True}
        _, payload = execute(body, {404})
        history_lines.append(f"Verification: errorCode={_error_code(payload)}")
        _record_dashboard_metrics(request, history_lines)
        return

    if "update with null purposeid" in title_l:
        body = {"purposeId": None, "name": _unique_name("NullId"), "active": True}
        response, payload = execute(body, {200, 400, 404})
        if response.status != 200:
            message = _error_message(payload, "")
            assert "purpose" in message.lower(), (
                f"{title}: expected purpose-related validation message, got '{message}'"
            )
        history_lines.append(f"Verification: errorCode={_error_code(payload)}")
        _record_dashboard_metrics(request, history_lines)
        return

    if "update with purposeid = 0" in title_l:
        body = {"purposeId": 0, "name": _unique_name("ZeroId"), "active": True}
        response, payload = execute(body, {200, 400, 404})
        if response.status != 200:
            message = _error_message(payload, "")
            assert "purpose" in message.lower(), (
                f"{title}: expected purpose-related validation message, got '{message}'"
            )
        history_lines.append(f"Verification: errorCode={_error_code(payload)}")
        _record_dashboard_metrics(request, history_lines)
        return

    if "update with negative purposeid" in title_l:
        body = {"purposeId": -10, "name": _unique_name("NegId"), "active": True}
        _, payload = execute(body, {400, 404}, expected_error_contains="purpose")
        history_lines.append(f"Verification: errorCode={_error_code(payload)}")
        _record_dashboard_metrics(request, history_lines)
        return

    if "update with invalid purposeid datatype" in title_l or "purposeid as string datatype" in title_l:
        body = {"purposeId": "abc", "name": _unique_name("IdString"), "active": True}
        _, payload = execute(body, {400}, expected_error_contains="purpose")
        history_lines.append(f"Verification: errorCode={_error_code(payload)}")
        _record_dashboard_metrics(request, history_lines)
        return

    if "update purpose with duplicate name" in title_l:
        source = _create_seed_purpose(
            api_context, db, active=True, prefix="SeedDupSource", title=title
        )
        target = _create_seed_purpose(
            api_context, db, active=True, prefix="SeedDupTarget", title=title
        )
        body = {
            "purposeId": int(source["purposeId"]),
            "name": target["name"],
            "active": bool(source["active"]),
        }
        _, payload = execute(body, {400}, expected_error_contains="unique")
        history_lines.append(f"Verification: duplicate target name='{target['name']}', errorCode={_error_code(payload)}")
        _record_dashboard_metrics(request, history_lines)
        return

    if "update purpose with missing active" in title_l:
        row = _get_existing_purpose(db)
        if not row:
            pytest.skip("No purpose exists in DB")
        body = {"purposeId": int(row["purposeId"]), "name": _unique_name("NoActive")}
        _, payload = execute(body, {400}, expected_error_contains="active")
        history_lines.append(f"Verification: errorCode={_error_code(payload)}")
        _record_dashboard_metrics(request, history_lines)
        return

    if "update purpose with missing name" in title_l:
        row = _get_existing_purpose(db)
        if not row:
            pytest.skip("No purpose exists in DB")
        body = {"purposeId": int(row["purposeId"]), "active": bool(row["active"])}
        _, payload = execute(body, {400}, expected_error_contains="name")
        history_lines.append(f"Verification: errorCode={_error_code(payload)}")
        _record_dashboard_metrics(request, history_lines)
        return

    if "verify updated purpose saved in database" in title_l or "verify updated name persisted correctly" in title_l:
        row = _create_seed_purpose(
            api_context, db, active=False, prefix="SeedVerifyUpdate", title=title
        )
        new_name = _unique_name("UpdDb")
        body = {"purposeId": int(row["purposeId"]), "name": new_name, "active": bool(row["active"])}
        execute(body, {200})
        updated = _assert_db_update(db, int(row["purposeId"]), new_name, bool(row["active"]), title)
        history_lines.append(f"DB validation: updated row persisted with expected name. {_db_snapshot(updated)}")
        _record_dashboard_metrics(request, history_lines)
        return

    if "verify active=true persisted correctly" in title_l:
        row = _create_seed_purpose(
            api_context, db, active=False, prefix="SeedPersistTrue", title=title
        )
        body = {"purposeId": int(row["purposeId"]), "name": row["name"], "active": True}
        execute(body, {200})
        updated = _assert_db_update(db, int(row["purposeId"]), row["name"], True, title)
        history_lines.append(f"DB validation: Active=True persisted. {_db_snapshot(updated)}")
        _record_dashboard_metrics(request, history_lines)
        return

    if "verify active=false persisted correctly" in title_l:
        row = _create_seed_purpose(
            api_context, db, active=True, prefix="SeedPersistFalse", title=title
        )
        body = {"purposeId": int(row["purposeId"]), "name": row["name"], "active": False}
        execute(body, {200})
        updated = _assert_db_update(db, int(row["purposeId"]), row["name"], False, title)
        history_lines.append(f"DB validation: Active=False persisted. {_db_snapshot(updated)}")
        _record_dashboard_metrics(request, history_lines)
        return

    if "create purpose with duplicate name containing leading/trailing spaces" in title_l:
        existing = _get_existing_purpose(db)
        if not existing:
            pytest.skip("No purpose exists in DB")
        body = {"name": f"  {existing['name']}  ", "active": True}
        response, payload = execute(body, {200, 400})
        if response.status == 400:
            message = _error_message(payload, "")
            assert "unique" in message.lower(), (
                f"{title}: expected duplicate-name validation, got '{message}'"
            )
        history_lines.append(f"Verification: duplicate-with-space check returned errorCode={_error_code(payload)}")
        _record_dashboard_metrics(request, history_lines)
        return

    if "create purpose with duplicate name" in title_l or "verify 400 response for duplicate name" in title_l:
        existing = _get_existing_purpose(db)
        if not existing:
            pytest.skip("No purpose exists in DB")
        body = {"name": existing["name"], "active": True}
        _, payload = execute(body, {400}, expected_error_contains="unique")
        history_lines.append(f"Verification: duplicate name errorCode={_error_code(payload)}")
        _record_dashboard_metrics(request, history_lines)
        return

    if "missing name" in title_l:
        body = {"active": True}
        _, payload = execute(body, {400}, expected_error_contains="name")
        history_lines.append(f"Verification: missing-name errorCode={_error_code(payload)}")
        _record_dashboard_metrics(request, history_lines)
        return

    if "null name" in title_l:
        body = {"name": None, "active": True}
        _, payload = execute(body, {400}, expected_error_contains="name")
        history_lines.append(f"Verification: null-name errorCode={_error_code(payload)}")
        _record_dashboard_metrics(request, history_lines)
        return

    if "empty name" in title_l:
        body = {"name": "", "active": True}
        _, payload = execute(body, {400}, expected_error_contains="name")
        history_lines.append(f"Verification: empty-name errorCode={_error_code(payload)}")
        _record_dashboard_metrics(request, history_lines)
        return

    if "whitespace-only name" in title_l:
        body = {"name": "   ", "active": True}
        _, payload = execute(body, {400}, expected_error_contains="name")
        history_lines.append(f"Verification: whitespace-name errorCode={_error_code(payload)}")
        _record_dashboard_metrics(request, history_lines)
        return

    if "missing active" in title_l:
        if "update" in title_l:
            row = _get_existing_purpose(db)
            if not row:
                pytest.skip("No purpose exists in DB")
            body = {"purposeId": int(row["purposeId"]), "name": _unique_name("MissActiveUpd")}
        else:
            body = {"name": _unique_name("MissActive")}
        _, payload = execute(body, {400}, expected_error_contains="active")
        history_lines.append(f"Verification: missing-active errorCode={_error_code(payload)}")
        _record_dashboard_metrics(request, history_lines)
        return

    if "null active" in title_l:
        body = {"name": _unique_name("NullActive"), "active": None}
        _, payload = execute(body, {400}, expected_error_contains="active")
        history_lines.append(f"Verification: null-active errorCode={_error_code(payload)}")
        _record_dashboard_metrics(request, history_lines)
        return

    if "active as string value" in title_l:
        body = {"name": _unique_name("ActiveString"), "active": "true"}
        _, payload = execute(body, {400}, expected_error_contains="active")
        history_lines.append(f"Verification: active-string errorCode={_error_code(payload)}")
        _record_dashboard_metrics(request, history_lines)
        return

    if "active as numeric value" in title_l:
        body = {"name": _unique_name("ActiveNumeric"), "active": 1}
        _, payload = execute(body, {400}, expected_error_contains="active")
        history_lines.append(f"Verification: active-numeric errorCode={_error_code(payload)}")
        _record_dashboard_metrics(request, history_lines)
        return

    if "name as numeric datatype" in title_l:
        body = {"name": 12345, "active": True}
        _, payload = execute(body, {400}, expected_error_contains="name")
        history_lines.append(f"Verification: name-numeric errorCode={_error_code(payload)}")
        _record_dashboard_metrics(request, history_lines)
        return

    if "name as boolean datatype" in title_l:
        body = {"name": False, "active": True}
        _, payload = execute(body, {400}, expected_error_contains="name")
        history_lines.append(f"Verification: name-boolean errorCode={_error_code(payload)}")
        _record_dashboard_metrics(request, history_lines)
        return

    if "verify correct error message for missing name" in title_l:
        body = {"active": True}
        _, payload = execute(body, {400}, expected_error_contains="name is required")
        history_lines.append(f"Verification: explicit message matched for missing Name. errorCode={_error_code(payload)}")
        _record_dashboard_metrics(request, history_lines)
        return

    if "verify correct error message for duplicate name" in title_l:
        existing = _get_existing_purpose(db)
        if not existing:
            pytest.skip("No purpose exists in DB")
        body = {"name": existing["name"], "active": True}
        _, payload = execute(body, {400}, expected_error_contains="unique")
        history_lines.append(f"Verification: explicit message matched for duplicate Name. errorCode={_error_code(payload)}")
        _record_dashboard_metrics(request, history_lines)
        return

    if "verify correct error message for invalid name format" in title_l:
        body = {"name": "Invalid@Name", "active": True}
        _, payload = execute(body, {400})
        message = _error_message(payload, "")
        assert any(
            token in message.lower() for token in ("name", "alphanumeric", "invalid", "validation")
        ), f"{title}: expected invalid-name style message, got '{message}'"
        history_lines.append(f"Verification: invalid format message='{message}'")
        _record_dashboard_metrics(request, history_lines)
        return

    if "verify 400 response for invalid name format" in title_l:
        body = {"name": "Name-With-!", "active": True}
        _, payload = execute(body, {400})
        history_lines.append(f"Verification: invalid-format errorCode={_error_code(payload)}")
        _record_dashboard_metrics(request, history_lines)
        return

    if "verify 400 response for missing active" in title_l:
        body = {"name": _unique_name("Resp400Active")}
        _, payload = execute(body, {400}, expected_error_contains="active")
        history_lines.append(f"Verification: expected 400 for missing Active. errorCode={_error_code(payload)}")
        _record_dashboard_metrics(request, history_lines)
        return

    if "verify 400 response for missing name" in title_l:
        body = {"active": True}
        _, payload = execute(body, {400}, expected_error_contains="name")
        history_lines.append(f"Verification: expected 400 for missing Name. errorCode={_error_code(payload)}")
        _record_dashboard_metrics(request, history_lines)
        return

    if "create new purpose with valid name and active=true" in title_l:
        name = _unique_name("CreateTrue")
        body = {"name": name, "active": True}
        before_name_count = db_scripts.count_rows(
            table="cimpl.Purpose",
            where_sql="Name = ?",
            params=(name,),
        )
        execute(body, {200})
        row = _assert_db_create(
            db,
            name,
            True,
            title,
            db_scripts=db_scripts,
            before_name_count=before_name_count,
        )
        history_lines.append(f"DB validation: create with Active=True persisted. {_db_snapshot(row)}")
        _record_dashboard_metrics(request, history_lines)
        return

    if "create new purpose with valid name and active=false" in title_l:
        name = _unique_name("CreateFalse")
        body = {"name": name, "active": False}
        before_name_count = db_scripts.count_rows(
            table="cimpl.Purpose",
            where_sql="Name = ?",
            params=(name,),
        )
        execute(body, {200})
        row = _assert_db_create(
            db,
            name,
            False,
            title,
            db_scripts=db_scripts,
            before_name_count=before_name_count,
        )
        history_lines.append(f"DB validation: create with Active=False persisted. {_db_snapshot(row)}")
        _record_dashboard_metrics(request, history_lines)
        return

    if "verify 200 response for successful create" in title_l:
        body = {"name": _unique_name("Resp200Create"), "active": True}
        _, payload = execute(body, {200})
        history_lines.append(f"Verification: expected 200 received; message={payload.get('message', '')}")
        _record_dashboard_metrics(request, history_lines)
        return

    if "verify created purpose saved in database" in title_l:
        name = _unique_name("CreateDb")
        body = {"name": name, "active": True}
        before_name_count = db_scripts.count_rows(
            table="cimpl.Purpose",
            where_sql="Name = ?",
            params=(name,),
        )
        execute(body, {200})
        row = _assert_db_create(
            db,
            name,
            True,
            title,
            db_scripts=db_scripts,
            before_name_count=before_name_count,
        )
        history_lines.append(f"DB validation: created row exists in DB. {_db_snapshot(row)}")
        _record_dashboard_metrics(request, history_lines)
        return

    if "alphanumeric name" in title_l:
        name = f"A1B2C3{int(time.time())}"
        body = {"name": name, "active": True}
        before_name_count = db_scripts.count_rows(
            table="cimpl.Purpose",
            where_sql="Name = ?",
            params=(name,),
        )
        execute(body, {200})
        row = _assert_db_create(
            db,
            name,
            True,
            title,
            db_scripts=db_scripts,
            before_name_count=before_name_count,
        )
        history_lines.append(f"DB validation: alphanumeric name saved. {_db_snapshot(row)}")
        _record_dashboard_metrics(request, history_lines)
        return

    if "numeric-only name" in title_l:
        name = str(int(time.time() * 1000))
        body = {"name": name, "active": True}
        before_name_count = db_scripts.count_rows(
            table="cimpl.Purpose",
            where_sql="Name = ?",
            params=(name,),
        )
        execute(body, {200})
        row = _assert_db_create(
            db,
            name,
            True,
            title,
            db_scripts=db_scripts,
            before_name_count=before_name_count,
        )
        history_lines.append(f"DB validation: numeric-only name saved. {_db_snapshot(row)}")
        _record_dashboard_metrics(request, history_lines)
        return

    if (
        "special character" in title_l
        or "underscore" in title_l
        or "hyphen" in title_l
        or "period" in title_l
        or "slash" in title_l
        or "unicode" in title_l
        or "spaces between words" in title_l
        or "leading spaces" in title_l
        or "trailing spaces" in title_l
        or "sql injection" in title_l
        or "script tag" in title_l
        or "very long name" in title_l
    ):
        name_value = "Invalid!Name"
        if "underscore" in title_l:
            name_value = "Invalid_Name"
        elif "hyphen" in title_l:
            name_value = "Invalid-Name"
        elif "period" in title_l:
            name_value = "Invalid.Name"
        elif "slash" in title_l:
            name_value = "Invalid/Name"
        elif "unicode" in title_l:
            name_value = "Purose\u00e9\u6d4b\u8bd5"
        elif "spaces between words" in title_l:
            name_value = "new purpose"
        elif "leading spaces" in title_l:
            name_value = "  NewPurpose"
        elif "trailing spaces" in title_l:
            name_value = "NewPurpose  "
        elif "sql injection" in title_l:
            name_value = "Name'; DROP TABLE cimpl.Purpose;--"
        elif "script tag" in title_l:
            name_value = "<script>alert(1)</script>"
        elif "very long name" in title_l:
            name_value = "A" * 700
        elif "special character combination" in title_l:
            name_value = "@Bad-Name!"

        body = {"name": name_value, "active": True}
        allowed_statuses = {400, 500}
        if "leading spaces" in title_l or "trailing spaces" in title_l:
            allowed_statuses = {200, 400}

        _, payload = execute(body, allowed_statuses)
        history_lines.append(f"Verification: invalid name format errorCode={_error_code(payload)}")
        _record_dashboard_metrics(request, history_lines)
        return

    raise AssertionError(f"No case logic mapped for title: {title}")


class TestPostPurposeSave:

    @pytest.mark.parametrize("case", CASE_PARAMS)
    def test_post_purpose_save_cases(self, case, api_context, db, request):
        _case_logic(case, api_context, db, request)
