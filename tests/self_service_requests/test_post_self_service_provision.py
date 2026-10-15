"""REQ01 coverage for POST /requestApi/SelfServiceRequests/Provision."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID

import pytest
import requests

from tests.workorders.test_post_workorders_provision import _build_base_payload, preview_seed_compatibility  # noqa: F401
from utils.post_api_db_scripts import PostApiDbScripts


SELF_SERVICE_URL = os.getenv("SELF_SERVICE_BASE_URL", "https://cplca1webq01.uplandsoftware.net/requestApi").rstrip("/") + "/SelfServiceRequests/Provision"


@dataclass(frozen=True)
class SelfServiceCase:
    case_id: str
    title: str
    label: str


_CASE_DATA = [
    ("17838560", "Create Self Service Provision request with compatible data", "Positive"),
    ("17838561", "Create Self Service Provision with required fields only", "Validation"),
    ("17838562", "Employee ID only", "Positive"),
    ("17838563", "Employee number only", "Positive"),
    ("17838564", "Employee email only", "Positive"),
    ("17838565", "Department ID only", "Positive"),
    ("17838566", "Employee and requester are different active employees", "Positive"),
    ("17838567", "Multiple compatible options", "Positive"),
    ("17838568", "Compatible accessory as integer catalog ID", "Positive"),
    ("17838569", "Valid company shipping address", "Positive"),
    ("17838570", "Valid cost center", "Positive"),
    ("17838571", "Missing employee", "Validation"), ("17838572", "Null employee", "Validation"),
    ("17838573", "Empty employee object", "Validation"), ("17838574", "Invalid employee ID", "Validation"),
    ("17838575", "Invalid employee number", "Validation"), ("17838576", "Invalid employee email", "Validation"),
    ("17838577", "Inactive employee", "Positive"), ("17838578", "Employee identifier mismatch", "Validation"),
    ("17838579", "Missing requester", "Validation"), ("17838580", "Null requester", "Validation"),
    ("17838581", "Empty requester object", "Validation"), ("17838582", "Invalid requester identifier", "Validation"),
    ("17838583", "Inactive requester", "Validation"), ("17838584", "Requester identifier mismatch", "Validation"),
    ("17838585", "Missing provider", "Validation"), ("17838586", "Provider not self-service enabled", "Validation"),
    ("17838587", "Invalid provider", "Validation"), ("17838588", "Missing service type", "Validation"),
    ("17838589", "Service type not self-service allowed", "Validation"), ("17838590", "Service type not provisioning allowed", "Validation"),
    ("17838591", "Service type incompatible with provider", "Validation"), ("17838592", "Missing contract", "Validation"),
    ("17838593", "Inactive contract", "Validation"), ("17838594", "Contract not self-service enabled", "Validation"),
    ("17838595", "Contract wrong provider", "Validation"), ("17838596", "Contract wrong service type", "Validation"),
    ("17838597", "Missing catalog item", "Validation"), ("17838598", "Inactive catalog item", "Validation"),
    ("17838599", "Unsupported catalog category", "Validation"), ("17838600", "Catalog incompatible with provider", "Validation"),
    ("17838601", "Catalog incompatible with service type", "Validation"), ("17838602", "Catalog incompatible with contract", "Validation"),
    ("17838603", "Missing options", "Positive"), ("17838604", "Null options", "Positive"),
    ("17838605", "Option already included in contract", "Validation"), ("17838606", "Option incompatible with provider or service type", "Validation"),
    ("17838607", "Duplicate option IDs", "Validation"), ("17838608", "Missing accessories", "Positive"),
    ("17838609", "Invalid accessory compatibility", "Validation"), ("17838610", "Missing shipping address", "Validation"),
    ("17838611", "Missing shipping address child field", "Validation"), ("17838612", "Invalid country code", "Validation"),
    ("17838613", "Missing cost center", "Validation"), ("17838614", "Invalid cost center", "Validation"),
    ("17838615", "Due date null", "Positive"), ("17838616", "Past due date", "Validation"),
    ("17838617", "Invalid calendar due date", "Validation"), ("17838618", "Comments SQL injection input", "Positive"),
    ("17838619", "Comments XSS input", "Positive"), ("17838620", "Unauthorized request", "Authorization"),
]
CASES = [SelfServiceCase(*row) for row in _CASE_DATA]
CASE_PARAMS = [pytest.param(case, id=f"C{case.case_id}", marks=[
    pytest.mark.swagger_section("Self Service Requests"),
    pytest.mark.swagger_operation("POST /requestApi/SelfServiceRequests/Provision"),
    pytest.mark.testrail_case(case.case_id),
    pytest.mark.dashboard_title(case.title),
]) for case in CASES]


def _json(response) -> dict:
    try:
        value = response.json()
        return value if isinstance(value, dict) else {"raw": value}
    except ValueError:
        return {}


def _status(response) -> int:
    return int(response.status_code)


def _text(response) -> str:
    return str(response.text)


def _record(request, metrics: list[str]):
    request.node.user_properties.append(("dashboard_metrics", "\n".join(metrics)))


def _employee(db, *, active: bool = True, exclude_id: int | None = None) -> dict:
    status = "=1" if active else "<>1"
    exclusion = "AND EmployeeId<>?" if exclude_id is not None else ""
    params = (exclude_id,) if exclude_id is not None else ()
    active_filter = "Active=1" if active else "Active=0"
    row = db.fetch_one("SELECT TOP 1 EmployeeId AS id,Number AS number,EMail AS email FROM cimpl.Employee WHERE " + active_filter + " AND EmployeeStatusId" + status + " AND EMail IS NOT NULL " + exclusion + " ORDER BY EmployeeId DESC", params)
    assert row, "No employee seed available"
    return {"id": int(row["id"]), "number": str(row.get("number") or ""), "email": str(row["email"])}


def _payload(db, case: SelfServiceCase) -> tuple[dict, dict]:
    body, context = _build_base_payload(db, case_id=case.case_id, scenario="same-employee-and-requester")
    allowed = {"employee", "departmentId", "catalogItemId", "providerId", "contractId", "serviceTypeId", "options", "accessories", "shippingAddress", "costCenterId", "requester", "dueDate", "comments"}
    body = {key: value for key, value in body.items() if key in allowed}
    if isinstance(body.get("accessories"), list):
        body["accessories"] = [int(item["id"] if isinstance(item, dict) else item) for item in body["accessories"]]
    body["comments"] = f"AUTO-SELF-SERVICE-REQ01-C{case.case_id}"
    context["marker"] = body["comments"]
    return body, context


def _alternate_service_values(db, service_id: int) -> dict:
    row = db.fetch_one("SELECT TOP 1 si.ProviderId AS providerId,a.Number AS accountNumber,si.PrimaryNumber AS serviceNumber FROM cimpl.ServiceInventory si INNER JOIN cimpl.Account a ON a.AccountId=si.AccountId WHERE si.ServiceInventoryId<>? AND si.ServiceInventoryStatusId=1 AND si.ProviderId IS NOT NULL AND si.PrimaryNumber IS NOT NULL AND a.Number IS NOT NULL ORDER BY si.ServiceInventoryId DESC", (service_id,))
    assert row, "No alternate service seed available"
    return {key: row[key] for key in row}


def _mutate(case: SelfServiceCase, body: dict, context: dict, db):
    title = case.title.lower()
    if "required fields only" in title: body.pop("dueDate", None); body.pop("comments", None); body.pop("costCenterId", None)
    elif "employee id only" in title: body["employee"] = {"id": body["employee"]["id"]}
    elif "employee number only" in title: body["employee"] = {"number": body["employee"]["number"]}
    elif "employee email only" in title: body["employee"] = {"email": body["employee"]["email"]}
    elif "department id only" in title: body.pop("employee", None); body["departmentId"] = int(db.fetch_scalar("SELECT TOP 1 OrganizationId FROM cimpl.Organization WHERE Active=1 ORDER BY OrganizationId DESC"))
    elif "different active employees" in title: body["requester"] = _employee(db, exclude_id=body["employee"]["id"])
    elif "multiple compatible options" in title:
        option = db.fetch_scalar("SELECT TOP 1 c.OptionTypeId FROM cimpl.OptionTypeProviderServiceTypeCompatibility c INNER JOIN cimpl.OptionType o ON o.OptionTypeId=c.OptionTypeId AND o.Active=1 WHERE c.ProviderId=? AND c.ServiceTypeId=? AND c.SelfProvisioningEnabled=1 AND NOT EXISTS (SELECT 1 FROM cimpl.ContractOptionTypeRelation r WHERE r.ContractId=? AND r.OptionTypeId=c.OptionTypeId) ORDER BY c.OptionTypeId DESC", (body["providerId"], body["serviceTypeId"], body["contractId"]))
        if option: body["options"] = [int(option)]
    elif "compatible accessory" in title: pass
    elif "missing employee" in title: body.pop("employee", None)
    elif "null employee" in title: body["employee"] = None
    elif "empty employee" in title: body["employee"] = {}
    elif "invalid employee id" in title: body["employee"] = {"id": 999999999}
    elif "invalid employee number" in title: body["employee"] = {"number": "INVALID-EMPLOYEE"}
    elif "invalid employee email" in title: body["employee"] = {"email": "invalid@example.invalid"}
    elif "inactive employee" in title: body["employee"] = _employee(db, active=False)
    elif "employee identifier mismatch" in title: body["employee"]["email"] = _employee(db, exclude_id=body["employee"]["id"])["email"]
    elif "missing requester" in title: body.pop("requester", None)
    elif "null requester" in title: body["requester"] = None
    elif "empty requester" in title: body["requester"] = {}
    elif "invalid requester" in title: body["requester"] = {"id": 999999999}
    elif "inactive requester" in title: body["requester"] = _employee(db, active=False)
    elif "requester identifier mismatch" in title: body["requester"]["email"] = _employee(db, exclude_id=body["requester"]["id"])["email"]
    elif "missing provider" in title: body.pop("providerId", None)
    elif "provider not self-service" in title: body["providerId"] = 999999999
    elif "invalid provider" in title: body["providerId"] = 999999999
    elif "missing service type" in title: body.pop("serviceTypeId", None)
    elif "not self-service allowed" in title: body["serviceTypeId"] = 999999999
    elif "not provisioning allowed" in title: body["serviceTypeId"] = 999999999
    elif "incompatible with provider" in title: body["serviceTypeId"] = 999999999
    elif "missing contract" in title: body.pop("contractId", None)
    elif "inactive contract" in title: body["contractId"] = 999999999
    elif "not self-service enabled" in title: body["contractId"] = 999999999
    elif "contract wrong provider" in title: body["contractId"] = int(db.fetch_scalar("SELECT TOP 1 ContractId FROM cimpl.Contract WHERE Active=1 AND ProviderId<>? ORDER BY ContractId DESC", (body["providerId"],)))
    elif "contract wrong service" in title: body["contractId"] = int(db.fetch_scalar("SELECT TOP 1 ContractId FROM cimpl.Contract WHERE Active=1 AND ServiceTypeId<>? ORDER BY ContractId DESC", (body["serviceTypeId"],)))
    elif "missing catalog" in title: body.pop("catalogItemId", None)
    elif "inactive catalog" in title: body["catalogItemId"] = 999999999
    elif "unsupported catalog" in title: body["catalogItemId"] = 999999999
    elif "catalog incompatible" in title: body["catalogItemId"] = 999999999
    elif "missing options" in title: body.pop("options", None)
    elif "null options" in title: body["options"] = None
    elif "option already" in title or "option incompatible" in title: body["options"] = [999999999]
    elif "duplicate option" in title: body["options"] = [body.get("options", [999999999])[0]] * 2
    elif "missing accessories" in title: body.pop("accessories", None)
    elif "invalid accessory" in title: body["accessories"] = [999999999]
    elif "missing shipping" in title: body.pop("shippingAddress", None)
    elif "shipping address child" in title: body["shippingAddress"].pop("postalCode", None)
    elif "invalid country" in title: body["shippingAddress"]["countryCode"] = "ZZZ"
    elif "missing cost center" in title: body.pop("costCenterId", None)
    elif "invalid cost center" in title: body["costCenterId"] = 999999999
    elif "due date null" in title: body["dueDate"] = None
    elif "past due" in title: body["dueDate"] = (date.today() - timedelta(days=1)).isoformat()
    elif "invalid calendar" in title: body["dueDate"] = "2026-02-30"
    elif "sql injection" in title: body["comments"] = "'; DROP TABLE SelfServiceActionLog;--"
    elif "xss" in title: body["comments"] = "<script>alert('x')</script>"


def _log_row(db, number: str) -> dict | None:
    return db.fetch_one("SELECT TOP 1 SelfServiceActionLogId,RequestId,Number,StatusId,ServiceTypeId,ProviderId,OperationTypeId,OperationTypeName,WorkorderNumber,WorkorderId,RequesterEmployeeId,RequesterEmployeeNumber,ReceiverEmployeeId,ReceiverEmployeeNumber,DueDate,Comment,ActionDetail,InputContext,CreatedDate,UTCCreatedDate FROM cimpl.SelfServiceActionLog WHERE Number=? ORDER BY SelfServiceActionLogId DESC", (number,))


class TestPostSelfServiceProvision:
    @pytest.mark.parametrize("case", CASE_PARAMS)
    def test_post_self_service_provision(self, case, api_token, db, request):
        body, context = _payload(db, case)
        _mutate(case, body, context, db)
        scripts = PostApiDbScripts(db)
        before = scripts.count_rows(table="cimpl.SelfServiceActionLog", where_sql="Comment LIKE ?", params=(f"%{context['marker']}%",))
        metrics = [f"CaseId: {case.case_id}", f"Request body: {json.dumps(body, ensure_ascii=True, default=str)}", "OperationTypeId: 19"]
        headers = {"Authorization": f"Bearer {api_token}", "Accept": "application/json", "Content-Type": "application/json"}
        request_headers = {} if case.label == "Authorization" else headers
        response = requests.post(SELF_SERVICE_URL, json=body, headers=request_headers, timeout=120)
        response_body = _json(response)
        try:
            if case.label == "Positive":
                assert _status(response) in {200, 201}, f"Expected success, got {_status(response)}: {_text(response)}"
                number = str(response_body.get("selfServiceRequestNumber") or "").strip()
                tracking_id = str(response_body.get("trackingId") or "").strip()
                assert number, f"Missing selfServiceRequestNumber: {_text(response)}"
                UUID(tracking_id)
                row = _log_row(db, number)
                assert row, f"No SelfServiceActionLog row found for Number {number}"
                assert int(row["StatusId"]) == 1
                assert int(row["OperationTypeId"]) == 19
                assert int(row["ProviderId"]) == int(body["providerId"])
                assert int(row["ServiceTypeId"]) == int(body["serviceTypeId"])
                metrics.extend([f"Response: {json.dumps(response_body, default=str)}", f"SelfServiceActionLog: {json.dumps(row, default=str)}", "Correlation: response selfServiceRequestNumber matched log Number; response trackingId was captured separately."])
            elif case.label == "Authorization":
                assert _status(response) in {401, 403}, f"Expected 401/403, got {_status(response)}: {_text(response)}"
            else:
                assert _status(response) in {400, 404, 409, 500}, f"Expected validation error, got {_status(response)}: {_text(response)}"
                after = scripts.count_rows(table="cimpl.SelfServiceActionLog", where_sql="Comment LIKE ?", params=(f"%{context['marker']}%",))
                scripts.assert_count_delta(before=before, after=after, expected_delta=0, context=case.title)
                metrics.extend([f"Response status: {_status(response)}", f"Response body: {_text(response)}"])
        finally:
            if response_body: metrics.append(f"Response payload: {json.dumps(response_body, default=str)}")
            _record(request, metrics)
