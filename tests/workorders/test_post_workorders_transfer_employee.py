"""Data-driven validation for POST /v1/workorders/transfer-employee."""

from __future__ import annotations

import csv
import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
import requests

from utils.post_api_db_scripts import PostApiDbScripts
from utils.organization_types import cost_center_type_sql


@dataclass(frozen=True)
class TransferCase:
    case_id: str
    title: str
    expected: str
    label: str
    preconditions: str


def _load_cases() -> list[TransferCase]:
    paths = (Path(__file__).with_name("cimpl (13).csv"), Path.home() / "Downloads" / "cimpl (13).csv")
    path = next((candidate for candidate in paths if candidate.exists()), None)
    if path is None:
        pytest.skip(
            "WOR20 catalog cimpl (13).csv is not present beside the tests or in Downloads; skipping transfer-employee validation until the seed file is provided.",
            allow_module_level=True,
        )
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return [
            TransferCase(
                str(row["ID"]).strip().removeprefix("C"), str(row["Title"]).strip(),
                str(row["Expected Result"]).strip(), str(row["Labels"]).strip(), str(row["Preconditions"]).strip(),
            )
            for row in csv.DictReader(stream)
        ]


CASES = _load_cases()
for case_index, case in enumerate(CASES):
    if case.case_id == "17796049":
        CASES[case_index] = TransferCase(
            case.case_id,
            case.title,
            "Validation error returned and no workorder created because newEmployee already owns the service",
            case.label,
            case.preconditions,
        )
CASES.extend(
    [
        TransferCase("17796050", "New employee is null", "Validation error and no workorder created", "Validation", "newEmployee=null"),
        TransferCase("17796051", "Empty newEmployee object", "Validation error and no workorder created", "Validation", "newEmployee={}"),
        TransferCase("17796052", "New employee invalid email format", "Validation error and no workorder created", "Validation", "newEmployee.email=abc@"),
        TransferCase("17796053", "New employee identifiers belong to different employees", "Validation error and no workorder created", "Validation", "newEmployee.id user A; email/number user B"),
        TransferCase("17796054", "Requester is null", "Validation error and no workorder created", "Validation", "requester=null"),
        TransferCase("17796055", "Empty requester object", "Validation error and no workorder created", "Validation", "requester={}"),
        TransferCase("17796056", "Cost center not related to new employee", "Validation error and no workorder created", "Validation", "valid CostCenter unrelated to newEmployee"),
        TransferCase("17796057", "Cost center is not a CostCenter organization", "Validation error and no workorder created", "Validation", "OrganizationTypeId<>4"),
        TransferCase("17796058", "Inactive CostCenter", "Validation error and no workorder created", "Validation", "OrganizationTypeId=4; Active=0"),
    ]
)
CASES.extend([
    TransferCase("17838362", "Requester ID only", "Validation error and no workorder created", "Validation", "requester.id only"),
    TransferCase("17838363", "Requester email only", "Validation error and no workorder created", "Validation", "requester.email only"),
    TransferCase("17838364", "Requester number only", "Validation error and no workorder created", "Validation", "requester.number only"),
    TransferCase("17838365", "Inactive requester", "Validation error and no workorder created", "Validation", "requester is inactive"),
    TransferCase("17838366", "Requester ID and email mismatch", "Validation error and no workorder created", "Validation", "requester identifiers mismatch"),
    TransferCase("17838367", "Service null", "Validation error and no workorder created", "Validation", "service=null"),
    TransferCase("17838368", "Service ID only", "Validation error and no workorder created", "Validation", "service.id only"),
    TransferCase("17838369", "Service ID and providerId only", "Validation error and no workorder created", "Validation", "service.id and providerId only"),
    TransferCase("17838370", "Due date null", "Validation error and no workorder created", "Validation", "dueDate=null"),
    TransferCase("17838371", "Due date current date", "Workorder created successfully", "Positive", "dueDate=today"),
    TransferCase("17838372", "New employee ID only", "Validation error and no workorder created", "Validation", "newEmployee.id only"),
    TransferCase("17838373", "New employee email only", "Validation error and no workorder created", "Validation", "newEmployee.email only"),
    TransferCase("17838374", "New employee number only", "Validation error and no workorder created", "Validation", "newEmployee.number only"),
    TransferCase("17838413", "Service with valid provider from another service", "Validation error and no workorder created", "Validation", "valid provider belongs to another service"),
    TransferCase("17838414", "Service with valid account from another provider", "Validation error and no workorder created", "Validation", "valid account belongs to another provider"),
    TransferCase("17838415", "Service with valid service number from another service", "Validation error and no workorder created", "Validation", "valid service number belongs to another service"),
])
CASE_PARAMS = [
    pytest.param(case, id=f"C{case.case_id}", marks=[
        pytest.mark.swagger_section("Workorders"),
        pytest.mark.swagger_operation("POST /v1/workorders/transfer-employee"),
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


def _response_status(response) -> int:
    return int(response.status if hasattr(response, "status") else response.status_code)


def _record_metrics(request, metrics: list[str]):
    request.node.user_properties.append(("dashboard_metrics", "\n".join(metrics)))


def _employee(db, *, active: bool, exclude_id: int | None = None, requires_cost_center: bool = False) -> dict:
    status_clause = "e.EmployeeStatusId = 1" if active else "e.EmployeeStatusId <> 1"
    exclusion = "AND e.EmployeeId <> ?" if exclude_id is not None else ""
    cost_center_clause = "AND cost_center.CostCenterId IS NOT NULL" if requires_cost_center else ""
    params = (exclude_id,) if exclude_id is not None else ()
    row = db.fetch_one(
        """SELECT TOP 1 e.EmployeeId AS id, e.Number AS number, e.EMail AS email,
                  cost_center.CostCenterId AS costCenterId
           FROM cimpl.Employee e
           OUTER APPLY (SELECT TOP 1 ec.CostCenterId
                        FROM cimpl.EmployeeCostCenter ec
                        INNER JOIN cimpl.Organization o ON o.OrganizationId = ec.CostCenterId
                            AND """ + cost_center_type_sql("o.OrganizationTypeId") + """ AND o.Active = 1
                        WHERE ec.EmployeeId = e.EmployeeId
                        ORDER BY ec.CostCenterId DESC) cost_center
           WHERE """ + status_clause + " AND e.EmployeeId IS NOT NULL AND e.EMail IS NOT NULL " + exclusion + " " + cost_center_clause + " ORDER BY e.EmployeeId DESC",
        params,
    )
    assert row, f"No {'active' if active else 'inactive'} employee available for transfer scenario"
    return {"id": int(row["id"]), "number": str(row.get("number") or ""), "email": str(row["email"]), "costCenterId": row.get("costCenterId")}


def _active_service(db, *, department_owned: bool = False, employee_owned: bool = False, provider_id: int | None = None, service_type_id: int | None = None) -> dict:
    ownership_clause = ""
    if department_owned:
        ownership_clause = "AND si.OrganizationId IS NOT NULL"
    elif employee_owned:
        ownership_clause = "AND EXISTS (SELECT 1 FROM cimpl.ServiceInventoryEmployeeRelation sier WHERE sier.ServiceInventoryId = si.ServiceInventoryId AND sier.IsPrimary = 1)"
    provider_filter = " AND si.ProviderId=?" if provider_id is not None else ""
    service_type_filter = " AND si.ServiceTypeId=?" if service_type_id is not None else ""
    params = tuple(value for value in (provider_id, service_type_id) if value is not None)
    row = db.fetch_one(
        """SELECT TOP 1 si.ServiceInventoryId AS id, a.Number AS accountNumber, si.ProviderId AS providerId,
                  si.PrimaryNumber AS serviceNumber, si.ServiceInventoryStatusId AS statusId
           FROM cimpl.ServiceInventory si INNER JOIN cimpl.Account a ON a.AccountId = si.AccountId
           WHERE si.ServiceInventoryStatusId = 1 AND si.ProviderId IS NOT NULL AND si.PrimaryNumber IS NOT NULL
             AND a.Number IS NOT NULL AND NOT EXISTS (SELECT 1 FROM cimpl.TransactServiceInventory tsi WHERE tsi.TransactId = (SELECT TOP 1 latest.TransactId FROM cimpl.TransactServiceInventory latest WHERE latest.ServiceInventoryId = si.ServiceInventoryId ORDER BY latest.TransactId DESC)) AND NOT EXISTS (SELECT 1 FROM cimpl.TransactWorkorderService tws INNER JOIN cimpl.WorkorderSummary ws ON ws.WorkorderId=tws.WorkorderId INNER JOIN cimpl.WorkorderStatus wos ON wos.WorkorderStatusId=ws.WorkorderStatusId AND wos.TenantId=ws.TenantId WHERE tws.ServiceInventoryId=si.ServiceInventoryId AND tws.WorkorderId=(SELECT TOP 1 latest.WorkorderId FROM cimpl.TransactWorkorderService latest WHERE latest.ServiceInventoryId=si.ServiceInventoryId ORDER BY latest.WorkorderId DESC) AND LOWER(wos.Name) NOT IN ('completed','cancelled','canceled')) """
        + ownership_clause + provider_filter + service_type_filter + " ORDER BY si.ServiceInventoryId DESC",
        params,
    )
    assert row, "No active transaction-free service available for transfer with the selected filters"
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
             AND a.Number IS NOT NULL AND NOT EXISTS (SELECT 1 FROM cimpl.TransactServiceInventory tsi WHERE tsi.TransactId = (SELECT TOP 1 latest.TransactId FROM cimpl.TransactServiceInventory latest WHERE latest.ServiceInventoryId = si.ServiceInventoryId ORDER BY latest.TransactId DESC))"""
        + provider_filter + service_type_filter + " ORDER BY si.ProviderId DESC",
        tuple(params),
    )
    return [
        {"providerId": int(row["providerId"]), "providerName": str(row.get("providerName") or ""), "serviceTypeId": int(row["serviceTypeId"]), "serviceTypeName": str(row.get("serviceTypeName") or "")}
        for row in rows
    ]


def _service_with_current_employee(db) -> tuple[dict, dict]:
        row = db.fetch_one(
                """SELECT TOP 1 si.ServiceInventoryId AS serviceId, a.Number AS accountNumber, si.ProviderId AS providerId,
                                    si.PrimaryNumber AS serviceNumber, e.EmployeeId AS employeeId, e.Number AS employeeNumber,
                                    e.EMail AS employeeEmail, ec.CostCenterId AS costCenterId
                     FROM cimpl.ServiceInventory si
                     INNER JOIN cimpl.ServiceInventoryEmployeeRelation sier ON sier.ServiceInventoryId = si.ServiceInventoryId AND sier.IsPrimary = 1
                     INNER JOIN cimpl.Employee e ON e.EmployeeId = sier.EmployeeId AND e.EmployeeStatusId = 1 AND e.EMail IS NOT NULL
                     INNER JOIN cimpl.EmployeeCostCenter ec ON ec.EmployeeId = e.EmployeeId
                     INNER JOIN cimpl.Organization o ON o.OrganizationId = ec.CostCenterId AND """ + cost_center_type_sql("o.OrganizationTypeId") + """ AND o.Active = 1
                     INNER JOIN cimpl.Account a ON a.AccountId = si.AccountId
                     WHERE si.ServiceInventoryStatusId = 1 AND si.ProviderId IS NOT NULL AND si.PrimaryNumber IS NOT NULL
                         AND a.Number IS NOT NULL AND NOT EXISTS (SELECT 1 FROM cimpl.TransactServiceInventory tsi WHERE tsi.TransactId = (SELECT TOP 1 latest.TransactId FROM cimpl.TransactServiceInventory latest WHERE latest.ServiceInventoryId = si.ServiceInventoryId ORDER BY latest.TransactId DESC))
                     ORDER BY si.ServiceInventoryId DESC""")
        assert row, "No active employee-owned transaction-free service with a related CostCenter is available"
        service = {"id": int(row["serviceId"]), "accountNumber": str(row["accountNumber"]), "providerId": int(row["providerId"]), "serviceNumber": str(row["serviceNumber"])}
        employee = {"id": int(row["employeeId"]), "number": str(row.get("employeeNumber") or ""), "email": str(row["employeeEmail"]), "costCenterId": int(row["costCenterId"])}
        return service, employee


def _service_payload(service: dict) -> dict:
    return {"id": int(service["id"]), "accountNumber": str(service["accountNumber"]), "providerId": int(service["providerId"]), "serviceNumber": str(service["serviceNumber"])}


def _inactive_service(db) -> dict:
    row = db.fetch_one(
        """SELECT TOP 1 si.ServiceInventoryId AS id, a.Number AS accountNumber, si.ProviderId AS providerId, si.PrimaryNumber AS serviceNumber
           FROM cimpl.ServiceInventory si INNER JOIN cimpl.Account a ON a.AccountId = si.AccountId
           WHERE si.ServiceInventoryStatusId <> 1 AND si.ProviderId IS NOT NULL AND si.PrimaryNumber IS NOT NULL AND a.Number IS NOT NULL
           ORDER BY si.ServiceInventoryId DESC""")
    assert row, "No inactive service available"
    return _service_payload(row)


def _service_with_open_workorder(db) -> dict:
    row = db.fetch_one(
        """SELECT TOP 1 si.ServiceInventoryId AS id, a.Number AS accountNumber, si.ProviderId AS providerId, si.PrimaryNumber AS serviceNumber
           FROM cimpl.TransactWorkorderService tws
           INNER JOIN cimpl.WorkorderSummary ws ON ws.WorkorderId = tws.WorkorderId
           INNER JOIN cimpl.OpenWorkorderStatusView open_status ON open_status.WorkorderStatusId = ws.WorkorderStatusId
           INNER JOIN cimpl.ServiceInventory si ON si.ServiceInventoryId = tws.ServiceInventoryId
           INNER JOIN cimpl.Account a ON a.AccountId = si.AccountId
           WHERE si.ProviderId IS NOT NULL AND si.PrimaryNumber IS NOT NULL AND a.Number IS NOT NULL
           ORDER BY tws.WorkorderId DESC""")
    assert row, "No service with an open workorder available"
    return _service_payload(row)


def _cost_center_required(db) -> bool:
    value = db.fetch_scalar("SELECT TOP 1 Value FROM cimpl.Parameter WHERE ParameterNameId = 276 ORDER BY TenantId")
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _cost_center(db, *, active: bool | None = None, must_be_cost_center: bool = True, exclude_employee_id: int | None = None) -> int:
    where = ["o.OrganizationId IS NOT NULL"]
    if active is not None:
        where.append(f"o.Active = {1 if active else 0}")
    where.append(cost_center_type_sql("o.OrganizationTypeId", required=must_be_cost_center))
    params: tuple = ()
    if exclude_employee_id is not None:
        where.append("NOT EXISTS (SELECT 1 FROM cimpl.EmployeeCostCenter ec WHERE ec.EmployeeId = ? AND ec.CostCenterId = o.OrganizationId)")
        params = (exclude_employee_id,)
    row = db.fetch_one(
        "SELECT TOP 1 o.OrganizationId AS id FROM cimpl.Organization o WHERE "
        + " AND ".join(where)
        + " ORDER BY o.OrganizationId DESC",
        params,
    )
    assert row, "No organization found for CostCenter validation scenario"
    return int(row["id"])


def _payload(db, case: TransferCase) -> tuple[dict, dict]:
    requester = _employee(db, active=True)
    new_employee = _employee(db, active=True, exclude_id=requester["id"], requires_cost_center=True)
    provider_id = int(os.getenv("TEST_PROVIDER_ID")) if os.getenv("TEST_PROVIDER_ID", "").strip() else None
    service_type_id = int(os.getenv("TEST_SERVICE_TYPE_ID")) if os.getenv("TEST_SERVICE_TYPE_ID", "").strip() else None
    service = _active_service(db, provider_id=provider_id, service_type_id=service_type_id)
    marker = f"AUTO-TRANSFER-C{case.case_id}-{datetime.now():%Y%m%d%H%M%S%f}"
    body = {
        "requester": {key: requester[key] for key in ("id", "number", "email")},
        "dueDate": (date.today() + timedelta(days=2)).isoformat(),
        "comments": marker,
        "service": _service_payload(service),
        "newEmployee": {key: new_employee[key] for key in ("id", "number", "email")},
        "costCenterId": int(new_employee["costCenterId"]),
    }
    return body, {"requester": requester, "newEmployee": new_employee, "service": service}


def _apply_scenario(case: TransferCase, body: dict, db) -> None:
    title = case.title.lower()
    if "valid provider from another service" in title or "valid account from another provider" in title or "valid service number from another service" in title:
        other = db.fetch_one("SELECT TOP 1 si.ProviderId AS providerId, a.Number AS accountNumber, si.PrimaryNumber AS serviceNumber FROM cimpl.ServiceInventory si INNER JOIN cimpl.Account a ON a.AccountId=si.AccountId WHERE si.ServiceInventoryId<>? AND si.ServiceInventoryStatusId=1 AND si.ProviderId IS NOT NULL AND si.PrimaryNumber IS NOT NULL AND a.Number IS NOT NULL ORDER BY si.ServiceInventoryId DESC", (body["service"]["id"],))
        assert other, "No alternate service compatibility seed available"
        if "provider" in title: body["service"]["providerId"] = int(other["providerId"])
        elif "account" in title: body["service"]["accountNumber"] = str(other["accountNumber"])
        else: body["service"]["serviceNumber"] = str(other["serviceNumber"])
    elif "requester id only" in title:
        body["requester"] = {"id": body["requester"]["id"]}
    elif "requester email only" in title:
        body["requester"] = {"email": body["requester"]["email"]}
    elif "requester number only" in title:
        body["requester"] = {"number": body["requester"]["number"]}
    elif "inactive requester" in title:
        employee = _employee(db, active=False)
        body["requester"] = {key: employee[key] for key in ("id", "number", "email")}
    elif "requester id and email mismatch" in title:
        other = _employee(db, active=True, exclude_id=body["requester"]["id"])
        body["requester"]["email"] = other["email"]
    elif "service null" in title:
        body["service"] = None
    elif "service id only" in title:
        body["service"] = {"id": body["service"]["id"]}
    elif "service id and providerid only" in title:
        body["service"] = {"id": body["service"]["id"], "providerId": body["service"]["providerId"]}
    elif "due date null" in title:
        body["dueDate"] = None
    elif "due date current" in title:
        body["dueDate"] = date.today().isoformat()
    elif "new employee id only" in title:
        body["newEmployee"] = {"id": body["newEmployee"]["id"]}
    elif "new employee email only" in title:
        body["newEmployee"] = {"email": body["newEmployee"]["email"]}
    elif "new employee number only" in title:
        body["newEmployee"] = {"number": body["newEmployee"]["number"]}
    elif "department to employee" in title:
        body["service"] = _service_payload(_active_service(db, department_owned=True))
    elif "employee  to different" in title:
        body["service"] = _service_payload(_active_service(db, employee_owned=True))
    elif "same employeeemployee" in title:
        service, employee = _service_with_current_employee(db)
        body["service"] = service
        body["newEmployee"] = {key: employee[key] for key in ("id", "number", "email")}
        body["costCenterId"] = employee["costCenterId"]
    elif "missing newemployee" in title:
        body.pop("newEmployee")
    elif "inactive newemployee" in title:
        employee = _employee(db, active=False)
        body["newEmployee"] = {key: employee[key] for key in ("id", "number", "email")}
    elif "nonexistent newemployee" in title:
        body["newEmployee"] = {"id": 999999999}
    elif "new employee is null" in title:
        body["newEmployee"] = None
    elif "empty newemployee" in title:
        body["newEmployee"] = {}
    elif "new employee invalid email" in title:
        body["newEmployee"] = {"email": "abc@"}
    elif "new employee identifiers" in title:
        other = _employee(db, active=True, exclude_id=body["newEmployee"]["id"])
        body["newEmployee"].update({"number": other["number"], "email": other["email"]})
    elif "missing service" in title:
        body.pop("service")
    elif "service not active" in title:
        body["service"] = _inactive_service(db)
    elif "service has open workorder" in title:
        body["service"] = _service_with_open_workorder(db)
    elif "missing requestor" in title:
        body.pop("requester")
    elif "requester is null" in title:
        body["requester"] = None
    elif "empty requester" in title:
        body["requester"] = {}
    elif "invalid duedate" in title:
        body["dueDate"] = "12/31/2026"
    elif "invalid costcenterid" in title:
        body["costCenterId"] = 999999999
    elif "mandatory config enabled and omitted" in title:
        assert _cost_center_required(db), "C17796042 requires ParameterNameId 276 to be enabled"
        body.pop("costCenterId")
    elif "cost center not related" in title:
        body["costCenterId"] = _cost_center(db, active=True, exclude_employee_id=body["newEmployee"]["id"])
    elif "not a costcenter" in title:
        body["costCenterId"] = _cost_center(db, active=True, must_be_cost_center=False)
    elif "inactive costcenter" in title:
        body["costCenterId"] = _cost_center(db, active=False)
    elif "without optional fields" in title:
        body.pop("dueDate")
        body.pop("comments")
    elif "with duedate" in title:
        body["dueDate"] = "2026-12-31"
    elif "with comments" in title:
        body["comments"] = "test transfer"
    elif "long comments" in title:
        body["comments"] = "C" * 501
    elif "special characters" in title:
        body["comments"] = "@#$% Test \u00e4"
    elif "sql injection" in title:
        body["comments"] = "' OR 1=1 --"
    elif "script injection" in title:
        body["comments"] = "<script>alert(1)</script>"


def _expects_success(case: TransferCase) -> bool:
    if case.case_id == "17796049":
        return False
    return case.label in {"Positive", "Boundary", "Security"}


class TestPostWorkordersTransferEmployee:
    @pytest.mark.parametrize("case", CASE_PARAMS)
    def test_post_workorders_transfer_employee(self, case, api_context, db, request):
        body, context = _payload(db, case)
        _apply_scenario(case, body, db)
        scripts = PostApiDbScripts(db)
        request_started_at = datetime.now()
        marker = str(body.get("comments") or "").strip()
        marker_before = scripts.count_rows(
            table="cimpl.WorkorderSummary",
            where_sql="Comment LIKE ?",
            params=(f"%{marker}%",),
        ) if marker else None
        metrics = [
            f"CaseId: {case.case_id}", f"Expected: {case.expected}",
            "Requester: " + json.dumps(context["requester"], ensure_ascii=True),
            "New employee: " + json.dumps(context["newEmployee"], default=str, ensure_ascii=True),
            "Service: " + json.dumps(context["service"], default=str, ensure_ascii=True),
            f"Request marker rows before: {marker_before}", f"Request body: {json.dumps(body, ensure_ascii=True)}",
        ]
        if case.case_id == "17796047":
            response = requests.post(
                f"{os.environ['BASE_URL'].rstrip('/')}/workorders/transfer-employee",
                json=body,
                headers={"accept": "application/json"},
                timeout=int(os.getenv("API_REQUEST_TIMEOUT_MS", "120000")) / 1000,
            )
        else:
            response = api_context.post("workorders/transfer-employee", data=json.dumps(body, ensure_ascii=True), headers={"Content-Type": "application/json", "accept": "application/json"})
        try:
            payload = _safe_json(response)
            if case.case_id == "17796047":
                assert _response_status(response) in {401, 403}, f"{case.title}: expected 401/403, got {_response_status(response)}: {_response_text(response)}"
            elif _expects_success(case):
                assert response.status in {200, 201}, f"{case.title}: expected success, got {response.status}: {_response_text(response)}"
                number = str(payload.get("workorderNumber") or "").strip()
                assert number, f"{case.title}: missing workorderNumber: {payload}"
                row = scripts.assert_single_row(query="""SELECT w.WorkorderId AS workorderId, w.Number AS workorderNumber,
                    tws.TransactId AS transactId, tws.ServiceInventoryId AS serviceInventoryId,
                    tsier.EmployeeId AS employeeId, tsier.IsPrimary AS isPrimary, ws.DueDate AS dueDate,
                    ws.Comment AS comments, wr.EmployeeId AS requesterEmployeeId, w.CreatedDate AS createdDate
                    FROM cimpl.Workorder w
                    INNER JOIN cimpl.WorkorderSummary ws ON ws.WorkorderId = w.WorkorderId
                    LEFT JOIN cimpl.WorkorderRequester wr ON wr.WorkorderRequesterId = ws.WorkorderRequesterId
                    INNER JOIN cimpl.TransactWorkorderService tws ON tws.WorkorderId = w.WorkorderId
                    INNER JOIN cimpl.TransactServiceInventoryEmployeeRelation tsier ON tsier.TransactId = tws.TransactId
                        AND tsier.ServiceInventoryId = tws.ServiceInventoryId
                    WHERE w.Number = ? AND tsier.IsPrimary = 1""", params=(number,), context=case.title)
                assert row["createdDate"] >= request_started_at - timedelta(minutes=1), (
                    f"{case.title}: returned workorder {number} was not created by this request. "
                    f"CreatedDate={row['createdDate']}, RequestStarted={request_started_at}"
                )
                assert int(row["serviceInventoryId"]) == int(body["service"]["id"]), f"{case.title}: persisted service mismatch"
                assert int(row["employeeId"]) == int(body["newEmployee"]["id"]), f"{case.title}: persisted new employee mismatch"
                assert bool(row["isPrimary"]), f"{case.title}: transfer employee relation is not primary"
                if body.get("requester", {}).get("id") is not None:
                    assert int(row["requesterEmployeeId"]) == int(body["requester"]["id"]), f"{case.title}: persisted requester mismatch"
                if "with duedate" in case.title.lower():
                    assert row["dueDate"].date().isoformat() == body["dueDate"], f"{case.title}: persisted dueDate mismatch"
                metrics.extend([f"Response status: {response.status}", f"Response payload: {json.dumps(payload, ensure_ascii=True)}", "DB validation: " + scripts.format_snapshot(row=row, keys=list(row))])
            else:
                assert response.status in {400, 404, 409, 500}, f"{case.title}: expected validation error, got {response.status}: {_response_text(response)}"
                if marker and marker_before is not None:
                    marker_after = scripts.count_rows(
                        table="cimpl.WorkorderSummary",
                        where_sql="Comment LIKE ?",
                        params=(f"%{marker}%",),
                    )
                    scripts.assert_count_delta(before=marker_before, after=marker_after, expected_delta=0, context=case.title)
                    metrics.append(f"Request marker rows after: {marker_after}")
                metrics.extend([f"Response status: {response.status}", f"Response payload: {json.dumps(payload, ensure_ascii=True)}"])
        finally:
            if sys.exc_info()[0] is not None:
                metrics.extend([f"Response status: {_response_status(response)}", f"Response body: {_response_text(response)}"])
            _record_metrics(request, metrics)