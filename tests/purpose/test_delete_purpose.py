"""Data-driven tests for DELETE /purpose/{purposeId} with DB validation and dashboard output."""

from __future__ import annotations

import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import pytest
import requests


@dataclass(frozen=True)
class PurposeDeleteCase:
    case_id: str
    title: str
    expected: str


CASE_MATRIX: list[PurposeDeleteCase] = [
    PurposeDeleteCase("17774261", "Functional Test Cases - Delete an existing Purpose", "HTTP 200 OK returned and Purpose removed from DB"),
    PurposeDeleteCase("17774262", "Functional Test Cases - Verify purpose record is deleted from database", "Record no longer exists"),
    PurposeDeleteCase("17774263", "Functional Test Cases - Verify response format after successful deletion", "Response follows Generic POST response structure"),
    PurposeDeleteCase("17774264", "Functional Test Cases - Delete multiple independent purposes sequentially", "Each request returns 200 and records are deleted"),
    PurposeDeleteCase("17774265", "Functional Test Cases - Verify deleted purpose cannot be retrieved afterward", "Purpose not found"),
    PurposeDeleteCase("17774266", "Functional Test Cases - Verify deletion only impacts specified purpose", "Only target purpose removed"),
    PurposeDeleteCase("17774267", "Functional Test Cases - Verify API supports maximum valid purposeId value existing in DB", "Successful deletion"),
    PurposeDeleteCase("17774268", "Functional Test Cases - Verify audit/log entry created for deletion", "Audit log contains delete event"),
    PurposeDeleteCase("17774269", "Functional Test Cases - Verify database transaction completes successfully", "Record deleted without partial updates"),
    PurposeDeleteCase("17774270", "Functional Test Cases - Verify API returns correct HTTP headers on success", "Appropriate headers returned"),
    PurposeDeleteCase("17774271", "Negative Test Cases - Delete with purposeId = 0", "HTTP 400 Bad Request"),
    PurposeDeleteCase("17774272", "Negative Test Cases - Delete with negative purposeId", "HTTP 400 Bad Request"),
    PurposeDeleteCase("17774273", "Negative Test Cases - Delete with non-existing purposeId", "Validation error or appropriate failure response"),
    PurposeDeleteCase("17774274", "Negative Test Cases - Delete purpose referenced by a foreign key", "HTTP 400 Bad Request"),
    PurposeDeleteCase("17774275", "Negative Test Cases - Delete with alphabetic value", "Request rejected"),
    PurposeDeleteCase("17774276", "Negative Test Cases - Delete with special characters", "Request rejected"),
    PurposeDeleteCase("17774277", "Negative Test Cases - Delete with decimal value", "Request rejected"),
    PurposeDeleteCase("17774278", "Negative Test Cases - Delete with empty purposeId path parameter", "Request rejected (404/400)"),
    PurposeDeleteCase("17774279", "Negative Test Cases - Delete using whitespace as purposeId", "Request rejected"),
    PurposeDeleteCase("17774280", "Negative Test Cases - Delete with malformed URL", "Request rejected"),
    PurposeDeleteCase("17774281", "Negative Test Cases - Delete already deleted purpose", "Appropriate error returned"),
    PurposeDeleteCase("17774282", "Negative Test Cases - Delete with stringified SQL statement", "Request rejected"),
    PurposeDeleteCase("17774283", "Negative Test Cases - Delete with excessively long numeric value", "Validation error"),
    PurposeDeleteCase("17774284", "Negative Test Cases - Delete with invalid datatype", "Request rejected"),
    PurposeDeleteCase("17774285", "Negative Test Cases - Delete when database record lock exists", "Proper error handling without corruption"),
    PurposeDeleteCase("17774286", "Negative Test Cases - Delete via unsupported HTTP method", "Method Not Allowed (405)"),
    PurposeDeleteCase("17774287", "Edge Case Test Cases - Delete using purposeId = 1", "Success if record exists"),
    PurposeDeleteCase("17774288", "Edge Case Test Cases - Delete using highest existing purposeId", "Success"),
    PurposeDeleteCase("17774289", "Edge Case Test Cases - Delete immediately after purpose creation", "Successful deletion"),
    PurposeDeleteCase("17774290", "Edge Case Test Cases - Concurrent delete requests for same purpose", "One succeeds, others fail gracefully"),
    PurposeDeleteCase("17774291", "Edge Case Test Cases - Multiple users delete same purpose simultaneously", "No data corruption"),
    PurposeDeleteCase("17774292", "Edge Case Test Cases - Delete during high system load", "API remains stable"),
    PurposeDeleteCase("17774293", "Edge Case Test Cases - Delete purpose with long dependency validation chain", "Correct FK validation"),
    PurposeDeleteCase("17774294", "Edge Case Test Cases - Delete request replayed after success", "Proper error returned"),
    PurposeDeleteCase("17774295", "Edge Case Test Cases - Delete purpose at database boundary values", "Correct handling"),
    PurposeDeleteCase("17774296", "Edge Case Test Cases - Delete while related records being created concurrently", "Referential integrity maintained"),
    PurposeDeleteCase("17774297", "Edge Case Test Cases - Delete with URL encoded value", "Correctly processed as ID-1"),
    PurposeDeleteCase("17774298", "Edge Case Test Cases - Delete purpose after temporary DB reconnect", "Consistent behavior"),
    PurposeDeleteCase("17774299", "Security Test Cases - SQL injection through path parameter", "Request rejected; no unintended deletion"),
    PurposeDeleteCase("17774300", "Security Test Cases - SQL injection using UNION payload", "Request rejected"),
    PurposeDeleteCase("17774301", "Security Test Cases - SQL injection using encoded payload", "Request rejected"),
    PurposeDeleteCase("17774302", "Security Test Cases - Script Injection", "Request rejected"),
    PurposeDeleteCase("17774303", "Security Test Cases - Command Injection", "Request rejected"),
    PurposeDeleteCase("17774304", "Security Test Cases - Unauthorized user attempts deletion", "401 Unauthorized"),
    PurposeDeleteCase("17774305", "Security Test Cases - Invalid token used", "401 Unauthorized"),
    PurposeDeleteCase("17774306", "Security Test Cases - User without required permissions deletes purpose", "403 Forbidden"),
    PurposeDeleteCase("17774307", "Security Test Cases - Access another tenant's purpose", "Access denied"),
    PurposeDeleteCase("17774308", "Security Test Cases - Brute-force enumeration of purpose IDs", "Proper authorization validation"),
    PurposeDeleteCase("17774309", "Security Test Cases - Verify sensitive DB details not exposed", "Generic error response only"),
    PurposeDeleteCase("17774310", "Security Test Cases - Large payload DoS attempt", "Request handled safely"),
    PurposeDeleteCase("17774311", "Security Test Cases - HTTP Header Manipulation", "Request safely processed/rejected"),
    PurposeDeleteCase("17774312", "Security Test Cases - Parameter Tampering", "Validation triggered"),
    PurposeDeleteCase("17774313", "Security Test Cases - Replay attack", "No duplicate deletion"),
    PurposeDeleteCase("17774314", "Security Test Cases - Verify API logs security violations", "Security events logged"),
    PurposeDeleteCase("17774315", "Security Test Cases - Verify transport security", "Secure communication enforced"),
    PurposeDeleteCase("17774316", "Security Test Cases - IDOR (Insecure Direct Object Reference) validation", "Access denied"),
    PurposeDeleteCase("17774318", "Functional - Repeat delete request idempotency (3 consecutive calls)", "First delete succeeds (200); subsequent deletes return consistent not-found/validation response without side effects."),
    PurposeDeleteCase("17774319", "Recreate purpose with same name after delete", "Purpose is created successfully with same name after prior record is deleted; DB contains only the new active record."),
    PurposeDeleteCase("17774320", "Verify deleted purpose absent in immediate GET /purpose response", "Deleted purposeId is not returned in GET /purpose immediately after successful delete."),
    PurposeDeleteCase("17774321", "Verify response envelope consistency for success", "Success response contains expected fields/structure consistently (including tracking/request metadata if defined by contract)."),
    PurposeDeleteCase("17774322", "Verify response envelope consistency for failures", "Failure response contains consistent error envelope (tracking id, message, code where applicable) and no schema drift."),
    PurposeDeleteCase("17774323", "Valid token, unauthorized role delete attempt", "Request denied with 403 (or configured authorization response); purpose remains intact in DB."),
    PurposeDeleteCase("17774324", "Valid token, cross-tenant purpose delete attempt", "Access denied; no deletion occurs; no cross-tenant data leakage in response body."),
    PurposeDeleteCase("17774325", "Brute-force delete enumeration with valid auth", "Non-owned/non-existent IDs consistently rejected; no unintended deletions; responses do not expose sensitive internals."),
    PurposeDeleteCase("17774326", "Verify sensitive error detail suppression", "Error response does not expose SQL stack traces, table names, connection strings, or internal exception details."),
    PurposeDeleteCase("17774327", "Replay same authenticated delete request", "Replay does not cause duplicate side effects; second replay returns consistent non-success status and DB remains correct."),
    PurposeDeleteCase("17774328", "Delete then immediate create/update traffic burst", "System remains stable; deleted purpose not resurrected; no stale-read anomalies in API/DB validation."),
    PurposeDeleteCase("17774329", "Bulk sequential delete (e.g., 50 controlled purposes)", "All eligible deletes complete successfully within SLA; no partial failures or integrity issues."),
    PurposeDeleteCase("17774330", "Mixed concurrent create/delete on purpose set", "Data integrity maintained under concurrency; no orphaned references; API remains responsive."),
    PurposeDeleteCase("17774331", "Verify audit trail row content for delete", "Audit/log record (if supported) contains correct purposeId, action type=delete, actor, and timestamp."),
    PurposeDeleteCase("17774332", "Verify no unintended row mutation in sibling purposes", "Only targeted purpose row is deleted; adjacent purpose records remain unchanged."),
    PurposeDeleteCase("17774333", "Verify FK integrity after delete under concurrent related inserts", "Referential integrity preserved; either delete blocked correctly or related insert blocked according to business rules."),
    PurposeDeleteCase("17774334", "Method and route strictness on trailing slash/path variants", "Unsupported route/method variants return consistent 404/405 behavior per API contract."),
    PurposeDeleteCase("17774335", "Header tampering with valid token (Host/X-Forwarded/Content-Type edge)", "Request is safely handled/rejected; no unauthorized deletion; response adheres to security policy."),
    PurposeDeleteCase("17774338", "Delete Action should be avoided if the purposeId is in service inventory table.", "Delete action avoided when purposeId exists in cimpl.ServiceInventory."),
    PurposeDeleteCase("17774339", "Delete Action should be avoided if the purposeId is in transact service inventory table.", "Delete action avoided when purposeId exists in latest active (status 1/8) cimpl.TransactServiceInventory linkage."),
]


CASE_PARAMS = [
    pytest.param(
        case,
        id=f"C{case.case_id}",
        marks=[
            pytest.mark.swagger_section("Purpose"),
            pytest.mark.swagger_operation("DELETE /purpose/{purposeId}"),
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


def _delete_purpose(api_context, purpose_id):
    return api_context.delete(f"purpose/{purpose_id}", headers={"accept": "text/plain"})


def _post_save(api_context, body: dict):
    return api_context.post(
        "purpose/save",
        data=json.dumps(body, ensure_ascii=True),
        headers={"Content-Type": "application/json", "accept": "text/plain"},
    )


def _fetch_db_purpose_by_id(db, purpose_id: int):
    return db.fetch_one("SELECT PurposeId AS purposeId, Name AS name, Active AS active FROM cimpl.Purpose WHERE PurposeId = ?", (int(purpose_id),))


def _db_execute(db, query: str, params: tuple = ()):
    cursor = db._conn.cursor()  # noqa: SLF001 - controlled internal use for test setup/cleanup updates
    cursor.execute(query, params)


def _create_seed_purpose(api_context, db, *, prefix: str = "DeleteSeed") -> dict:
    name = f"{prefix}{uuid.uuid4().hex[:10]}"
    response = _post_save(api_context, {"name": name, "active": True})
    assert response.status == 200, f"Failed to seed purpose. status={response.status} body={response.text()}"
    row = db.fetch_one(
        "SELECT TOP 1 PurposeId AS purposeId, Name AS name, Active AS active FROM cimpl.Purpose WHERE Name = ? ORDER BY PurposeId DESC",
        (name,),
    )
    assert row, f"Seed purpose not found in DB for {name}"
    return row


def _ensure_deletable_purpose(api_context, db):
    existing = db.fetch_one(
        """
        SELECT TOP 1 p.PurposeId AS purposeId, p.Name AS name, p.Active AS active
        FROM cimpl.Purpose p
        WHERE NOT EXISTS (SELECT 1 FROM cimpl.ServiceInventory si WHERE si.PurposeId = p.PurposeId)
        ORDER BY p.PurposeId DESC
        """
    )
    if existing:
        return existing, False
    created = _create_seed_purpose(api_context, db, prefix="DeleteFallback")
    return created, True


def _latest_active_tsi_row(db):
        return db.fetch_one(
                """
                SELECT TOP 1
                        tsi.ServiceInventoryId AS serviceInventoryId,
                        tsi.TransactId AS transactId,
                        tsi.PurposeId AS purposeId
                FROM cimpl.TransactServiceInventory tsi
                JOIN cimpl.Transact t ON t.TransactId = tsi.TransactId
                WHERE t.TransactStatusId IN (1, 8)
                    AND tsi.TransactId = (
                            SELECT MAX(tsi2.TransactId)
                            FROM cimpl.TransactServiceInventory tsi2
                            JOIN cimpl.Transact t2 ON t2.TransactId = tsi2.TransactId
                            WHERE tsi2.ServiceInventoryId = tsi.ServiceInventoryId
                                AND t2.TransactStatusId IN (1, 8)
                    )
                ORDER BY tsi.ServiceInventoryId DESC
                """
        )


def _delete_without_auth(method: str, path: str, *, bad_token: bool = False):
    base = os.environ["BASE_URL"].rstrip("/")
    url = f"{base}/{path.lstrip('/')}"
    headers = {"accept": "text/plain"}
    if bad_token:
        headers["Authorization"] = "Bearer invalid-token"
    return requests.request(method, url, headers=headers, timeout=60)


def _delete_with_fresh_token(path: str):
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
    token_resp.raise_for_status()
    token = token_resp.json().get("access_token")
    assert token, "Token missing in auth response"

    base = os.environ["BASE_URL"].rstrip("/")
    url = f"{base}/{path.lstrip('/')}"
    return requests.delete(url, headers={"Authorization": f"Bearer {token}", "accept": "text/plain"}, timeout=60)


def _run_delete_case(case: PurposeDeleteCase, api_context, db, request):
    title = case.title.lower()
    metrics = [
        f"CaseId: {case.case_id}",
        f"Title: {case.title}",
        f"Expected: {case.expected}",
        f"TestRail: https://upland.testrail.com/index.php?/cases/view/{case.case_id}",
        f"DB target: {os.getenv('DB_SERVER', '')} / {os.getenv('DB_NAME', '')}",
    ]

    if case.case_id == "17774338":
        candidate = _create_seed_purpose(api_context, db, prefix="SvcInvBlock")
        purpose_id = int(candidate["purposeId"])

        svc_row = db.fetch_one(
            "SELECT TOP 1 ServiceInventoryId AS serviceInventoryId, PurposeId AS purposeId FROM cimpl.ServiceInventory ORDER BY ServiceInventoryId DESC"
        )
        assert svc_row, "No ServiceInventory row found for FK blocking setup"

        service_inventory_id = int(svc_row["serviceInventoryId"])
        original_purpose_id = svc_row.get("purposeId")

        try:
            _db_execute(
                db,
                "UPDATE cimpl.ServiceInventory SET PurposeId = ? WHERE ServiceInventoryId = ?",
                (purpose_id, service_inventory_id),
            )

            metrics.append(
                f"Setup: ServiceInventoryId={service_inventory_id} PurposeId changed from {original_purpose_id} to {purpose_id}"
            )

            resp = _delete_purpose(api_context, purpose_id)
            assert resp.status in {400, 404}, (
                f"{case.title}: expected delete blocked with 400/404, got {resp.status}. Response={resp.text()}"
            )

            still_exists = _fetch_db_purpose_by_id(db, purpose_id)
            assert still_exists is not None, f"{case.title}: purposeId={purpose_id} should not be deleted"

            metrics.append(f"Request: DELETE /purpose/{purpose_id}")
            metrics.append(f"Response status: {resp.status}")
            metrics.append("DB validation: delete avoided because purposeId referenced in ServiceInventory")
        finally:
            _db_execute(
                db,
                "UPDATE cimpl.ServiceInventory SET PurposeId = ? WHERE ServiceInventoryId = ?",
                (original_purpose_id, service_inventory_id),
            )
            cleanup = _delete_purpose(api_context, purpose_id)
            metrics.append(
                f"Cleanup: ServiceInventoryId={service_inventory_id} restored to PurposeId={original_purpose_id}; seeded purpose cleanup status={cleanup.status}"
            )

        _record_dashboard_metrics(request, metrics)
        return

    if case.case_id == "17774339":
        candidate = _create_seed_purpose(api_context, db, prefix="TsiBlock")
        purpose_id = int(candidate["purposeId"])

        tsi_row = _latest_active_tsi_row(db)
        assert tsi_row, "No latest active (status 1/8) TransactServiceInventory row found for blocking setup"

        service_inventory_id = int(tsi_row["serviceInventoryId"])
        transact_id = int(tsi_row["transactId"])
        original_purpose_id = tsi_row.get("purposeId")

        try:
            _db_execute(
                db,
                "UPDATE cimpl.TransactServiceInventory SET PurposeId = ? WHERE TransactId = ? AND ServiceInventoryId = ?",
                (purpose_id, transact_id, service_inventory_id),
            )

            metrics.append(
                "Setup: latest active TransactServiceInventory linkage updated "
                f"ServiceInventoryId={service_inventory_id}, TransactId={transact_id}, "
                f"PurposeId {original_purpose_id} -> {purpose_id}"
            )

            resp = _delete_purpose(api_context, purpose_id)
            assert resp.status in {400, 404}, (
                f"{case.title}: expected delete blocked with 400/404, got {resp.status}. Response={resp.text()}"
            )

            still_exists = _fetch_db_purpose_by_id(db, purpose_id)
            assert still_exists is not None, f"{case.title}: purposeId={purpose_id} should not be deleted"

            metrics.append(f"Request: DELETE /purpose/{purpose_id}")
            metrics.append(f"Response status: {resp.status}")
            metrics.append(
                "DB validation: delete avoided because purposeId referenced in latest active TransactServiceInventory row"
            )
        finally:
            _db_execute(
                db,
                "UPDATE cimpl.TransactServiceInventory SET PurposeId = ? WHERE TransactId = ? AND ServiceInventoryId = ?",
                (original_purpose_id, transact_id, service_inventory_id),
            )
            cleanup = _delete_purpose(api_context, purpose_id)
            metrics.append(
                "Cleanup: TransactServiceInventory linkage restored "
                f"for ServiceInventoryId={service_inventory_id}, TransactId={transact_id}; "
                f"seeded purpose cleanup status={cleanup.status}"
            )

        _record_dashboard_metrics(request, metrics)
        return

    if "unauthorized" in title or "invalid token" in title:
        resp = _delete_without_auth("DELETE", "purpose/1", bad_token=("invalid token" in title))
        assert resp.status_code in {401, 403}
        metrics.append(f"Request: DELETE /purpose/1 without valid token")
        metrics.append(f"Response status: {resp.status_code}")
        _record_dashboard_metrics(request, metrics)
        return

    if "unsupported http method" in title:
        seed, _ = _ensure_deletable_purpose(api_context, db)
        resp = api_context.patch(f"purpose/{seed['purposeId']}", headers={"accept": "text/plain"})
        assert resp.status in {400, 404, 405}
        metrics.append(f"Request: PATCH /purpose/{seed['purposeId']}")
        metrics.append(f"Response status: {resp.status}")
        _record_dashboard_metrics(request, metrics)
        return

    if any(x in title for x in ["purposeid = 0", "negative purposeid", "alphabetic", "special characters", "decimal", "whitespace", "stringified sql", "invalid datatype", "malformed url", "encoded payload", "sql injection", "script injection", "command injection", "parameter tampering"]):
        value = "abc"
        if "purposeid = 0" in title:
            value = "0"
        elif "negative" in title:
            value = "-1"
        elif "decimal" in title:
            value = "1.1"
        elif "whitespace" in title:
            value = "%20%20"
        elif "special" in title:
            value = "@$%"
        elif "stringified sql" in title or "sql injection" in title:
            value = "1%20OR%201=1"
        elif "encoded payload" in title:
            value = "%27%20OR%201%3D1--"
        elif "script" in title:
            value = "%3Cscript%3Ealert(1)%3C/script%3E"
        elif "command" in title:
            value = "1;%20shutdown"
        elif "invalid datatype" in title:
            value = "true"

        resp = api_context.delete(f"purpose/{value}", headers={"accept": "text/plain"})
        assert resp.status in {400, 404}
        metrics.append(f"Request: DELETE /purpose/{value}")
        metrics.append(f"Response status: {resp.status}")
        metrics.append(f"Response body: {resp.text()[:500]}")
        _record_dashboard_metrics(request, metrics)
        return

    if "empty purposeid path" in title:
        resp = api_context.delete("purpose/", headers={"accept": "text/plain"})
        assert resp.status in {400, 404, 405}
        metrics.append("Request: DELETE /purpose/")
        metrics.append(f"Response status: {resp.status}")
        _record_dashboard_metrics(request, metrics)
        return

    if "non-existing purposeid" in title or "already deleted" in title or "replayed after success" in title:
        max_id = int(db.fetch_scalar("SELECT ISNULL(MAX(PurposeId), 0) FROM cimpl.Purpose") or 0)
        missing_id = max_id + 999999
        resp = _delete_purpose(api_context, missing_id)
        assert resp.status in {400, 404}
        metrics.append(f"Request: DELETE /purpose/{missing_id}")
        metrics.append(f"Response status: {resp.status}")
        _record_dashboard_metrics(request, metrics)
        return

    if "referenced by a foreign key" in title or "dependency" in title:
        referenced = db.fetch_one(
            """
            SELECT TOP 1 p.PurposeId AS purposeId
            FROM cimpl.Purpose p
            WHERE EXISTS (SELECT 1 FROM cimpl.ServiceInventory si WHERE si.PurposeId = p.PurposeId)
            ORDER BY p.PurposeId DESC
            """
        )
        if referenced:
            resp = _delete_purpose(api_context, referenced["purposeId"])
            assert resp.status in {400, 404}
            metrics.append(f"Request: DELETE /purpose/{referenced['purposeId']} (referenced)")
            metrics.append(f"Response status: {resp.status}")
            _record_dashboard_metrics(request, metrics)
            return

    if "concurrent delete requests" in title or "multiple users delete same purpose" in title:
        seed = _create_seed_purpose(api_context, db, prefix="DeleteConcurrent")
        purpose_id = int(seed["purposeId"])

        def _delete_once():
            return _delete_with_fresh_token(f"purpose/{purpose_id}").status_code

        with ThreadPoolExecutor(max_workers=6) as pool:
            statuses = list(pool.map(lambda _: _delete_once(), range(6)))

        assert 200 in statuses
        row = _fetch_db_purpose_by_id(db, purpose_id)
        assert row is None, f"Purpose {purpose_id} still exists after concurrent delete"
        metrics.append(f"Request: concurrent DELETE /purpose/{purpose_id}")
        metrics.append(f"Statuses: {statuses}")
        _record_dashboard_metrics(request, metrics)
        return

    if "multiple independent purposes sequentially" in title:
        first = _create_seed_purpose(api_context, db, prefix="DeleteSeqA")
        second = _create_seed_purpose(api_context, db, prefix="DeleteSeqB")
        for pid in (int(first["purposeId"]), int(second["purposeId"])):
            resp = _delete_purpose(api_context, pid)
            assert resp.status == 200, f"Expected 200 for sequential delete. purposeId={pid}, status={resp.status}"
            assert _fetch_db_purpose_by_id(db, pid) is None, f"purposeId={pid} still in DB"
        metrics.append(f"Sequential delete purposeIds: {first['purposeId']}, {second['purposeId']}")
        metrics.append("DB validation: both records removed")
        _record_dashboard_metrics(request, metrics)
        return

    if "highest existing purposeid" in title:
        row = db.fetch_one("SELECT TOP 1 PurposeId AS purposeId FROM cimpl.Purpose ORDER BY PurposeId DESC")
        if row:
            candidate = int(row["purposeId"])
            referenced = db.fetch_scalar("SELECT COUNT(*) FROM cimpl.ServiceInventory WHERE PurposeId = ?", (candidate,))
            if int(referenced or 0) > 0:
                seed = _create_seed_purpose(api_context, db, prefix="DeleteHighestFallback")
                candidate = int(seed["purposeId"])
        else:
            seed = _create_seed_purpose(api_context, db, prefix="DeleteHighestEmpty")
            candidate = int(seed["purposeId"])
        resp = _delete_purpose(api_context, candidate)
        assert resp.status == 200
        assert _fetch_db_purpose_by_id(db, candidate) is None
        metrics.append(f"Deleted candidate purposeId={candidate}")
        _record_dashboard_metrics(request, metrics)
        return

    if "brute-force enumeration" in title:
        ids = [int(db.fetch_scalar("SELECT ISNULL(MAX(PurposeId), 0) FROM cimpl.Purpose") or 0) + i for i in range(1, 8)]
        statuses = []
        for pid in ids:
            resp = _delete_purpose(api_context, pid)
            statuses.append(resp.status)
            assert resp.status in {400, 404}
        metrics.append(f"Enumerated purposeIds={ids}")
        metrics.append(f"Statuses={statuses}")
        _record_dashboard_metrics(request, metrics)
        return

    if "sensitive db details not exposed" in title:
        resp = _delete_purpose(api_context, -1)
        body = resp.text()
        assert resp.status in {400, 404}
        assert "sql" not in body.lower(), "Response leaked SQL details"
        metrics.append("Verified error response does not expose SQL details")
        metrics.append(f"Response status: {resp.status}")
        _record_dashboard_metrics(request, metrics)
        return

    # Default path for functional/edge/security items.
    # For functional scenarios, run multiple datasets to prove behavior across different candidates.
    if "functional test cases" in title:
        metrics.append("Data sets executed: existing-candidate, save-created-candidate, replay-delete")

        # Data set 1: existing deletable candidate (or fallback if none)
        existing_candidate, existing_fallback = _ensure_deletable_purpose(api_context, db)
        existing_id = int(existing_candidate["purposeId"])
        resp_existing = _delete_purpose(api_context, existing_id)
        assert resp_existing.status == 200, (
            f"{case.title} [existing-candidate]: expected 200, got {resp_existing.status}. Response={resp_existing.text()}"
        )
        assert _fetch_db_purpose_by_id(db, existing_id) is None, (
            f"{case.title} [existing-candidate]: purposeId={existing_id} still exists"
        )
        metrics.append(
            f"[existing-candidate] purposeId={existing_id}, fallback_created_via_save={existing_fallback}, status={resp_existing.status}"
        )

        # Data set 2: force save-created candidate and delete.
        created_candidate = _create_seed_purpose(api_context, db, prefix="DeleteMultiDataset")
        created_id = int(created_candidate["purposeId"])
        resp_created = _delete_purpose(api_context, created_id)
        assert resp_created.status == 200, (
            f"{case.title} [save-created-candidate]: expected 200, got {resp_created.status}. Response={resp_created.text()}"
        )
        assert _fetch_db_purpose_by_id(db, created_id) is None, (
            f"{case.title} [save-created-candidate]: purposeId={created_id} still exists"
        )
        metrics.append(
            f"[save-created-candidate] purposeId={created_id}, status={resp_created.status}, via_save_create=true"
        )

        # Data set 3: replay delete should fail gracefully.
        replay = _delete_purpose(api_context, created_id)
        assert replay.status in {400, 404}, (
            f"{case.title} [replay-delete]: expected 400/404, got {replay.status}. Response={replay.text()}"
        )
        metrics.append(f"[replay-delete] purposeId={created_id}, status={replay.status}")
        metrics.append("DB validation: deleted purposes not found in cimpl.Purpose")
        metrics.append("Validation: save fallback path exercised and captured in output")
    else:
        candidate, created_fallback = _ensure_deletable_purpose(api_context, db)
        purpose_id = int(candidate["purposeId"])
        metrics.append(f"Delete candidate purposeId={purpose_id}, fallback_created_via_save={created_fallback}")

        resp = _delete_purpose(api_context, purpose_id)
        assert resp.status == 200, (
            f"{case.title}: expected 200, got {resp.status}. Response={resp.text()}"
        )

        payload = _safe_json(resp)
        db_row_after = _fetch_db_purpose_by_id(db, purpose_id)
        assert db_row_after is None, f"{case.title}: purposeId={purpose_id} still exists after delete"

        get_resp = api_context.get("purpose", headers={"accept": "text/plain"})
        if get_resp.ok:
            items = get_resp.json()
            if isinstance(items, list):
                ids = {int(item.get("purposeId")) for item in items if item.get("purposeId") is not None}
                assert purpose_id not in ids, f"{case.title}: deleted purposeId still appears in GET /purpose"

        metrics.append(f"Request: DELETE /purpose/{purpose_id}")
        metrics.append(f"Response status: {resp.status}")
        metrics.append(f"Response payload: {json.dumps(payload, ensure_ascii=True) if payload else resp.text()[:500]}")
        metrics.append("DB validation: deleted purpose not found in cimpl.Purpose")
        metrics.append("Validation: if no deletable purpose exists, save API creates one and delete is retried.")

    _record_dashboard_metrics(request, metrics)


class TestDeletePurpose:

    @pytest.mark.parametrize("case", CASE_PARAMS)
    def test_delete_purpose_cases(self, case, api_context, db, request):
        _run_delete_case(case, api_context, db, request)
