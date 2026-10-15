"""REQ07 coverage for POST /requestApi/SelfServiceRequests/Activate (OperationTypeId=14)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID

import pytest
import requests

from utils.post_api_db_scripts import PostApiDbScripts


SELF_SERVICE_URL = os.getenv("SELF_SERVICE_BASE_URL", "https://cplca1webq01.uplandsoftware.net/requestApi").rstrip("/") + "/SelfServiceRequests/Activate"
OPERATION_TYPE_ID = 14


@dataclass(frozen=True)
class ActivateCase:
    case_id: str
    title: str
    label: str


_CASE_DATA = [
    ("17839542", "Create Activate request with compatible data", "Positive"),
    ("17839543", "Service ID only", "Positive"),
    ("17839544", "Service account number, provider ID, service number only", "Positive"),
    ("17839545", "Requester ID only", "Positive"),
    ("17839546", "Requester number only", "Positive"),
    ("17839547", "Requester email only", "Positive"),
    ("17839548", "Missing service", "Validation"),
    ("17839549", "Null service", "Validation"),
    ("17839550", "Invalid service", "Validation"),
    ("17839551", "Service account number mismatch", "Validation"),
    ("17839552", "Service provider ID mismatch", "Validation"),
    ("17839553", "Service number mismatch", "Validation"),
    ("17839554", "Service not suspended", "Validation"),
    ("17839555", "Service with open workorder", "Validation"),
    ("17839556", "Service with open transaction", "Validation"),
    ("17839557", "Service with pending self service approval", "Validation"),
    ("17839558", "Missing requester", "Validation"),
    ("17839559", "Null requester", "Validation"),
    ("17839560", "Invalid requester", "Validation"),
    ("17839561", "Requester identifiers mismatch", "Validation"),
    ("17839562", "Inactive requester", "Validation"),
    ("17839563", "Due date omitted", "Validation"),
    ("17839564", "Due date blank", "Validation"),
    ("17839565", "Due date null", "Validation"),
    ("17839566", "Due date past", "Validation"),
    ("17839567", "Due date invalid calendar date", "Validation"),
    ("17839568", "Due date future", "Positive"),
    ("17839569", "Comments omitted", "Positive"),
    ("17839570", "Comments null", "Positive"),
    ("17839571", "Comments blank", "Positive"),
    ("17839572", "Comments SQL injection input", "Positive"),
    ("17839573", "Comments XSS input", "Positive"),
    ("17839574", "Unauthorized request", "Authorization"),
]

CASES = [ActivateCase(case_id, title, label) for case_id, title, label in _CASE_DATA]
CASE_PARAMS = [
    pytest.param(
        case,
        id=f"C{case.case_id}",
        marks=[
            pytest.mark.swagger_section("Self Service Requests"),
            pytest.mark.swagger_operation("POST /requestApi/SelfServiceRequests/Activate"),
            pytest.mark.testrail_case(case.case_id),
            pytest.mark.dashboard_title(case.title),
        ],
    )
    for case in CASES
]


def _json(response) -> dict:
    try:
        value = response.json()
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}


def _employee(db, *, active: bool = True, exclude_id: int | None = None) -> dict:
    row = db.fetch_one(
        "SELECT TOP 1 EmployeeId AS id,Number AS number,EMail AS email FROM cimpl.Employee WHERE EmployeeStatusId "
        + ("=1" if active else "<>1")
        + " AND EMail IS NOT NULL "
        + ("AND EmployeeId<>?" if exclude_id else "")
        + " ORDER BY EmployeeId DESC",
        (exclude_id,) if exclude_id else (),
    )
    assert row, "No employee seed available"
    return {"id": int(row["id"]), "number": str(row.get("number") or ""), "email": str(row["email"])}


_USED_SERVICE_IDS: set[int] = set()


def _seed(db, *, status_id: int = 3) -> dict:
    rows = db.fetch_all(
        """SELECT TOP 200 si.ServiceInventoryId AS service_id, a.Number AS account_number, si.ProviderId AS provider_id, si.PrimaryNumber AS service_number
        FROM cimpl.ServiceInventory si
        INNER JOIN cimpl.Account a ON a.AccountId=si.AccountId
        WHERE si.ServiceInventoryStatusId=? AND si.ProviderId IS NOT NULL AND si.PrimaryNumber IS NOT NULL AND a.Number IS NOT NULL
                    AND NOT EXISTS (
                            SELECT 1 FROM cimpl.TransactServiceInventory tsi
                            WHERE tsi.TransactId=(SELECT TOP 1 tsi_latest.TransactId FROM cimpl.TransactServiceInventory tsi_latest WHERE tsi_latest.ServiceInventoryId=si.ServiceInventoryId ORDER BY tsi_latest.TransactId DESC)
                                AND tsi.TransactStatusId=1 AND tsi.OperationTypeId=211
                    )
          AND NOT EXISTS (
              SELECT 1 FROM cimpl.TransactWorkorderService tws
              INNER JOIN cimpl.WorkorderSummary ws ON ws.WorkorderId=tws.WorkorderId
                              INNER JOIN cimpl.WorkorderStatus wos ON wos.WorkorderStatusId=ws.WorkorderStatusId AND wos.TenantId=ws.TenantId
                            WHERE tws.ServiceInventoryId=si.ServiceInventoryId
                                AND tws.WorkorderId=(SELECT TOP 1 tws_latest.WorkorderId FROM cimpl.TransactWorkorderService tws_latest WHERE tws_latest.ServiceInventoryId=si.ServiceInventoryId ORDER BY tws_latest.WorkorderId DESC)
                                AND LOWER(wos.Name) NOT IN ('completed','cancelled','canceled')
          )
          AND NOT EXISTS (SELECT 1 FROM cimpl.SelfServiceActionLog ssl WHERE ssl.ServiceInventoryId=si.ServiceInventoryId AND ssl.StatusId=1 AND ssl.WorkorderId IS NULL AND ssl.UpdatedDate IS NULL)
        ORDER BY si.ServiceInventoryId DESC""",
        (status_id,),
    )
    row = next((candidate for candidate in rows if int(candidate["service_id"]) not in _USED_SERVICE_IDS), None)
    assert row, f"No eligible service available for status {status_id} (all top candidates already used in this run)"
    _USED_SERVICE_IDS.add(int(row["service_id"]))
    return {
        "serviceId": int(row["service_id"]),
        "accountNumber": str(row["account_number"]),
        "providerId": int(row["provider_id"]),
        "serviceNumber": str(row["service_number"]),
    }


def preview_seed_compatibility(db, *, provider_id: int | None = None, service_type_id: int | None = None, limit: int = 2) -> list[dict]:
    """Activate has no catalog/contract compatibility rules; any suspended, open-workorder/approval/active-transaction-free service qualifies."""
    provider_filter = " AND si.ProviderId=?" if provider_id is not None else ""
    service_type_filter = " AND si.ServiceTypeId=?" if service_type_id is not None else ""
    params = [limit] + [value for value in (provider_id, service_type_id) if value is not None]
    rows = db.fetch_all(
        """SELECT DISTINCT TOP (?) si.ProviderId AS providerId, p.Name AS providerName, si.ServiceTypeId AS serviceTypeId, st.Name AS serviceTypeName
        FROM cimpl.ServiceInventory si
        INNER JOIN cimpl.Account a ON a.AccountId=si.AccountId
        INNER JOIN cimpl.Provider p ON p.ProviderId=si.ProviderId
        INNER JOIN cimpl.ServiceType st ON st.ServiceTypeId=si.ServiceTypeId
        WHERE si.ServiceInventoryStatusId=3 AND si.ProviderId IS NOT NULL AND si.PrimaryNumber IS NOT NULL AND a.Number IS NOT NULL
                    AND NOT EXISTS (
                            SELECT 1 FROM cimpl.TransactServiceInventory tsi
                            WHERE tsi.TransactId=(SELECT TOP 1 tsi_latest.TransactId FROM cimpl.TransactServiceInventory tsi_latest WHERE tsi_latest.ServiceInventoryId=si.ServiceInventoryId ORDER BY tsi_latest.TransactId DESC)
                                AND tsi.TransactStatusId=1 AND tsi.OperationTypeId=211
                    )"""
        + provider_filter + service_type_filter +
        """ ORDER BY si.ProviderId DESC""",
        tuple(params),
    )
    return [
        {"providerId": int(row["providerId"]), "providerName": str(row.get("providerName") or ""), "serviceTypeId": int(row["serviceTypeId"]), "serviceTypeName": str(row.get("serviceTypeName") or "")}
        for row in rows
    ]


def _service_body(seed: dict) -> dict:
    return {
        "id": int(seed["serviceId"]),
        "accountNumber": str(seed["accountNumber"]),
        "providerId": int(seed["providerId"]),
        "serviceNumber": str(seed["serviceNumber"]),
    }


def _not_suspended_service_id(db) -> int:
    value = db.fetch_scalar("SELECT TOP 1 ServiceInventoryId FROM cimpl.ServiceInventory WHERE ServiceInventoryStatusId<>3 ORDER BY ServiceInventoryId DESC")
    assert value, "No non-suspended service available"
    return int(value)


def _in_transaction_service_body(db):
    """Finds a suspended service with an Active (TransactStatusId=1) pending transaction - this DOES block Activate (unlike Suspend)."""
    rows = db.fetch_all(
        """SELECT TOP 20 si.ServiceInventoryId AS service_id, a.Number AS account_number, si.PrimaryNumber AS service_number, si.ProviderId AS provider_id
        FROM cimpl.ServiceInventory si
        INNER JOIN cimpl.Account a ON a.AccountId=si.AccountId
        INNER JOIN cimpl.TransactServiceInventory tsi ON tsi.ServiceInventoryId=si.ServiceInventoryId AND tsi.TransactStatusId=1
        WHERE si.ServiceInventoryStatusId=3 AND si.ProviderId IS NOT NULL AND si.PrimaryNumber IS NOT NULL AND a.Number IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM cimpl.SelfServiceActionLog ssl WHERE ssl.ServiceInventoryId=si.ServiceInventoryId AND ssl.StatusId=1 AND ssl.WorkorderId IS NULL AND ssl.UpdatedDate IS NULL)
          AND NOT EXISTS (
              SELECT 1 FROM cimpl.TransactWorkorderService tws
              INNER JOIN cimpl.WorkorderSummary ws ON ws.WorkorderId=tws.WorkorderId
                              INNER JOIN cimpl.WorkorderStatus wos ON wos.WorkorderStatusId=ws.WorkorderStatusId AND wos.TenantId=ws.TenantId
                            WHERE tws.ServiceInventoryId=si.ServiceInventoryId
                                AND tws.WorkorderId=(SELECT TOP 1 tws_latest.WorkorderId FROM cimpl.TransactWorkorderService tws_latest WHERE tws_latest.ServiceInventoryId=si.ServiceInventoryId ORDER BY tws_latest.WorkorderId DESC)
                                AND LOWER(wos.Name) NOT IN ('completed','cancelled','canceled')
          )
        ORDER BY si.ServiceInventoryId DESC"""
    )
    row = next((candidate for candidate in rows if int(candidate["service_id"]) not in _USED_SERVICE_IDS), None)
    if not row:
        return None
    _USED_SERVICE_IDS.add(int(row["service_id"]))
    return {"id": int(row["service_id"]), "accountNumber": str(row["account_number"]), "providerId": int(row["provider_id"]), "serviceNumber": str(row["service_number"])}


def _open_workorder_service_body(db) -> dict:
    row = db.fetch_one(
        """SELECT TOP 1 si.ServiceInventoryId AS service_id, a.Number AS account_number, si.PrimaryNumber AS service_number, si.ProviderId AS provider_id
        FROM cimpl.TransactWorkorderService tws
        INNER JOIN cimpl.WorkorderSummary ws ON ws.WorkorderId=tws.WorkorderId
        INNER JOIN cimpl.OpenWorkorderStatusView os ON os.WorkorderStatusId=ws.WorkorderStatusId
        INNER JOIN cimpl.ServiceInventory si ON si.ServiceInventoryId=tws.ServiceInventoryId
        INNER JOIN cimpl.Account a ON a.AccountId=si.AccountId
        WHERE si.ProviderId IS NOT NULL AND si.PrimaryNumber IS NOT NULL AND a.Number IS NOT NULL
        ORDER BY tws.WorkorderId DESC"""
    )
    assert row, "No service with an open workorder available"
    return {"id": int(row["service_id"]), "accountNumber": str(row["account_number"]), "providerId": int(row["provider_id"]), "serviceNumber": str(row["service_number"])}


def _pending_approval_service_body(db) -> dict:
    row = db.fetch_one(
        """SELECT TOP 1 si.ServiceInventoryId AS service_id, a.Number AS account_number, si.PrimaryNumber AS service_number, si.ProviderId AS provider_id
        FROM cimpl.ServiceInventory si
        INNER JOIN cimpl.Account a ON a.AccountId=si.AccountId
        INNER JOIN cimpl.SelfServiceActionLog ssl ON ssl.ServiceInventoryId=si.ServiceInventoryId AND ssl.StatusId=1 AND ssl.WorkorderId IS NULL AND ssl.UpdatedDate IS NULL
        WHERE si.ProviderId IS NOT NULL AND si.PrimaryNumber IS NOT NULL AND a.Number IS NOT NULL
        ORDER BY si.ServiceInventoryId DESC"""
    )
    assert row, "No service with a pending Self Service approval available"
    return {"id": int(row["service_id"]), "accountNumber": str(row["account_number"]), "providerId": int(row["provider_id"]), "serviceNumber": str(row["service_number"])}


def _payload(db, case: ActivateCase) -> tuple[dict, dict]:
    seed = _seed(db)
    marker = f"AUTO-SELF-SERVICE-REQ07-C{case.case_id}"
    body = {
        "service": _service_body(seed),
        "requester": _employee(db),
        "dueDate": (date.today() + timedelta(days=2)).isoformat(),
        "comments": marker,
    }
    return body, {"seed": seed, "marker": marker}


def _mutate(case: ActivateCase, body: dict, context: dict, db):
    title = case.title.lower()

    if "service id only" in title:
        body["service"] = {"id": body["service"]["id"]}
    elif "account number, provider id, service number only" in title:
        service = body["service"]
        body["service"] = {"accountNumber": service["accountNumber"], "providerId": service["providerId"], "serviceNumber": service["serviceNumber"]}
    elif "missing service" in title:
        body.pop("service", None)
    elif "null service" in title:
        body["service"] = None
    elif "invalid service" in title:
        body["service"]["id"] = 999999999
    elif "service account number mismatch" in title:
        body["service"]["accountNumber"] = "INVALID-ACCOUNT"
    elif "service provider id mismatch" in title:
        body["service"]["providerId"] = 999999999
    elif "service number mismatch" in title:
        body["service"]["serviceNumber"] = "INVALID-SERVICE"
    elif "service not suspended" in title:
        body["service"]["id"] = _not_suspended_service_id(db)
    elif "open workorder" in title:
        body["service"] = _open_workorder_service_body(db)
    elif "open transaction" in title:
        service = _in_transaction_service_body(db)
        if service is None:
            pytest.skip("No suspended service with an Active pending transaction is available")
        body["service"] = service
    elif "pending self service approval" in title:
        body["service"] = _pending_approval_service_body(db)
    elif "requester id only" in title:
        body["requester"] = {"id": body["requester"]["id"]}
    elif "requester number only" in title:
        body["requester"] = {"number": body["requester"]["number"]}
    elif "requester email only" in title:
        body["requester"] = {"email": body["requester"]["email"]}
    elif "inactive requester" in title:
        body["requester"] = _employee(db, active=False)
    elif "missing requester" in title:
        body.pop("requester", None)
    elif "null requester" in title:
        body["requester"] = None
    elif "invalid requester" in title:
        body["requester"] = {"id": 999999999}
    elif "requester identifiers mismatch" in title:
        body["requester"]["email"] = _employee(db, exclude_id=body["requester"]["id"])["email"]
    elif "due date omitted" in title:
        body.pop("dueDate", None)
    elif "due date blank" in title:
        body["dueDate"] = ""
    elif "due date null" in title:
        body["dueDate"] = None
    elif "due date past" in title:
        body["dueDate"] = (date.today() - timedelta(days=1)).isoformat()
    elif "due date invalid calendar" in title:
        body["dueDate"] = "2026-02-30"
    elif "due date future" in title:
        body["dueDate"] = (date.today() + timedelta(days=10)).isoformat()
    elif "comments omitted" in title:
        body.pop("comments", None)
    elif "comments null" in title:
        body["comments"] = None
    elif "comments blank" in title:
        body["comments"] = ""
    elif "comments sql injection" in title:
        body["comments"] = "'; DROP TABLE SelfServiceActionLog;--"
    elif "comments xss" in title:
        body["comments"] = "<script>alert('x')</script>"


def _log(db, number: str):
    return db.fetch_one(
        "SELECT TOP 1 SelfServiceActionLogId,RequestId,Number,StatusId,OperationTypeId,ServiceInventoryId,RequesterEmployeeId,DueDate,Comment,ActionDetail FROM cimpl.SelfServiceActionLog WHERE Number=? ORDER BY SelfServiceActionLogId DESC",
        (number,),
    )


class TestPostSelfServiceActivate:
    @pytest.mark.parametrize("case", CASE_PARAMS)
    def test_post_self_service_activate(self, case: ActivateCase, api_token, db, request):
        body, context = _payload(db, case)
        _mutate(case, body, context, db)

        scripts = PostApiDbScripts(db)
        marker = context["marker"]
        before = scripts.count_rows(table="cimpl.SelfServiceActionLog", where_sql="Comment LIKE ?", params=(f"%{marker}%",))

        headers = {} if case.label == "Authorization" else {"Authorization": f"Bearer {api_token}", "Accept": "application/json", "Content-Type": "application/json"}
        response = requests.post(SELF_SERVICE_URL, json=body, headers=headers, timeout=120)
        payload = _json(response)

        metrics = [f"CaseId: {case.case_id}", f"Request body: {json.dumps(body, default=str)}", f"OperationTypeId: {OPERATION_TYPE_ID}"]
        try:
            if case.label == "Positive":
                assert response.status_code in {200, 201}, f"Expected success, got {response.status_code}: {response.text}"
                number = str(payload.get("selfServiceRequestNumber") or "")
                UUID(str(payload.get("trackingId") or ""))
                row = _log(db, number)
                assert row and int(row["OperationTypeId"]) == OPERATION_TYPE_ID
                metrics.extend([f"Response: {json.dumps(payload, default=str)}", f"SelfServiceActionLog: {json.dumps(row, default=str)}"])
            elif case.label == "Authorization":
                assert response.status_code in {401, 403}
            else:
                assert response.status_code in {400, 404, 409, 500}, f"Expected failure, got {response.status_code}: {response.text}"
                after = scripts.count_rows(table="cimpl.SelfServiceActionLog", where_sql="Comment LIKE ?", params=(f"%{marker}%",))
                scripts.assert_count_delta(before=before, after=after, expected_delta=0, context=case.title)
                metrics.append(f"Response: {response.text}")
        finally:
            metrics.append(f"Payload: {json.dumps(payload, default=str)}")
            request.node.user_properties.append(("dashboard_metrics", "\n".join(metrics)))
