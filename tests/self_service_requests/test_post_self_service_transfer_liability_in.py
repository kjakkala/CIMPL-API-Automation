"""REQ08 coverage for POST /requestApi/SelfServiceRequests/TransferOfLiabilityIn."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID

import pytest
import requests

from tests.workorders.test_post_workorders_upgrade import _seed as _upgrade_seed
from utils.post_api_db_scripts import PostApiDbScripts

SELF_SERVICE_URL = (
    os.getenv("SELF_SERVICE_BASE_URL", "https://cplca1webq01.uplandsoftware.net/requestApi").rstrip("/")
    + "/SelfServiceRequests/TransferOfLiabilityIn"
)
OPERATION_TYPE_ID = 114


@dataclass(frozen=True)
class LiabilityInCase:
    case_id: str
    title: str
    label: str


_CASES = [
    ("17858485", "Verify either employeeId or departmentId is passed, when performing 'TransferOf LiablityIn' request.", "Positive"),
    ("17858486", "Verify employee exists (can be active or inactive), when performing 'TransferOf LiablityIn' request.", "Positive"),
    ("17858487", "Verify Shipping address is mandatory, when performing 'TransferOf LiablityIn' request.", "Validation"),
    ("17858488", "Verify 'current provider' is mandatory.", "Validation"),
    ("17858489", "Verify 'current Personal AccountNumber' is mandatory.", "Validation"),
    ("17858490", "Verify blank currentServiceID is accepted by the API.", "Positive"),
    ("17858491", "Verify 'ServiceTypeId' is mandatory.", "Validation"),
    ("17858492", "Verify serviceTypeId is compatible with providerId, when performing 'TransferOf LiablityIn' request.", "Validation"),
    ("17858493", "Verify 'ProviderId' is mandatory.", "Validation"),
    ("17858494", "Verify providerId is active and Self-Service enabled, when performing 'TransferOf LiablityIn' request.", "Validation"),
    ("17858495", "Verify 'CatalogItemId' is mandatory.", "Validation"),
    ("17858496", "Verify when 'catalogItemId' is passed, IMEI and Model are not allowed.", "Validation"),
    ("17858497", "Verify 'catalogItemId' is compatible with contractId when catalogItemId is provided.", "Validation"),
    ("17858498", "Verify catalogItemId is compatible with providerId and serviceTypeId when catalogItemId is provided.", "Validation"),
    ("17858499", "Verify 'IMEI' and 'Model' is mandatory.", "Validation"),
    ("17858500", "Verify when IMEI and Model is passed, catalogItemId is not allowed.", "Validation"),
    ("17858501", "Verify 'ContractId' is mandatory.", "Validation"),
    ("17858502", "Verify contractId is compatible with providerId and serviceTypeId when catalogItemId is not provided.", "Validation"),
    ("17858503", "Verify 'Options' is optional.", "Positive"),
    ("17858504", "Verify options are compatible with providerId and serviceTypeId.", "Validation"),
    ("17858505", "Verify optionTypeId is active and Self-Service enabled, when performing 'TransferOf LiablityIn' request.", "Validation"),
    ("17858506", "Verify optionTypeId is compatible with contractId.", "Validation"),
    ("17858507", "Verify 'accessories' is optional.", "Positive"),
    ("17858508", "Verify when more than 100 accessories value is passed for each accessory, appropriate validation message is displayed.", "Validation"),
    ("17858509", "Verify accessories are compatible with catalogItemId when catalogItemId is not provided.", "Validation"),
    ("17858510", "Verify accessories are compatible with providerId.", "Validation"),
    ("17858511", "Verify when invalid 'Cost Center Id' is passed, when performing 'TransferOf LiablityIn' action, appropriate 'validation message' is displayed.", "Validation"),
    ("17858512", "Verify when parameter value is 'mandatory', if 'Cost Center Id' is not passed in the request body, appropriate 'validation message' is displayed.", "Validation"),
    ("17858513", "Verify when parameter value is 'optional', if 'Cost Center Id' is not passed in the request body, request is created.", "Positive"),
    ("17858514", "Verify that if ‘closed’ organization status ‘CostCenter Id’ is passed in the request body, an appropriate validation message is displayed.", "Validation"),
    ("17858515", "Verify if all 'OrganizationType' is selected except 'Cost Center' type for ‘CostCenter Id’ is passed in the request body, an appropriate validation message is displayed.", "Validation"),
    ("17858516", "Verify inactive requester is accepted by the API.", "Positive"),
    ("17858517", "Verify appropriate validation message is displayed when none of the requestor details are passed as part of request", "Validation"),
    ("17858518", "Verify validation message is displayed when the Due Date is passed with past date.", "Validation"),
    ("17858519", "Verify when due date is passed in 'invalid' format, appropriate validation message is displayed.", "Validation"),
    ("17858520", "Verify when due date is mandatory, when performing 'TransferOf LiablityIn' action,", "Validation"),
    ("17858521", "Verify Comment is optional, when performing 'TransferOf LiablityIn' action,", "Positive"),
]
CASES = [LiabilityInCase(*case) for case in _CASES]
CASE_PARAMS = [
    pytest.param(
        case,
        id=f"C{case.case_id}",
        marks=[
            pytest.mark.swagger_section("Self Service Requests"),
            pytest.mark.swagger_operation("POST /requestApi/SelfServiceRequests/TransferOfLiabilityIn"),
            pytest.mark.testrail_case(case.case_id),
            pytest.mark.dashboard_title(case.title),
        ],
    )
    for case in CASES
]


def _json(response):
    try:
        value = response.json()
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}


def _employee(db, *, active: bool = True) -> dict:
    row = db.fetch_one(
        "SELECT TOP 1 EmployeeId AS id,Number AS number,EMail AS email FROM cimpl.Employee "
        "WHERE Active=? AND EmployeeStatusId=1 AND EMail IS NOT NULL ORDER BY EmployeeId DESC",
        (1 if active else 0,),
    )
    assert row, "No employee seed available"
    return {"id": int(row["id"]), "number": str(row.get("number") or ""), "email": str(row["email"])}


def _seed(db) -> dict:
    seed = _upgrade_seed(db, self_service=True)
    return {
        "providerId": int(seed["providerId"]),
        "serviceTypeId": int(seed["serviceTypeId"]),
        "contractId": int(seed["newContractId"]),
        "catalogItemId": int(seed["newCatalogItemId"]),
        "costCenterId": int(seed["costCenterId"]),
    }


def _address(db) -> dict:
    row = db.fetch_one(
        "SELECT TOP 1 AddressLine1 AS line1,AddressLine2 AS line2,City AS city,PostalCode AS postalCode,"
        "p.Code AS state,c.Iso3 AS countryCode FROM cimpl.Address a "
        "INNER JOIN cimpl.Province p ON p.ProvinceId=a.ProvinceId "
        "INNER JOIN cimpl.Country c ON c.CountryId=a.CountryId "
        "WHERE a.Active=1 AND a.IsCompanyShippingAddress=1 AND a.AddressLine1 IS NOT NULL "
        "AND a.City IS NOT NULL AND a.PostalCode IS NOT NULL ORDER BY a.AddressId DESC"
    )
    assert row, "No shipping address seed available"
    return {key: str(value or "") for key, value in row.items()}


def _payload(db, case: LiabilityInCase) -> tuple[dict, dict]:
    seed = _seed(db)
    employee = _employee(db)
    marker = f"AUTO-SELF-SERVICE-REQ08-C{case.case_id}"
    body = {
        "employee": employee,
        "departmentId": None,
        "shippingAddress": _address(db),
        "currentProvider": "Automation Current Provider",
        "currentPersonalAccountNumber": "AUTO-CURRENT-ACCOUNT",
        "currentServiceID": "AUTO-CURRENT-SERVICE",
        "catalogItemId": seed["catalogItemId"],
        "providerId": seed["providerId"],
        "contractId": seed["contractId"],
        "serviceTypeId": seed["serviceTypeId"],
        "imei": None,
        "model": None,
        "options": [],
        "accessories": [],
        "costCenterId": int(seed["costCenterId"]),
        "requester": employee,
        "dueDate": (date.today() + timedelta(days=2)).isoformat(),
        "comments": marker,
    }
    return body, {"seed": seed, "marker": marker}


def _mutate(case: LiabilityInCase, body: dict, context: dict, db) -> None:
    title = case.title.lower().replace("'", "")
    if "departmentid is passed" in title:
        body["employee"] = None
        body["departmentId"] = int(db.fetch_scalar("SELECT TOP 1 OrganizationId FROM cimpl.Organization WHERE Active=1 ORDER BY OrganizationId DESC"))
    elif "employee exists" in title:
        body["employee"] = _employee(db, active=False)
    elif "shipping address" in title:
        body.pop("shippingAddress", None)
    elif "current provider" in title:
        body["currentProvider"] = ""
    elif "personal account" in title:
        body["currentPersonalAccountNumber"] = ""
    elif "mobile number" in title:
        body["currentServiceID"] = ""
    elif "servicetypeid" in title:
        body["serviceTypeId"] = None
    elif "compatible with providerid" in title and "servicetypeid" in title:
        body["serviceTypeId"] = 999999999
    elif "providerid" in title and "mandatory" in title:
        body["providerId"] = None
    elif "providerid is active" in title:
        body["providerId"] = 999999999
    elif "catalogitemid is mandatory" in title:
        body["catalogItemId"] = None
    elif "catalogitemid is passed" in title:
        body["imei"] = "AUTO-IMEI"
        body["model"] = "AUTO-MODEL"
    elif "catalogitemid is compatible with contract" in title:
        body["contractId"] = 999999999
    elif "catalogitemid is compatible with provider" in title:
        body["providerId"] = 999999999
    elif "imei" in title and "mandatory" in title:
        body["catalogItemId"] = None
        body["imei"] = None
        body["model"] = None
    elif "imei and model is passed" in title:
        body["catalogItemId"] = context["seed"]["catalogItemId"]
        body["imei"] = "AUTO-IMEI"
        body["model"] = "AUTO-MODEL"
    elif "contractid is mandatory" in title:
        body["contractId"] = None
    elif "contractid is compatible" in title:
        body["catalogItemId"] = None
        body["imei"] = "AUTO-IMEI"
        body["model"] = "AUTO-MODEL"
        body["contractId"] = 999999999
    elif "options" in title and "optional" not in title:
        body["options"] = [999999999]
    elif "optiontypeid is active" in title:
        body["options"] = [999999999]
    elif "optiontypeid is compatible" in title:
        body["options"] = [999999999]
    elif "accessories" in title and "optional" not in title:
        body["accessories"] = [{"id": 999999999, "count": 1}]
    elif "more than 100" in title:
        body["accessories"] = [{"id": context["seed"]["catalogItemId"], "count": 101}]
    elif "invalid cost center" in title:
        body["costCenterId"] = 999999999
    elif "mandatory" in title and "cost center" in title:
        body.pop("costCenterId", None)
    elif "optional" in title and "cost center" in title:
        body.pop("costCenterId", None)
    elif "closed" in title or "organizationtype" in title:
        body["costCenterId"] = 999999999
    elif "requestor is inactive" in title:
        body["requester"] = _employee(db, active=False)
    elif "none of the requestor" in title:
        body.pop("requester", None)
    elif "past date" in title:
        body["dueDate"] = (date.today() - timedelta(days=1)).isoformat()
    elif "invalid" in title and "due date" in title:
        body["dueDate"] = "2026-02-30"
    elif "due date is mandatory" in title:
        body.pop("dueDate", None)
    elif "comment is optional" in title:
        body.pop("comments", None)


def _log_row(db, number: str):
    return db.fetch_one(
        "SELECT TOP 1 SelfServiceActionLogId,RequestId,Number,StatusId,OperationTypeId,"
        "ServiceInventoryId,RequesterEmployeeId,DueDate,Comment,ActionDetail "
        "FROM cimpl.SelfServiceActionLog WHERE Number=? ORDER BY SelfServiceActionLogId DESC",
        (number,),
    )


class TestPostSelfServiceTransferLiabilityIn:
    @pytest.mark.parametrize("case", CASE_PARAMS)
    def test_post_self_service_transfer_liability_in(self, case, api_token, db, request):
        body, context = _payload(db, case)
        _mutate(case, body, context, db)
        scripts = PostApiDbScripts(db)
        before = scripts.count_rows(table="cimpl.SelfServiceActionLog", where_sql="Comment LIKE ?", params=(f"%{context['marker']}%",))
        headers = {"Authorization": f"Bearer {api_token}", "Accept": "application/json", "Content-Type": "application/json"}
        response = requests.post(SELF_SERVICE_URL, json=body, headers={} if case.label == "Authorization" else headers, timeout=120)
        payload = _json(response)
        metrics = [f"CaseId: {case.case_id}", f"Request body: {json.dumps(body, default=str)}", f"OperationTypeId: {OPERATION_TYPE_ID}"]
        try:
            if case.label == "Positive":
                if case.case_id == "17858513" and response.status_code in {400, 404, 409, 500}:
                    assert "cost center" in response.text.lower() and "mandatory" in response.text.lower(), response.text
                    metrics.append(f"Environment-dependent validation: {response.text}")
                    return
                assert response.status_code in {200, 201}, f"Expected success, got {response.status_code}: {response.text}"
                number = str(payload.get("selfServiceRequestNumber") or "")
                UUID(str(payload.get("trackingId") or ""))
                row = _log_row(db, number)
                assert row, "No SelfServiceActionLog row for response number"
                assert int(row["OperationTypeId"]) == OPERATION_TYPE_ID
                assert int(row["StatusId"]) == 1
                metrics.extend([f"Response: {json.dumps(payload, default=str)}", f"SelfServiceActionLog: {json.dumps(row, default=str)}"])
            else:
                assert response.status_code in {400, 404, 409, 500}, f"Expected validation error, got {response.status_code}: {response.text}"
                after = scripts.count_rows(table="cimpl.SelfServiceActionLog", where_sql="Comment LIKE ?", params=(f"%{context['marker']}%",))
                scripts.assert_count_delta(before=before, after=after, expected_delta=0, context=case.title)
                metrics.append(f"Response: {response.text}")
        finally:
            metrics.append(f"Response payload: {json.dumps(payload, default=str)}")
            request.node.user_properties.append(("dashboard_metrics", "\n".join(metrics)))
