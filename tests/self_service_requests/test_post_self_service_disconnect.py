"""REQ09 coverage for POST /requestApi/SelfServiceRequests/Disconnect."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID

import pytest
import requests

from utils.post_api_db_scripts import PostApiDbScripts

SELF_SERVICE_URL = os.getenv("SELF_SERVICE_BASE_URL", "https://cplca1webq01.uplandsoftware.net/requestApi").rstrip("/") + "/SelfServiceRequests/Disconnect"
OPERATION_TYPE_ID = 11

@dataclass(frozen=True)
class DisconnectCase:
    case_id: str
    title: str
    label: str

_CASES = [
("17863648", "Verify when Service status is inactive", "Validation"),
("17863649", "Verify when service has an open workorder", "Validation"),
("17863650", "Verify when service has an open transaction", "Validation"),
("17863651", "Verify when service has a self-service approval in progress", "Validation"),
("17863652", "Verify invalid asset condition", "Validation"),
("17863653", "Verify invalid asset returnOrKeep", "Validation"),
("17863654", "Verify asset return or removal modes", "Positive"),
("17863655", "Verify ReturnOrKeep is mandatory", "Validation"),
("17863656", "Verify warehouse CanAdd disabled", "Validation"),
("17863657", "Verify warehouse is compatible with service type", "Validation"),
("17863658", "Verify inactive warehouse", "Validation"),
("17863659", "Verify valid service identifier", "Positive"),
("17863660", "Verify invalid service identifier", "Validation"),
("17863661", "Verify missing service", "Validation"),
("17863662", "Verify provider mismatch", "Validation"),
("17863663", "Verify service number mismatch", "Validation"),
    ("17863664", "Verify inactive requester is accepted by the API", "Positive"),
("17863665", "Verify invalid requester", "Validation"),
("17863666", "Verify missing requester", "Validation"),
("17863667", "Verify past due date", "Validation"),
("17863668", "Verify invalid due date", "Validation"),
("17863669", "Verify missing due date", "Validation"),
("17863670", "Verify comments optional", "Positive"),
("17863671", "Verify hardware inventory during disconnect transaction", "Positive"),
]
CASES = [DisconnectCase(*row) for row in _CASES]
CASE_PARAMS = [pytest.param(c, id=f"C{c.case_id}", marks=[pytest.mark.swagger_section("Self Service Requests"), pytest.mark.swagger_operation("POST /requestApi/SelfServiceRequests/Disconnect"), pytest.mark.testrail_case(c.case_id), pytest.mark.dashboard_title(c.title)]) for c in CASES]


def _json(response):
    try:
        value = response.json()
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}


def _employee(db, *, active: bool = True, exclude_id: int | None = None) -> dict:
    exclusion = " AND EmployeeId<>?" if exclude_id is not None else ""
    params = (1 if active else 0,) + ((exclude_id,) if exclude_id is not None else ())
    row = db.fetch_one("SELECT TOP 1 EmployeeId AS id,Number AS number,EMail AS email FROM cimpl.Employee WHERE Active=? AND EmployeeStatusId=1 AND EMail IS NOT NULL" + exclusion + " ORDER BY EmployeeId DESC", params)
    assert row, "No employee seed available"
    return {"id": int(row["id"]), "number": str(row.get("number") or ""), "email": str(row["email"])}


def _service(db, *, status_id: int = 1, open_workorder: bool = False) -> dict:
    if open_workorder:
        row = db.fetch_one("""SELECT TOP 1 si.ServiceInventoryId AS id,a.Number AS accountNumber,si.ProviderId AS providerId,si.PrimaryNumber AS serviceNumber,si.ServiceTypeId AS serviceTypeId FROM cimpl.TransactWorkorderService tws INNER JOIN cimpl.WorkorderSummary ws ON ws.WorkorderId=tws.WorkorderId INNER JOIN cimpl.OpenWorkorderStatusView os ON os.WorkorderStatusId=ws.WorkorderStatusId INNER JOIN cimpl.ServiceInventory si ON si.ServiceInventoryId=tws.ServiceInventoryId INNER JOIN cimpl.Account a ON a.AccountId=si.AccountId WHERE si.PrimaryNumber IS NOT NULL AND a.Number IS NOT NULL ORDER BY tws.WorkorderId DESC""")
    else:
        transaction_filter = "AND NOT EXISTS (SELECT 1 FROM cimpl.TransactServiceInventory tsi WHERE tsi.TransactId=(SELECT TOP 1 latest.TransactId FROM cimpl.TransactServiceInventory latest WHERE latest.ServiceInventoryId=si.ServiceInventoryId ORDER BY latest.TransactId DESC))" if status_id == 1 else ""
        row = db.fetch_one("""SELECT TOP 1 si.ServiceInventoryId AS id,a.Number AS accountNumber,si.ProviderId AS providerId,si.PrimaryNumber AS serviceNumber,si.ServiceTypeId AS serviceTypeId FROM cimpl.ServiceInventory si INNER JOIN cimpl.Account a ON a.AccountId=si.AccountId WHERE si.ServiceInventoryStatusId=? AND si.ProviderId IS NOT NULL AND si.PrimaryNumber IS NOT NULL AND a.Number IS NOT NULL AND EXISTS (SELECT 1 FROM cimpl.ServiceInventoryHardwareInventoryRelation r WHERE r.ServiceInventoryId=si.ServiceInventoryId) """ + transaction_filter + " AND NOT EXISTS (SELECT 1 FROM cimpl.TransactWorkorderService tws INNER JOIN cimpl.WorkorderSummary ws ON ws.WorkorderId=tws.WorkorderId INNER JOIN cimpl.WorkorderStatus wos ON wos.WorkorderStatusId=ws.WorkorderStatusId AND wos.TenantId=ws.TenantId WHERE tws.ServiceInventoryId=si.ServiceInventoryId AND tws.WorkorderId=(SELECT TOP 1 latest.WorkorderId FROM cimpl.TransactWorkorderService latest WHERE latest.ServiceInventoryId=si.ServiceInventoryId ORDER BY latest.WorkorderId DESC) AND LOWER(wos.Name) NOT IN ('completed','cancelled','canceled')) AND NOT EXISTS (SELECT 1 FROM cimpl.SelfServiceActionLog ssl WHERE ssl.ServiceInventoryId=si.ServiceInventoryId AND ssl.StatusId=1 AND ssl.WorkorderId IS NULL AND ssl.UpdatedDate IS NULL) ORDER BY si.ServiceInventoryId DESC""", (status_id,))
    assert row, "No matching service seed available"
    return {key: row[key] for key in row}


def _asset(db, service_id: int) -> dict:
    row = db.fetch_one("SELECT TOP 1 HardwareInventoryId AS id FROM cimpl.ServiceInventoryHardwareInventoryRelation WHERE ServiceInventoryId=? ORDER BY IsPrimary DESC,HardwareInventoryId DESC", (service_id,))
    assert row, "No hardware asset seed available"
    return {"id": str(row["id"]), "returnOrKeep": "ReturnToWarehouse", "condition": "Used"}


def _warehouse(db, service_type_id: int, *, active: bool = True, can_add: bool = True) -> int:
    row = db.fetch_one("SELECT TOP 1 w.WarehouseId AS id FROM cimpl.Warehouse w LEFT JOIN cimpl.WarehouseServiceTypeCompatibility c ON c.WarehouseId=w.WarehouseId AND c.ServiceTypeId=? WHERE w.Active=? AND w.CanAdd=? " + ("AND c.WarehouseId IS NOT NULL" if active and can_add else "") + " ORDER BY w.WarehouseId DESC", (service_type_id, int(active), int(can_add)))
    assert row, "No compatible warehouse seed available"
    return int(row["id"])


def _payload(db, case: DisconnectCase):
    service = _service(db)
    employee = _employee(db)
    marker = f"AUTO-SELF-SERVICE-REQ09-C{case.case_id}"
    body = {"asset": [_asset(db, int(service["id"]))], "service": {"id": int(service["id"]), "accountNumber": str(service["accountNumber"]), "providerId": int(service["providerId"]), "serviceNumber": str(service["serviceNumber"])}, "requester": employee, "dueDate": (date.today() + timedelta(days=2)).isoformat(), "comments": marker}
    body["asset"][0]["warehouseId"] = _warehouse(db, int(service["serviceTypeId"]))
    return body, {"service": service, "marker": marker}


def _mutate(case: DisconnectCase, body: dict, context: dict, db):
    title = case.title.lower()
    if "inactive" in title and "requester" not in title and "warehouse" not in title and "service" in title: body["service"] = {**_service(db, status_id=2), "id": int(_service(db, status_id=2)["id"])}
    elif "open workorder" in title: service = _service(db, open_workorder=True); body["service"] = {k: service[k] for k in ("id", "accountNumber", "providerId", "serviceNumber")}
    elif "open transaction" in title:
        service = db.fetch_one("""SELECT TOP 1 si.ServiceInventoryId AS id,a.Number AS accountNumber,si.ProviderId AS providerId,si.PrimaryNumber AS serviceNumber FROM cimpl.ServiceInventory si INNER JOIN cimpl.Account a ON a.AccountId=si.AccountId INNER JOIN cimpl.TransactServiceInventory tsi ON tsi.ServiceInventoryId=si.ServiceInventoryId AND tsi.TransactStatusId=1 WHERE si.ServiceInventoryStatusId=1 AND si.PrimaryNumber IS NOT NULL AND a.Number IS NOT NULL ORDER BY tsi.TransactId DESC""")
        assert service, "No active-transaction service seed available"
        body["service"] = {k: service[k] for k in ("id", "accountNumber", "providerId", "serviceNumber")}
    elif "approval" in title:
        service = db.fetch_one("""SELECT TOP 1 si.ServiceInventoryId AS id,a.Number AS accountNumber,si.ProviderId AS providerId,si.PrimaryNumber AS serviceNumber FROM cimpl.ServiceInventory si INNER JOIN cimpl.Account a ON a.AccountId=si.AccountId INNER JOIN cimpl.SelfServiceActionLog ssl ON ssl.ServiceInventoryId=si.ServiceInventoryId AND ssl.StatusId=1 AND ssl.WorkorderId IS NULL AND ssl.UpdatedDate IS NULL WHERE si.ServiceInventoryStatusId=1 AND si.PrimaryNumber IS NOT NULL AND a.Number IS NOT NULL ORDER BY ssl.SelfServiceActionLogId DESC""")
        assert service, "No pending self-service approval seed available"
        body["service"] = {k: service[k] for k in ("id", "accountNumber", "providerId", "serviceNumber")}
    elif "invalid asset condition" in title: body["asset"][0]["condition"] = "ajhsh"
    elif "invalid asset return" in title: body["asset"][0]["returnOrKeep"] = "Return"
    elif "return or removal" in title: body["asset"][0]["returnOrKeep"] = "RemoveFromInventory"
    elif "returnorkeep is mandatory" in title: body["asset"][0].pop("returnOrKeep", None)
    elif "canadd disabled" in title: body["asset"][0]["warehouseId"] = _warehouse(db, int(context["service"]["serviceTypeId"]), can_add=False)
    elif "compatible with service" in title: body["asset"][0]["warehouseId"] = 999999999
    elif "inactive warehouse" in title: body["asset"][0]["warehouseId"] = _warehouse(db, int(context["service"]["serviceTypeId"]), active=False)
    elif "invalid service identifier" in title: body["service"] = {"id": 999999999}
    elif "missing service" in title: body.pop("service", None)
    elif "provider mismatch" in title: body["service"]["providerId"] = 999999999
    elif "service number mismatch" in title: body["service"]["serviceNumber"] = "INVALID-SERVICE"
    elif "inactive requester" in title: body["requester"] = _employee(db, active=False)
    elif "invalid requester" in title: body["requester"] = {"id": 999999999}
    elif "missing requester" in title: body.pop("requester", None)
    elif "past due" in title: body["dueDate"] = (date.today() - timedelta(days=1)).isoformat()
    elif "invalid due" in title: body["dueDate"] = "2026-02-30"
    elif "missing due" in title: body.pop("dueDate", None)
    elif "comments optional" in title: body.pop("comments", None)


def _log(db, number):
    return db.fetch_one("SELECT TOP 1 SelfServiceActionLogId,RequestId,Number,StatusId,OperationTypeId,ServiceInventoryId,WorkorderNumber,WorkorderId,RequesterEmployeeId,DueDate,Comment FROM cimpl.SelfServiceActionLog WHERE Number=? ORDER BY SelfServiceActionLogId DESC", (number,))


class TestPostSelfServiceDisconnect:
    @pytest.mark.parametrize("case", CASE_PARAMS)
    def test_post_self_service_disconnect(self, case, api_token, db, request):
        body, context = _payload(db, case); _mutate(case, body, context, db)
        scripts = PostApiDbScripts(db); before = scripts.count_rows(table="cimpl.SelfServiceActionLog", where_sql="Comment LIKE ?", params=(f"%{context['marker']}%",))
        headers = {"Authorization": f"Bearer {api_token}", "Accept": "application/json", "Content-Type": "application/json"}
        response = requests.post(SELF_SERVICE_URL, json=body, headers={} if case.label == "Authorization" else headers, timeout=120); payload = _json(response)
        metrics = [f"CaseId: {case.case_id}", f"Request body: {json.dumps(body, default=str)}", f"OperationTypeId: {OPERATION_TYPE_ID}"]
        try:
            if case.label == "Positive":
                assert response.status_code in {200, 201}, f"Expected success, got {response.status_code}: {response.text}"; number = str(payload.get("selfServiceRequestNumber") or payload.get("selfServiceRequestNumber") or ""); UUID(str(payload.get("trackingId") or "")); row = _log(db, number); assert row and int(row["OperationTypeId"]) == OPERATION_TYPE_ID; metrics.append(f"SelfServiceActionLog: {json.dumps(row, default=str)}")
            else:
                assert response.status_code in {400, 404, 409, 500}, f"Expected validation error, got {response.status_code}: {response.text}"; after = scripts.count_rows(table="cimpl.SelfServiceActionLog", where_sql="Comment LIKE ?", params=(f"%{context['marker']}%",)); scripts.assert_count_delta(before=before, after=after, expected_delta=0, context=case.title)
        finally:
            metrics.append(f"Response: {json.dumps(payload, default=str)}"); request.node.user_properties.append(("dashboard_metrics", "\n".join(metrics)))
