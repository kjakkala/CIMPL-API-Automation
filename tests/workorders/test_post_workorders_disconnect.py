"""Data-driven validation for POST /v1/workorders/disconnect."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pytest
import requests

from utils.post_api_db_scripts import PostApiDbScripts


@dataclass(frozen=True)
class DisconnectCase:
    case_id: str
    title: str
    label: str


CASES = [
    DisconnectCase("17799742", "Disconnect with warehouse", "Positive"), DisconnectCase("17799743", "Disconnect without warehouse", "Positive"),
    DisconnectCase("17799744", "Disconnect with requester ID only", "Positive"), DisconnectCase("17799745", "Missing requester", "Validation"),
    DisconnectCase("17799746", "Requester identifiers mismatch", "Validation"), DisconnectCase("17799747", "Missing service", "Validation"),
    DisconnectCase("17799748", "Inactive service", "Validation"), DisconnectCase("17799749", "Suspended service", "Validation"),
    DisconnectCase("17799750", "Spare service", "Validation"), DisconnectCase("17799751", "Disconnected service", "Validation"),
    DisconnectCase("17799752", "Service with open workorder", "Validation"), DisconnectCase("17799753", "Invalid service identifier", "Validation"),
    DisconnectCase("17799754", "Missing warehouseId", "Positive"), DisconnectCase("17799755", "Invalid warehouseId", "Validation"),
    DisconnectCase("17799756", "Inactive warehouse", "Positive"), DisconnectCase("17799757", "Warehouse cannot add hardware", "Positive"),
    DisconnectCase("17799758", "Invalid dueDate format", "Validation"), DisconnectCase("17799759", "Missing dueDate", "Positive"),
    DisconnectCase("17799760", "Missing comments", "Positive"), DisconnectCase("17799761", "SQL injection in comments", "Security"),
    DisconnectCase("17799762", "Unauthorized Disconnect request", "Authorization"),
    DisconnectCase("17799763", "Disconnect without action", "Positive"), DisconnectCase("17799764", "Disconnect with TransferOfLiabilityOut action", "Positive"),
    DisconnectCase("17799765", "Disconnect with lowercase TransferOfLiabilityOut action", "Positive"), DisconnectCase("17799766", "Disconnect with invalid action", "Validation"),
    DisconnectCase("17799767", "Disconnect with requester email only", "Positive"), DisconnectCase("17799768", "Disconnect with requester number only", "Positive"),
    DisconnectCase("17799769", "Disconnect with null requester", "Validation"), DisconnectCase("17799770", "Disconnect with empty requester", "Validation"),
    DisconnectCase("17799771", "Disconnect with invalid requester ID", "Validation"), DisconnectCase("17799772", "Disconnect with invalid requester email", "Validation"),
    DisconnectCase("17799773", "Disconnect with invalid requester number", "Validation"), DisconnectCase("17799774", "Disconnect with inactive requester", "Validation"),
    DisconnectCase("17799775", "Disconnect with service ID only", "Positive"), DisconnectCase("17799776", "Disconnect with service ID and providerId only", "Positive"),
    DisconnectCase("17799777", "Disconnect missing service providerId", "Validation"), DisconnectCase("17799778", "Disconnect missing service accountNumber", "Validation"),
    DisconnectCase("17799779", "Disconnect missing service serviceNumber", "Validation"), DisconnectCase("17799780", "Disconnect with invalid providerId", "Validation"),
    DisconnectCase("17799781", "Disconnect with invalid accountNumber", "Validation"), DisconnectCase("17799782", "Disconnect with invalid serviceNumber", "Validation"),
    DisconnectCase("17799783", "Disconnect with null warehouseId", "Positive"), DisconnectCase("17799784", "Disconnect with zero warehouseId", "Validation"),
    DisconnectCase("17799785", "Disconnect with negative warehouseId", "Validation"), DisconnectCase("17799786", "Disconnect with warehouse CanRemove disabled", "Positive"),
    DisconnectCase("17799787", "Disconnect with blank dueDate", "Positive"), DisconnectCase("17799788", "Disconnect with past dueDate", "Validation"),
    DisconnectCase("17799789", "Disconnect with current dueDate", "Positive"), DisconnectCase("17799790", "Disconnect with script comments", "Security"),
    DisconnectCase("17799791", "Disconnect with Unicode comments", "Security"), DisconnectCase("17799792", "Disconnect response requestId UUID", "Positive"),
    DisconnectCase("17799793", "Disconnect requester and dueDate persistence", "Positive"),
    DisconnectCase("17838382", "Service null", "Validation"), DisconnectCase("17838383", "Service empty object", "Validation"), DisconnectCase("17838384", "Requester ID and number mismatch", "Validation"), DisconnectCase("17838385", "Action null", "Validation"), DisconnectCase("17838386", "Action invalid casing", "Validation"), DisconnectCase("17838387", "Comments over 4000 characters", "Validation"),
    DisconnectCase("17838422", "Disconnect with valid provider from another service", "Validation"), DisconnectCase("17838423", "Disconnect with valid account from another provider", "Validation"), DisconnectCase("17838424", "Disconnect with valid service number from another service", "Validation"),
]
CASE_PARAMS = [pytest.param(case, id=f"C{case.case_id}", marks=[pytest.mark.swagger_section("Workorders"), pytest.mark.swagger_operation("POST /v1/workorders/disconnect"), pytest.mark.testrail_case(case.case_id), pytest.mark.dashboard_title(case.title)]) for case in CASES]


def _json(response) -> dict:
    try:
        value = response.json()
        return value if isinstance(value, dict) else {"raw": value}
    except Exception:  # noqa: BLE001
        return {}


def _text(response) -> str:
    value = response.text
    return str(value() if callable(value) else value)


def _status(response) -> int:
    return int(response.status if hasattr(response, "status") else response.status_code)


def _record(request, metrics: list[str]):
    request.node.user_properties.append(("dashboard_metrics", "\n".join(metrics)))


def _employee(db, *, active: bool = True, exclude_id: int | None = None) -> dict:
    status_sql = "= 1" if active else "<> 1"; exclude_sql = "AND EmployeeId <> ?" if exclude_id is not None else ""
    row = db.fetch_one("SELECT TOP 1 EmployeeId AS id, Number AS number, EMail AS email FROM cimpl.Employee WHERE EmployeeStatusId " + status_sql + " AND EMail IS NOT NULL " + exclude_sql + " ORDER BY EmployeeId DESC", (exclude_id,) if exclude_id is not None else ())
    assert row, "No employee seed available"
    return {"id": int(row["id"]), "number": str(row.get("number") or ""), "email": str(row["email"])}


def _service(db, *, status_id: int = 1, open_workorder: bool = False, provider_id: int | None = None, service_type_id: int | None = None) -> dict:
    provider_filter = " AND si.ProviderId=?" if provider_id is not None else ""
    service_type_filter = " AND si.ServiceTypeId=?" if service_type_id is not None else ""
    extra_params = tuple(value for value in (provider_id, service_type_id) if value is not None)
    if open_workorder:
        query = """SELECT TOP 1 si.ServiceInventoryId AS id,a.Number AS accountNumber,si.ProviderId AS providerId,si.PrimaryNumber AS serviceNumber
        FROM cimpl.TransactWorkorderService tws INNER JOIN cimpl.WorkorderSummary ws ON ws.WorkorderId=tws.WorkorderId
        INNER JOIN cimpl.OpenWorkorderStatusView os ON os.WorkorderStatusId=ws.WorkorderStatusId INNER JOIN cimpl.ServiceInventory si ON si.ServiceInventoryId=tws.ServiceInventoryId
        INNER JOIN cimpl.Account a ON a.AccountId=si.AccountId WHERE si.ProviderId IS NOT NULL AND si.PrimaryNumber IS NOT NULL AND a.Number IS NOT NULL""" + provider_filter + service_type_filter + " ORDER BY tws.WorkorderId DESC"
        row = db.fetch_one(query, extra_params)
    else:
        row = db.fetch_one("""SELECT TOP 1 si.ServiceInventoryId AS id,a.Number AS accountNumber,si.ProviderId AS providerId,si.PrimaryNumber AS serviceNumber
        FROM cimpl.ServiceInventory si INNER JOIN cimpl.Account a ON a.AccountId=si.AccountId
        WHERE si.ServiceInventoryStatusId=? AND si.ProviderId IS NOT NULL AND si.PrimaryNumber IS NOT NULL AND a.Number IS NOT NULL
        AND EXISTS (SELECT 1 FROM cimpl.ServiceInventoryHardwareInventoryRelation r WHERE r.ServiceInventoryId=si.ServiceInventoryId)
        """ + ("AND NOT EXISTS (SELECT 1 FROM cimpl.TransactServiceInventory tsi WHERE tsi.TransactId=(SELECT TOP 1 latest.TransactId FROM cimpl.TransactServiceInventory latest WHERE latest.ServiceInventoryId=si.ServiceInventoryId ORDER BY latest.TransactId DESC)) AND NOT EXISTS (SELECT 1 FROM cimpl.TransactWorkorderService tws INNER JOIN cimpl.WorkorderSummary ws ON ws.WorkorderId=tws.WorkorderId INNER JOIN cimpl.WorkorderStatus wos ON wos.WorkorderStatusId=ws.WorkorderStatusId AND wos.TenantId=ws.TenantId WHERE tws.ServiceInventoryId=si.ServiceInventoryId AND tws.WorkorderId=(SELECT TOP 1 latest.WorkorderId FROM cimpl.TransactWorkorderService latest WHERE latest.ServiceInventoryId=si.ServiceInventoryId ORDER BY latest.WorkorderId DESC) AND LOWER(wos.Name) NOT IN ('completed','cancelled','canceled')) " if status_id == 1 else "") + provider_filter + service_type_filter + "ORDER BY si.ServiceInventoryId DESC", (status_id, *extra_params))
    assert row, f"No service seed available for status {status_id} with the selected filters"
    return {key: row[key] for key in row}


def preview_seed_compatibility(db, *, provider_id: int | None = None, service_type_id: int | None = None, limit: int = 2) -> list[dict]:
    provider_filter = " AND si.ProviderId=?" if provider_id is not None else ""
    service_type_filter = " AND si.ServiceTypeId=?" if service_type_id is not None else ""
    params = [limit] + [value for value in (provider_id, service_type_id) if value is not None]
    rows = db.fetch_all(
        """SELECT DISTINCT TOP (?) si.ProviderId AS providerId, p.Name AS providerName, si.ServiceTypeId AS serviceTypeId, st.Name AS serviceTypeName
           FROM cimpl.ServiceInventory si
           INNER JOIN cimpl.Account a ON a.AccountId=si.AccountId
           INNER JOIN cimpl.Provider p ON p.ProviderId=si.ProviderId
           INNER JOIN cimpl.ServiceType st ON st.ServiceTypeId=si.ServiceTypeId
           WHERE si.ServiceInventoryStatusId=1 AND si.ProviderId IS NOT NULL AND si.PrimaryNumber IS NOT NULL AND a.Number IS NOT NULL
             AND EXISTS (SELECT 1 FROM cimpl.ServiceInventoryHardwareInventoryRelation r WHERE r.ServiceInventoryId=si.ServiceInventoryId)
             AND NOT EXISTS (SELECT 1 FROM cimpl.TransactServiceInventory tsi WHERE tsi.TransactId=(SELECT TOP 1 latest.TransactId FROM cimpl.TransactServiceInventory latest WHERE latest.ServiceInventoryId=si.ServiceInventoryId ORDER BY latest.TransactId DESC))"""
        + provider_filter + service_type_filter + " ORDER BY si.ProviderId DESC",
        tuple(params),
    )
    return [
        {"providerId": int(row["providerId"]), "providerName": str(row.get("providerName") or ""), "serviceTypeId": int(row["serviceTypeId"]), "serviceTypeName": str(row.get("serviceTypeName") or "")}
        for row in rows
    ]


def _service_body(service: dict) -> dict:
    return {"id": int(service["id"]), "accountNumber": str(service["accountNumber"]), "providerId": int(service["providerId"]), "serviceNumber": str(service["serviceNumber"])}


def _warehouse(db, *, active: bool = True, can_add: bool = True, can_remove: bool | None = None) -> int:
    remove_sql = "AND CanRemove=?" if can_remove is not None else ""
    params = (1 if active else 0, 1 if can_add else 0, 1 if can_remove else 0) if can_remove is not None else (1 if active else 0, 1 if can_add else 0)
    row = db.fetch_one("SELECT TOP 1 WarehouseId AS id FROM cimpl.Warehouse WHERE Active=? AND CanAdd=? " + remove_sql + " ORDER BY WarehouseId DESC", params)
    assert row, "No warehouse seed available"
    return int(row["id"])


def _payload(db, case: DisconnectCase) -> tuple[dict, dict]:
    marker = f"AUTO-DISCONNECT-C{case.case_id}-{datetime.now():%Y%m%d%H%M%S%f}"
    provider_id = int(os.getenv("TEST_PROVIDER_ID")) if os.getenv("TEST_PROVIDER_ID", "").strip() else None
    service_type_id = int(os.getenv("TEST_SERVICE_TYPE_ID")) if os.getenv("TEST_SERVICE_TYPE_ID", "").strip() else None
    service = _service(db, provider_id=provider_id, service_type_id=service_type_id)
    body = {"requester": _employee(db), "dueDate": (date.today() + timedelta(days=2)).isoformat(), "comments": marker, "service": _service_body(service), "action": "Disconnect", "warehouseId": _warehouse(db)}
    return body, {"service": service, "marker": marker}


def _mutate(case: DisconnectCase, body: dict, db):
    title = case.title.lower()
    if "valid provider from another service" in title or "valid account from another provider" in title or "valid service number from another service" in title:
        other = db.fetch_one("SELECT TOP 1 si.ProviderId AS providerId, a.Number AS accountNumber, si.PrimaryNumber AS serviceNumber FROM cimpl.ServiceInventory si INNER JOIN cimpl.Account a ON a.AccountId=si.AccountId WHERE si.ServiceInventoryId<>? AND si.ServiceInventoryStatusId=1 AND si.ProviderId IS NOT NULL AND si.PrimaryNumber IS NOT NULL AND a.Number IS NOT NULL ORDER BY si.ServiceInventoryId DESC", (body["service"]["id"],))
        assert other, "No alternate service compatibility seed available"
        if "provider" in title: body["service"]["providerId"] = int(other["providerId"])
        elif "account" in title: body["service"]["accountNumber"] = str(other["accountNumber"])
        else: body["service"]["serviceNumber"] = str(other["serviceNumber"])
    elif "service null" in title: body["service"] = None
    elif "service empty" in title: body["service"] = {}
    elif "requester id and number mismatch" in title:
        other = _employee(db, exclude_id=body["requester"]["id"]); body["requester"]["number"] = other["number"]
    elif "action null" in title: body["action"] = None
    elif "action invalid casing" in title: body["action"] = "disconnect"
    elif "comments over 4000" in title: body["comments"] = "C" * 4001
    elif "without warehouse" in title or "missing warehouse" in title: body.pop("warehouseId")
    elif "invalid warehouse" in title: body["warehouseId"] = 999999999
    elif "inactive warehouse" in title: body["warehouseId"] = _warehouse(db, active=False)
    elif "cannot add" in title: body["warehouseId"] = _warehouse(db, can_add=False)
    elif "requester id only" in title: body["requester"] = {"id": body["requester"]["id"]}
    elif "missing requester" in title: body.pop("requester")
    elif "identifiers mismatch" in title:
        other = _employee(db, exclude_id=body["requester"]["id"]); body["requester"]["email"] = other["email"]
    elif "missing service" in title: body.pop("service")
    elif "inactive service" in title: body["service"] = _service_body(_service(db, status_id=2))
    elif "suspended service" in title: body["service"] = _service_body(_service(db, status_id=3))
    elif "spare service" in title: body["service"] = _service_body(_service(db, status_id=4))
    elif "disconnected service" in title: body["service"] = _service_body(_service(db, status_id=7))
    elif "open workorder" in title: body["service"] = _service_body(_service(db, open_workorder=True))
    elif "invalid service" in title: body["service"] = {"id": 999999999}
    elif "invalid duedate" in title: body["dueDate"] = "12/31/2026"
    elif "missing duedate" in title: body.pop("dueDate")
    elif "missing comments" in title: body.pop("comments")
    elif "sql injection" in title: body["comments"] = "' OR 1=1 --"
    elif "without action" in title: body.pop("action")
    elif "lowercase transferofliabilityout" in title: body["action"] = "transferofliabilityout"
    elif "transferofliabilityout action" in title: body["action"] = "TransferOfLiabilityOut"
    elif "invalid action" in title: body["action"] = "InvalidValue"
    elif "requester email only" in title: body["requester"] = {"email": body["requester"]["email"]}
    elif "requester number only" in title: body["requester"] = {"number": body["requester"]["number"]}
    elif "null requester" in title: body["requester"] = None
    elif "empty requester" in title: body["requester"] = {}
    elif "invalid requester id" in title: body["requester"] = {"id": 999999999}
    elif "invalid requester email" in title: body["requester"] = {"email": "abc@"}
    elif "invalid requester number" in title: body["requester"] = {"number": "INVALID"}
    elif "inactive requester" in title: body["requester"] = _employee(db, active=False)
    elif "service id only" in title: body["service"] = {"id": body["service"]["id"]}
    elif "id and providerid only" in title: body["service"] = {"id": body["service"]["id"], "providerId": body["service"]["providerId"]}
    elif "missing service providerid" in title: body["service"].pop("providerId")
    elif "missing service accountnumber" in title: body["service"].pop("accountNumber")
    elif "missing service servicenumber" in title: body["service"].pop("serviceNumber")
    elif "invalid providerid" in title: body["service"]["providerId"] = 999999999
    elif "invalid accountnumber" in title: body["service"]["accountNumber"] = "INVALID"
    elif "invalid servicenumber" in title: body["service"]["serviceNumber"] = "INVALID"
    elif "null warehouse" in title: body["warehouseId"] = None
    elif "zero warehouse" in title: body["warehouseId"] = 0
    elif "negative warehouse" in title: body["warehouseId"] = -1
    elif "canremove disabled" in title: body["warehouseId"] = _warehouse(db, can_add=False, can_remove=False)
    elif "blank duedate" in title: body["dueDate"] = ""
    elif "past duedate" in title: body["dueDate"] = (date.today() - timedelta(days=1)).isoformat()
    elif "current duedate" in title: body["dueDate"] = date.today().isoformat()
    elif "script comments" in title: body["comments"] = "<script>alert(1)</script>"
    elif "unicode comments" in title: body["comments"] = "\u65e5\u672c\u8a9e \u0627\u0644\u0639\u0631\u0628\u064a\u0629 \U0001f600"


class TestPostWorkordersDisconnect:
    @pytest.mark.parametrize("case", CASE_PARAMS)
    def test_post_workorders_disconnect(self, case, api_context, db, request):
        body, context = _payload(db, case); _mutate(case, body, db)
        scripts = PostApiDbScripts(db); marker = str(body.get("comments") or "")
        before = scripts.count_rows(table="cimpl.WorkorderSummary", where_sql="Comment LIKE ?", params=(f"%{marker}%",)) if marker else None
        metrics = [f"CaseId: {case.case_id}", "Service: " + json.dumps(context["service"], default=str), f"Request body: {json.dumps(body, ensure_ascii=True)}"]
        response = requests.post(f"{os.environ['BASE_URL'].rstrip('/')}/workorders/disconnect", json=body, headers={"accept":"application/json"}, timeout=int(os.getenv("API_REQUEST_TIMEOUT_MS","120000"))/1000) if case.label == "Authorization" else api_context.post("workorders/disconnect", data=json.dumps(body), headers={"Content-Type":"application/json","accept":"application/json"})
        try:
            payload = _json(response)
            if case.label in {"Positive", "Security"}:
                assert _status(response) in {200,201}, f"{case.title}: expected success, got {_status(response)}: {_text(response)}"
                number=str(payload.get("workorderNumber") or "").strip(); assert number, f"{case.title}: missing workorderNumber"
                rows=db.fetch_all("""SELECT tws.TransactId AS transactId,tsi.TransactActionTypeId AS serviceActionId,tsi.OperationTypeId AS operationTypeId,tsi.ExecuteOperationTypeId AS executeOperationTypeId,r.HardwareInventoryId AS hardwareId,r.TransactActionTypeId AS relationActionId,th.TransactActionTypeId AS hardwareActionId,th.WarehouseId AS warehouseId,th.IncludeInWorkorder AS includeInWorkorder,ws.DueDate AS dueDate,wr.EmployeeId AS requesterEmployeeId
                FROM cimpl.Workorder w INNER JOIN cimpl.WorkorderSummary ws ON ws.WorkorderId=w.WorkorderId LEFT JOIN cimpl.WorkorderRequester wr ON wr.WorkorderRequesterId=ws.WorkorderRequesterId INNER JOIN cimpl.TransactWorkorderService tws ON tws.WorkorderId=w.WorkorderId INNER JOIN cimpl.TransactServiceInventory tsi ON tsi.TransactId=tws.TransactId AND tsi.ServiceInventoryId=tws.ServiceInventoryId LEFT JOIN cimpl.TransactServiceInventoryHardwareInventoryRelation r ON r.TransactId=tws.TransactId AND r.ServiceInventoryId=tws.ServiceInventoryId LEFT JOIN cimpl.TransactHardwareInventory th ON th.TransactId=tws.TransactId AND th.HardwareInventoryId=r.HardwareInventoryId WHERE w.Number=?""",(number,))
                assert rows, f"{case.title}: no pending transaction rows for returned workorder {number}"
                if "requestid uuid" in case.title.lower():
                    import uuid
                    assert str(uuid.UUID(str(payload.get("requestId") or ""))), f"{case.title}: response requestId is not a UUID"
                if "requester and duedate persistence" in case.title.lower():
                    assert int(rows[0]["requesterEmployeeId"]) == int(body["requester"]["id"]), f"{case.title}: requester not persisted"
                    assert rows[0]["dueDate"].date().isoformat() == body["dueDate"], f"{case.title}: dueDate not persisted"
                if "transferofliabilityout" not in case.title.lower():
                    assert all(int(row["serviceActionId"]) == 7 and int(row["operationTypeId"]) == 11 and int(row["executeOperationTypeId"]) == 11 for row in rows), f"{case.title}: expected default Disconnect action 7 and operation 11"
                else:
                    assert all(int(row["serviceActionId"]) == 7 and int(row["operationTypeId"]) == 115 and int(row["executeOperationTypeId"]) == 115 for row in rows), f"{case.title}: expected TransferOfLiabilityOut operation 115"
                if body.get("warehouseId") is None:
                    assert all(int(row["relationActionId"]) == 7 and int(row["hardwareActionId"]) == 4 and row["warehouseId"] is None for row in rows), f"{case.title}: expected pending hardware deletion with no warehouse"
                else: assert any(int(row["warehouseId"]) == int(body["warehouseId"]) for row in rows if row["warehouseId"] is not None), f"{case.title}: warehouse was not assigned"
                metrics.extend([f"Response status: {_status(response)}",f"Response payload: {json.dumps(payload)}",f"DB validation: {json.dumps(rows,default=str)}"])
            elif case.label == "Authorization": assert _status(response) in {401,403}, f"{case.title}: expected 401/403, got {_status(response)}"
            else:
                assert _status(response) in {400,404,406,409,500}, f"{case.title}: expected validation error, got {_status(response)}: {_text(response)}"
                if marker and before is not None:
                    after=scripts.count_rows(table="cimpl.WorkorderSummary",where_sql="Comment LIKE ?",params=(f"%{marker}%",)); scripts.assert_count_delta(before=before,after=after,expected_delta=0,context=case.title)
                metrics.extend([f"Response status: {_status(response)}",f"Response payload: {json.dumps(payload)}"])
        finally:
            if sys.exc_info()[0] is not None: metrics.extend([f"Response status: {_status(response)}",f"Response body: {_text(response)}"])
            _record(request,metrics)