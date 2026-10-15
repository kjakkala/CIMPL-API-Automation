"""Data-driven validation for POST /v1/workorders/transfer-cost-center."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pytest
import requests

from utils.post_api_db_scripts import PostApiDbScripts
from utils.organization_types import cost_center_type_sql


@dataclass(frozen=True)
class CostCenterCase:
    case_id: str
    title: str
    expected: str
    label: str


CASES = [
    CostCenterCase("17798154", "Create transfer CostCenter workorder with valid data", "201 Created", "Positive"),
    CostCenterCase("17798155", "Create transfer CostCenter workorder with required fields only", "201 Created", "Positive"),
    CostCenterCase("17798156", "Create transfer CostCenter workorder with dueDate", "201 Created", "Positive"),
    CostCenterCase("17798157", "Create transfer CostCenter workorder with comments", "201 Created", "Positive"),
    CostCenterCase("17798158", "Missing newCostCenterOrgId", "Validation error", "Validation"),
    CostCenterCase("17798159", "newCostCenterOrgId is null", "Validation error", "Validation"),
    CostCenterCase("17798160", "newCostCenterOrgId does not exist", "Validation error", "Validation"),
    CostCenterCase("17798161", "newCostCenterOrgId is inactive", "Validation error", "Validation"),
    CostCenterCase("17798162", "newCostCenterOrgId is not a CostCenter", "Validation error", "Validation"),
    CostCenterCase("17798163", "newCostCenterOrgId equals current CostCenter", "Validation error", "Validation"),
    CostCenterCase("17798164", "Missing service", "Validation error", "Validation"),
    CostCenterCase("17798165", "Service is inactive", "Validation error", "Validation"),
    CostCenterCase("17798166", "Service has an open workorder", "Validation error", "Validation"),
    CostCenterCase("17798167", "Invalid service identifier", "Validation error", "Validation"),
    CostCenterCase("17798168", "Missing requester", "Validation error", "Validation"),
    CostCenterCase("17798169", "Requester is null", "Validation error", "Validation"),
    CostCenterCase("17798170", "Invalid requester", "Validation error", "Validation"),
    CostCenterCase("17798171", "Invalid dueDate format", "Validation error", "Validation"),
    CostCenterCase("17798172", "SQL injection in comments", "201 Created", "Security"),
    CostCenterCase("17798173", "Script injection in comments", "201 Created", "Security"),
    CostCenterCase("17798174", "Unauthorized transfer CostCenter request", "401/403", "Authorization"),
    CostCenterCase("17798177", "Requester ID only", "201 Created", "Positive"),
    CostCenterCase("17798178", "Requester email only", "Validation error", "Validation"),
    CostCenterCase("17798179", "Requester number only", "Validation error", "Validation"),
    CostCenterCase("17798180", "Requester email does not match requester ID", "Validation error", "Validation"),
    CostCenterCase("17798181", "Requester number does not match requester ID", "Validation error", "Validation"),
    CostCenterCase("17798182", "Inactive requester", "Validation error", "Validation"),
    CostCenterCase("17798183", "Service ID without providerId", "Validation error", "Validation"),
    CostCenterCase("17798184", "Service ID and providerId only", "201 Created", "Positive"),
    CostCenterCase("17798185", "Service primary number without all identifiers", "Validation error", "Validation"),
    CostCenterCase("17798186", "Missing service providerId", "Validation error", "Validation"),
    CostCenterCase("17798187", "Missing service accountNumber", "Validation error", "Validation"),
    CostCenterCase("17798188", "Missing service serviceNumber", "Validation error", "Validation"),
    CostCenterCase("17798189", "Closed CostCenter", "Validation error", "Validation"),
    CostCenterCase("17798190", "Negative newCostCenterOrgId", "Validation error", "Validation"),
    CostCenterCase("17798191", "Zero newCostCenterOrgId", "Validation error", "Validation"),
    CostCenterCase("17798192", "Past dueDate", "Validation error", "Validation"),
    CostCenterCase("17798193", "Current-date dueDate", "Validation error", "Validation"),
    CostCenterCase("17798194", "Comments length 250", "201 Created", "Boundary"),
    CostCenterCase("17798195", "Comments length 600", "201 Created", "Boundary"),
    CostCenterCase("17798196", "Comments length 1000", "201 Created", "Boundary"),
    CostCenterCase("17798197", "Comments length 5000", "500 error and no workorder created", "Boundary"),
    CostCenterCase("17798198", "Verify pending transaction operation type", "201 Created", "Positive"),
    CostCenterCase("17798199", "Comments length 1500", "201 Created", "Boundary"),
    CostCenterCase("17798200", "Comments length 2500", "201 Created", "Boundary"),
    CostCenterCase("17798201", "Comments length 4000", "500 error and no workorder created", "Boundary"),
    CostCenterCase("17798202", "Comments length 4500", "500 error and no workorder created", "Boundary"),
    CostCenterCase("17838388", "Service null", "Validation error", "Validation"), CostCenterCase("17838389", "Service empty object", "Validation error", "Validation"), CostCenterCase("17838390", "Service ID only", "Validation error", "Validation"), CostCenterCase("17838391", "Service providerId only", "Validation error", "Validation"), CostCenterCase("17838392", "Service accountNumber only", "Validation error", "Validation"), CostCenterCase("17838393", "Service serviceNumber only", "Validation error", "Validation"),
    CostCenterCase("17838416", "Service with valid provider from another service", "Validation error", "Validation"), CostCenterCase("17838417", "Service with valid account from another provider", "Validation error", "Validation"), CostCenterCase("17838418", "Service with valid service number from another service", "Validation error", "Validation"),
]

CASE_PARAMS = [
    pytest.param(case, id=f"C{case.case_id}", marks=[
        pytest.mark.swagger_section("Workorders"),
        pytest.mark.swagger_operation("POST /v1/workorders/transfer-cost-center"),
        pytest.mark.testrail_case(case.case_id),
        pytest.mark.dashboard_title(case.title),
    ])
    for case in CASES
]


def _safe_json(response) -> dict:
    try:
        value = response.json()
        return value if isinstance(value, dict) else {"raw": value}
    except Exception:  # noqa: BLE001
        return {}


def _response_text(response) -> str:
    value = response.text
    return str(value() if callable(value) else value)


def _status(response) -> int:
    return int(response.status if hasattr(response, "status") else response.status_code)


def _record_metrics(request, metrics: list[str]):
    request.node.user_properties.append(("dashboard_metrics", "\n".join(metrics)))


def _employee(db, *, active: bool = True, exclude_id: int | None = None) -> dict:
    status_sql = "= 1" if active else "<> 1"
    exclude_sql = "AND EmployeeId <> ?" if exclude_id is not None else ""
    params = (exclude_id,) if exclude_id is not None else ()
    row = db.fetch_one("""SELECT TOP 1 EmployeeId AS id, Number AS number, EMail AS email
        FROM cimpl.Employee WHERE EmployeeStatusId """ + status_sql + " AND EMail IS NOT NULL " + exclude_sql + " ORDER BY EmployeeId DESC", params)
    assert row, "No active requester employee available"
    return {"id": int(row["id"]), "number": str(row.get("number") or ""), "email": str(row["email"])}


def _cost_center(db, *, active: bool = True, correct_type: bool = True, open_status: bool = True, exclude_id: int | None = None) -> int:
    type_sql = cost_center_type_sql("o.OrganizationTypeId", required=correct_type)
    exclude_sql = "AND o.OrganizationId <> ?" if exclude_id is not None else ""
    params = (exclude_id,) if exclude_id is not None else ()
    row = db.fetch_one(
        """SELECT TOP 1 o.OrganizationId AS id FROM cimpl.Organization o
              WHERE o.Active = ? AND o.OrganizationStatusId = ? AND """ + type_sql + " " + exclude_sql + " ORDER BY o.OrganizationId DESC",
          (1 if active else 0, 1 if open_status else 2, *params),
    )
    assert row, "No CostCenter organization available for scenario"
    return int(row["id"])


def _service(db, *, inactive: bool = False, open_workorder: bool = False, current_cost_center: bool = False, provider_id: int | None = None, service_type_id: int | None = None) -> dict:
    provider_filter = " AND si.ProviderId=?" if provider_id is not None else ""
    service_type_filter = " AND si.ServiceTypeId=?" if service_type_id is not None else ""
    extra_params = tuple(value for value in (provider_id, service_type_id) if value is not None)
    if open_workorder:
        query = """SELECT TOP 1 si.ServiceInventoryId AS id, a.Number AS accountNumber, si.ProviderId AS providerId,
            si.PrimaryNumber AS serviceNumber, si.CostCenterId AS currentCostCenterId
            FROM cimpl.TransactWorkorderService tws INNER JOIN cimpl.WorkorderSummary ws ON ws.WorkorderId = tws.WorkorderId
            INNER JOIN cimpl.OpenWorkorderStatusView os ON os.WorkorderStatusId = ws.WorkorderStatusId
            INNER JOIN cimpl.ServiceInventory si ON si.ServiceInventoryId = tws.ServiceInventoryId
            INNER JOIN cimpl.Account a ON a.AccountId = si.AccountId
            WHERE si.ProviderId IS NOT NULL AND si.PrimaryNumber IS NOT NULL AND a.Number IS NOT NULL""" + provider_filter + service_type_filter + " ORDER BY tws.WorkorderId DESC"
        row = db.fetch_one(query, extra_params)
    else:
        status_sql = "<> 1" if inactive else "= 1"
        current_sql = "AND si.CostCenterId IS NOT NULL" if current_cost_center else ""
        row = db.fetch_one(
            """SELECT TOP 1 si.ServiceInventoryId AS id, a.Number AS accountNumber, si.ProviderId AS providerId,
                si.PrimaryNumber AS serviceNumber, si.CostCenterId AS currentCostCenterId
                FROM cimpl.ServiceInventory si INNER JOIN cimpl.Account a ON a.AccountId = si.AccountId
                                WHERE si.ServiceInventoryStatusId """ + status_sql + """ AND si.ProviderId IS NOT NULL AND si.PrimaryNumber IS NOT NULL
                                    AND a.Number IS NOT NULL AND NOT EXISTS (SELECT 1 FROM cimpl.TransactServiceInventory tsi WHERE tsi.TransactId = (SELECT TOP 1 latest.TransactId FROM cimpl.TransactServiceInventory latest WHERE latest.ServiceInventoryId = si.ServiceInventoryId ORDER BY latest.TransactId DESC)) AND NOT EXISTS (SELECT 1 FROM cimpl.TransactWorkorderService tws INNER JOIN cimpl.WorkorderSummary ws ON ws.WorkorderId=tws.WorkorderId INNER JOIN cimpl.WorkorderStatus wos ON wos.WorkorderStatusId=ws.WorkorderStatusId AND wos.TenantId=ws.TenantId WHERE tws.ServiceInventoryId=si.ServiceInventoryId AND tws.WorkorderId=(SELECT TOP 1 latest.WorkorderId FROM cimpl.TransactWorkorderService latest WHERE latest.ServiceInventoryId=si.ServiceInventoryId ORDER BY latest.WorkorderId DESC) AND LOWER(wos.Name) NOT IN ('completed','cancelled','canceled')) """ + current_sql + provider_filter + service_type_filter + " ORDER BY si.ServiceInventoryId DESC",
            extra_params,
        )
    assert row, "No service available for CostCenter transfer scenario with the selected filters"
    return {key: row[key] for key in row}


def preview_seed_compatibility(db, *, provider_id: int | None = None, service_type_id: int | None = None, limit: int = 2) -> list[dict]:
    provider_filter = " AND si.ProviderId=?" if provider_id is not None else ""
    service_type_filter = " AND si.ServiceTypeId=?" if service_type_id is not None else ""
    params = [limit] + [value for value in (provider_id, service_type_id) if value is not None]
    rows = db.fetch_all(
        """SELECT DISTINCT TOP (?) si.ProviderId AS providerId, p.Name AS providerName, si.ServiceTypeId AS serviceTypeId, st.Name AS serviceTypeName
           FROM cimpl.ServiceInventory si
           INNER JOIN cimpl.Account a ON a.AccountId = si.AccountId
           INNER JOIN cimpl.Provider p ON p.ProviderId = si.ProviderId
           INNER JOIN cimpl.ServiceType st ON st.ServiceTypeId = si.ServiceTypeId
           WHERE si.ServiceInventoryStatusId = 1 AND si.ProviderId IS NOT NULL AND si.PrimaryNumber IS NOT NULL
               AND a.Number IS NOT NULL AND NOT EXISTS (SELECT 1 FROM cimpl.TransactServiceInventory tsi WHERE tsi.TransactId = (SELECT TOP 1 latest.TransactId FROM cimpl.TransactServiceInventory latest WHERE latest.ServiceInventoryId = si.ServiceInventoryId ORDER BY latest.TransactId DESC)) AND NOT EXISTS (SELECT 1 FROM cimpl.TransactWorkorderService tws INNER JOIN cimpl.WorkorderSummary ws ON ws.WorkorderId=tws.WorkorderId INNER JOIN cimpl.WorkorderStatus wos ON wos.WorkorderStatusId=ws.WorkorderStatusId AND wos.TenantId=ws.TenantId WHERE tws.ServiceInventoryId=si.ServiceInventoryId AND tws.WorkorderId=(SELECT TOP 1 latest.WorkorderId FROM cimpl.TransactWorkorderService latest WHERE latest.ServiceInventoryId=si.ServiceInventoryId ORDER BY latest.WorkorderId DESC) AND LOWER(wos.Name) NOT IN ('completed','cancelled','canceled'))"""
        + provider_filter + service_type_filter + " ORDER BY si.ProviderId DESC",
        tuple(params),
    )
    return [
        {"providerId": int(row["providerId"]), "providerName": str(row.get("providerName") or ""), "serviceTypeId": int(row["serviceTypeId"]), "serviceTypeName": str(row.get("serviceTypeName") or "")}
        for row in rows
    ]


def _service_body(service: dict) -> dict:
    return {"id": int(service["id"]), "accountNumber": str(service["accountNumber"]), "providerId": int(service["providerId"]), "serviceNumber": str(service["serviceNumber"])}


def _payload(db, case: CostCenterCase) -> tuple[dict, dict]:
    provider_id = int(os.getenv("TEST_PROVIDER_ID")) if os.getenv("TEST_PROVIDER_ID", "").strip() else None
    service_type_id = int(os.getenv("TEST_SERVICE_TYPE_ID")) if os.getenv("TEST_SERVICE_TYPE_ID", "").strip() else None
    service = _service(db, provider_id=provider_id, service_type_id=service_type_id)
    new_cost_center = _cost_center(db, exclude_id=service.get("currentCostCenterId"))
    marker = f"AUTO-CC-C{case.case_id}-{datetime.now():%Y%m%d%H%M%S%f}"
    body = {
        "requester": _employee(db), "dueDate": (date.today() + timedelta(days=2)).isoformat(), "comments": marker,
        "service": _service_body(service), "newCostCenterOrgId": new_cost_center,
    }
    return body, {"service": service, "newCostCenterOrgId": new_cost_center, "marker": marker}


def _mutate(case: CostCenterCase, body: dict, db):
    title = case.title.lower()
    if "valid provider from another service" in title or "valid account from another provider" in title or "valid service number from another service" in title:
        other = db.fetch_one("SELECT TOP 1 si.ProviderId AS providerId, a.Number AS accountNumber, si.PrimaryNumber AS serviceNumber FROM cimpl.ServiceInventory si INNER JOIN cimpl.Account a ON a.AccountId=si.AccountId WHERE si.ServiceInventoryId<>? AND si.ServiceInventoryStatusId=1 AND si.ProviderId IS NOT NULL AND si.PrimaryNumber IS NOT NULL AND a.Number IS NOT NULL ORDER BY si.ServiceInventoryId DESC", (body["service"]["id"],))
        assert other, "No alternate service compatibility seed available"
        if "provider" in title: body["service"]["providerId"] = int(other["providerId"])
        elif "account" in title: body["service"]["accountNumber"] = str(other["accountNumber"])
        else: body["service"]["serviceNumber"] = str(other["serviceNumber"])
    elif "service null" in title: body["service"] = None
    elif "service empty" in title: body["service"] = {}
    elif "service id only" in title: body["service"] = {"id": body["service"]["id"]}
    elif "service providerid only" in title: body["service"] = {"providerId": body["service"]["providerId"]}
    elif "service accountnumber only" in title: body["service"] = {"accountNumber": body["service"]["accountNumber"]}
    elif "service servicenumber only" in title: body["service"] = {"serviceNumber": body["service"]["serviceNumber"]}
    elif "missing newcostcenter" in title: body.pop("newCostCenterOrgId")
    elif "is null" in title and "newcostcenter" in title: body["newCostCenterOrgId"] = None
    elif "does not exist" in title: body["newCostCenterOrgId"] = 999999999
    elif "is inactive" in title and "newcostcenter" in title: body["newCostCenterOrgId"] = _cost_center(db, active=False)
    elif "closed costcenter" in title: body["newCostCenterOrgId"] = _cost_center(db, open_status=False)
    elif "not a costcenter" in title: body["newCostCenterOrgId"] = _cost_center(db, correct_type=False)
    elif "equals current" in title:
        service = _service(db, current_cost_center=True)
        body["service"] = _service_body(service)
        body["newCostCenterOrgId"] = int(service["currentCostCenterId"])
    elif "missing service" in title: body.pop("service")
    elif "service is inactive" in title: body["service"] = _service_body(_service(db, inactive=True))
    elif "open workorder" in title: body["service"] = _service_body(_service(db, open_workorder=True))
    elif "invalid service" in title: body["service"] = {"id": 999999999}
    elif "missing requester" in title: body.pop("requester")
    elif "requester is null" in title: body["requester"] = None
    elif "invalid requester" in title: body["requester"] = {"id": 999999999}
    elif "requester id only" in title: body["requester"] = {"id": body["requester"]["id"]}
    elif "requester email only" in title: body["requester"] = {"email": body["requester"]["email"]}
    elif "requester number only" in title: body["requester"] = {"number": body["requester"]["number"]}
    elif "email does not match" in title:
        other = _employee(db, exclude_id=body["requester"]["id"])
        body["requester"]["email"] = other["email"]
    elif "number does not match" in title:
        other = _employee(db, exclude_id=body["requester"]["id"])
        body["requester"]["number"] = other["number"]
    elif "inactive requester" in title:
        inactive = _employee(db, active=False)
        body["requester"] = inactive
    elif "invalid duedate" in title: body["dueDate"] = "12/31/2026"
    elif "past duedate" in title: body["dueDate"] = (date.today() - timedelta(days=1)).isoformat()
    elif "current-date" in title: body["dueDate"] = date.today().isoformat()
    elif "required fields only" in title:
        body.pop("dueDate")
        body.pop("comments")
    elif "with duedate" in title: body["dueDate"] = "2026-12-31"
    elif "with comments" in title: body["comments"] = "test transfer CostCenter"
    elif "sql injection" in title: body["comments"] = "' OR 1=1 --"
    elif "script injection" in title: body["comments"] = "<script>alert(1)</script>"
    elif "negative newcostcenter" in title: body["newCostCenterOrgId"] = -1
    elif "zero newcostcenter" in title: body["newCostCenterOrgId"] = 0
    elif "service id without provider" in title: body["service"] = {"id": body["service"]["id"]}
    elif "id and providerid only" in title: body["service"] = {"id": body["service"]["id"], "providerId": body["service"]["providerId"]}
    elif "primary number without" in title: body["service"] = {"serviceNumber": body["service"]["serviceNumber"]}
    elif "missing service providerid" in title: body["service"].pop("providerId")
    elif "missing service accountnumber" in title: body["service"].pop("accountNumber")
    elif "missing service servicenumber" in title: body["service"].pop("serviceNumber")
    elif "comments length" in title:
        target_length = int(title.rsplit(" ", 1)[1])
        marker = body["comments"]
        assert len(marker) <= target_length, f"Marker exceeds requested comment length {target_length}"
        body["comments"] = marker + ("C" * (target_length - len(marker)))


class TestPostWorkordersTransferCostCenter:
    @pytest.mark.parametrize("case", CASE_PARAMS)
    def test_post_workorders_transfer_cost_center(self, case, api_context, db, request):
        body, context = _payload(db, case)
        _mutate(case, body, db)
        scripts = PostApiDbScripts(db)
        marker = str(body.get("comments") or "")
        marker_before = scripts.count_rows(table="cimpl.WorkorderSummary", where_sql="Comment LIKE ?", params=(f"%{marker}%",)) if marker else None
        metrics = [f"CaseId: {case.case_id}", f"Expected: {case.expected}", "Service: " + json.dumps(context["service"], default=str, ensure_ascii=True), f"Requested CostCenterId: {body.get('newCostCenterOrgId')}", f"Request marker rows before: {marker_before}", f"Request body: {json.dumps(body, ensure_ascii=True)}"]
        if case.case_id == "17798174":
            response = requests.post(f"{os.environ['BASE_URL'].rstrip('/')}/workorders/transfer-cost-center", json=body, headers={"accept": "application/json"}, timeout=int(os.getenv("API_REQUEST_TIMEOUT_MS", "120000")) / 1000)
        else:
            response = api_context.post("workorders/transfer-cost-center", data=json.dumps(body, ensure_ascii=True), headers={"Content-Type": "application/json", "accept": "application/json"})
        try:
            payload = _safe_json(response)
            is_success = case.label in {"Positive", "Security"} or (
                case.label == "Boundary" and case.expected == "201 Created"
            )
            if is_success:
                assert _status(response) in {200, 201}, f"{case.title}: expected success, got {_status(response)}: {_response_text(response)}"
                number = str(payload.get("workorderNumber") or "").strip()
                assert number, f"{case.title}: response has no workorderNumber: {payload}"
                row = scripts.assert_single_row(query="""SELECT w.Number AS workorderNumber, w.CreatedDate AS createdDate,
                    tws.TransactId AS transactId, tws.ServiceInventoryId AS serviceId, tsi.CostCenterId AS costCenterId,
                    tsi.TransactStatusId AS transactStatusId, tsi.OperationTypeId AS operationTypeId,
                    tsi.ExecuteOperationTypeId AS executeOperationTypeId
                    FROM cimpl.Workorder w INNER JOIN cimpl.TransactWorkorderService tws ON tws.WorkorderId = w.WorkorderId
                    INNER JOIN cimpl.TransactServiceInventory tsi ON tsi.TransactId = tws.TransactId AND tsi.ServiceInventoryId = tws.ServiceInventoryId
                    WHERE w.Number = ?""", params=(number,), context=case.title)
                assert int(row["serviceId"]) == int(body["service"]["id"]), f"{case.title}: persisted service mismatch"
                assert int(row["costCenterId"]) == int(body["newCostCenterOrgId"]), f"{case.title}: pending CostCenterId mismatch"
                if case.case_id == "17798198":
                    assert int(row["operationTypeId"]) == 124, f"{case.title}: expected OperationTypeId 124, got {row['operationTypeId']}"
                    assert int(row["executeOperationTypeId"]) == 124, f"{case.title}: expected ExecuteOperationTypeId 124, got {row['executeOperationTypeId']}"
                if "with duedate" in case.title.lower():
                    assert row["createdDate"] is not None
                metrics.extend([f"Response status: {_status(response)}", f"Response payload: {json.dumps(payload, ensure_ascii=True)}", "DB validation: " + scripts.format_snapshot(row=row, keys=list(row))])
            elif case.case_id == "17798174":
                assert _status(response) in {401, 403}, f"{case.title}: expected 401/403, got {_status(response)}: {_response_text(response)}"
            else:
                assert _status(response) in {400, 404, 409, 500}, f"{case.title}: expected validation error, got {_status(response)}: {_response_text(response)}"
                if marker and marker_before is not None:
                    marker_after = scripts.count_rows(table="cimpl.WorkorderSummary", where_sql="Comment LIKE ?", params=(f"%{marker}%",))
                    scripts.assert_count_delta(before=marker_before, after=marker_after, expected_delta=0, context=case.title)
                    metrics.append(f"Request marker rows after: {marker_after}")
                metrics.extend([f"Response status: {_status(response)}", f"Response payload: {json.dumps(payload, ensure_ascii=True)}"])
        finally:
            if sys.exc_info()[0] is not None:
                metrics.extend([f"Response status: {_status(response)}", f"Response body: {_response_text(response)}"])
            _record_metrics(request, metrics)