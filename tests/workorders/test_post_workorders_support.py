"""Data-driven validation for POST /v1/workorders/support."""

from __future__ import annotations

import csv
import json
import os
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pytest

from utils.post_api_db_scripts import PostApiDbScripts


@dataclass(frozen=True)
class SupportCase:
    case_id: str
    title: str
    expected: str
    label: str
    preconditions: str


FALLBACK_CASES = [
    SupportCase("17794738", "Description missing", "Validation error returned", "Validation", "description field omitted"),
    SupportCase("17794739", "Description is null", "Validation error returned", "Validation", "description=null"),
    SupportCase("17794740", "Description is empty", "Validation error returned", "Validation", 'description=""'),
    SupportCase("17794741", "Service missing", "Validation error returned", "Validation", "service omitted"),
    SupportCase("17794742", "Requester missing", "Validation error returned", "Validation", "requester omitted"),
    SupportCase("17794743", "Action is null", "Workorder created successfully with the default action", "Validation", "action=null"),
    SupportCase("17794744", "Invalid action value", "Validation error returned and workorder not created", "Validation", 'action="InvalidValue"'),
    SupportCase("17794745", "Action lowercase support", "Verify API validation and expected behavior", "Validation", 'action="support"'),
    SupportCase("17794746", "Action lowercase repair", "Verify API validation and expected behavior", "Validation", 'action="repair"'),
    SupportCase("17794747", "Action contains numeric value", "Validation error returned", "Validation", 'action="123"'),
    SupportCase("17794750", "Invalid requester email format", "Validation error returned", "Validation", 'email="abc@"'),
    SupportCase("17794751", "Non-existing requester email", "Validation error returned", "Validation", "Unknown email address"),
    SupportCase("17794752", "Invalid requester ID", "Validation error returned", "Validation", "id=-1"),
    SupportCase("17794753", "Invalid requester number", "Validation error returned", "Validation", 'number="INVALID"'),
    SupportCase("17794754", "Empty requester object", "Validation error returned", "Validation", "requester={}"),
    SupportCase("17794755", "Requester identifiers belong to different users", "Validation error returned", "Validation", "id user A, email user B"),
    SupportCase("17794757", "Invalid providerId", "Validation error returned", "Validation", "providerId invalid"),
    SupportCase("17794758", "Invalid accountNumber", "Validation error returned", "Validation", "accountNumber invalid"),
    SupportCase("17794759", "Invalid serviceNumber", "Validation error returned", "Validation", "serviceNumber invalid"),
    SupportCase("17794762", "Empty service object", "Validation error returned", "Validation", "service={}"),
    SupportCase("17794766", "Valid dueDate format", "Workorder created successfully", "Validation", "dueDate=2022-05-14"),
    SupportCase("17794770", "Invalid month in dueDate", "Validation error returned", "Validation", "2022-13-01"),
    SupportCase("17794771", "Invalid day in dueDate", "Validation error returned", "Validation", "2022-02-31"),
    SupportCase("17794772", "Alphabetic dueDate", "Validation error returned", "Validation", "abcd-ef-gh"),
    SupportCase("17794773", "Blank dueDate", "Workorder created successfully with no due date", "Validation", 'dueDate=""'),
    SupportCase("17794774", "Valid leap year date", "Accepted successfully", "Validation", "2024-02-29"),
    SupportCase("17794775", "Invalid leap year date", "Validation error returned", "Validation", "2023-02-29"),
    SupportCase("17794733", "Create support workorder with all valid fields", "Workorder created successfully and success response returned", "Positive", ""),
    SupportCase("17794734", "Create workorder without action", "Workorder created with default action=Support", "Positive", ""),
    SupportCase("17794735", "Create workorder with action=Repair", "Workorder created successfully with Repair action", "Positive", ""),
    SupportCase("17794736", "Create workorder without dueDate", "Workorder created successfully", "Positive", ""),
    SupportCase("17794737", "Create workorder without comments", "Workorder created successfully", "Positive", ""),
    SupportCase("17794748", "Valid requester ID with invalid email and number", "Workorder not created and validation error returned", "Regression", ""),
    SupportCase("17794749", "Invalid requester ID with valid email and number", "Workorder not created and validation error returned", "Regression", ""),
    SupportCase("17794756", "Valid service identifiers", "Workorder created successfully", "Positive", ""),
    SupportCase("17794760", "ProviderId incompatible with accountNumber", "Validation error returned", "Regression", ""),
    SupportCase("17794761", "AccountNumber incompatible with serviceNumber", "Validation error returned", "Regression", ""),
    SupportCase("17794767", "Invalid dueDate MM-DD-YYYY", "Validation error returned", "Regression", "06-14-2022"),
    SupportCase("17794768", "Invalid dueDate DD-MM-YYYY", "Validation error returned", "Regression", "14-06-2022"),
    SupportCase("17794769", "Invalid dueDate YYYY/MM/DD", "Validation error returned", "Regression", "2022/06/14"),
    SupportCase("17794763", "Service does not exist", "Validation error returned", "Business Rule", ""),
    SupportCase("17794764", "Service already has open workorder", "Validation error returned and new workorder not created", "Business Rule", ""),
    SupportCase("17794765", "Inactive service", "Validation error returned and workorder not created", "Business Rule", ""),
    SupportCase("17794776", "Maximum supported description length", "Workorder created successfully", "Boundary", ""),
    SupportCase("17794777", "Description exceeds max length", "Validation error returned", "Boundary", ""),
    SupportCase("17794778", "SQL injection in description", "Request handled safely and no unintended behavior", "Security", ""),
    SupportCase("17794779", "Script injection in comments", "Input sanitized or validation error returned", "Security", ""),
    SupportCase("17794780", "Unicode characters in description", "Request handled successfully", "Security", ""),
    SupportCase("17794781", "Verify response schema", "Response matches documented schema", "Contract", ""),
    SupportCase("17794782", "Verify action value in response", "Returned response contains Repair action", "Contract", ""),
    SupportCase("17794783", "Verify dueDate persistence", "Workorder persists the same dueDate", "Contract", ""),
    SupportCase("17794784", "Verify comments in response", "Returned response contains same comments", "Contract", ""),
]


def _load_cases() -> list[SupportCase]:
    paths = (Path(__file__).with_name("cimpl (12).csv"), Path.home() / "Downloads" / "cimpl (12).csv")
    path = next((candidate for candidate in paths if candidate.exists()), None)
    if path is None:
        return FALLBACK_CASES
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return [
            SupportCase(
                str(row["ID"]).strip().removeprefix("C"),
                str(row["Title"]).strip(), str(row["Expected Result"]).strip(),
                str(row["Labels"]).strip(), str(row["Preconditions"]).strip(),
            )
            for row in csv.DictReader(stream)
        ]


CASES = _load_cases()
CASES.extend([
    SupportCase("17838348", "Requester null", "Validation error returned", "Validation", "requester=null"),
    SupportCase("17838349", "Requester ID only", "Validation error returned", "Validation", "requester.id only"),
    SupportCase("17838350", "Requester email only", "Validation error returned", "Validation", "requester.email only"),
    SupportCase("17838351", "Requester number only", "Validation error returned", "Validation", "requester.number only"),
    SupportCase("17838352", "Inactive requester", "Validation error returned", "Validation", "requester is inactive"),
    SupportCase("17838353", "Service null", "Validation error returned", "Validation", "service=null"),
    SupportCase("17838354", "Service ID only", "Validation error returned", "Validation", "service.id only"),
    SupportCase("17838355", "Service ID and providerId only", "Validation error returned", "Validation", "service.id and service.providerId only"),
    SupportCase("17838356", "Due date null", "Validation error returned", "Validation", "dueDate=null"),
    SupportCase("17838357", "Due date current date", "Workorder created successfully", "Positive", "dueDate=today"),
    SupportCase("17838358", "Due date leap year", "Workorder created successfully", "Positive", "dueDate=2028-02-29"),
    SupportCase("17838359", "Comments null", "Validation error returned", "Validation", "comments=null"),
    SupportCase("17838360", "Comments Unicode characters", "Workorder created successfully", "Positive", "comments contains Unicode"),
    SupportCase("17838361", "Comments over 4000 characters", "Validation error returned", "Validation", "comments length > 4000"),
    SupportCase("17838410", "Service with valid provider from another service", "Validation error returned", "Validation", "valid provider belongs to another service"),
    SupportCase("17838411", "Service with valid account from another provider", "Validation error returned", "Validation", "valid account belongs to another provider"),
    SupportCase("17838412", "Service with valid service number from another service", "Validation error returned", "Validation", "valid service number belongs to another service"),
])
for case_index, case in enumerate(CASES):
    if case.case_id == "17794743":
        CASES[case_index] = SupportCase(
            case.case_id,
            case.title,
            "Workorder created successfully with the default action",
            case.label,
            case.preconditions,
        )
    elif case.case_id == "17794773":
        CASES[case_index] = SupportCase(
            case.case_id,
            case.title,
            "Workorder created successfully with no due date",
            case.label,
            case.preconditions,
        )
    elif case.case_id == "17794765":
        CASES[case_index] = SupportCase(
            case.case_id,
            case.title,
            "Validation error returned and workorder not created because only ServiceInventoryStatusId 1 is accepted",
            case.label,
            case.preconditions,
        )
    elif case.case_id == "17794783":
        CASES[case_index] = SupportCase(
            case.case_id,
            "Verify dueDate persistence",
            "Workorder persists the same dueDate",
            case.label,
            case.preconditions,
        )
CASE_PARAMS = [
    pytest.param(case, id=f"C{case.case_id}", marks=[
        pytest.mark.swagger_section("Workorders"), pytest.mark.swagger_operation("POST /v1/workorders/support"),
        pytest.mark.testrail_case(case.case_id), pytest.mark.dashboard_title(case.title),
    ])
    for case in CASES
]


def _json(response) -> dict:
    try:
        value = response.json()
        return value if isinstance(value, dict) else {"raw": value}
    except Exception:  # noqa: BLE001
        return {}


def _response_text(response) -> str:
    return str(response.text())


def _record_metrics(request, metrics: list[str]):
    request.node.user_properties.append(("dashboard_metrics", "\n".join(metrics)))


def _employees(db, exclude_id: int | None = None, limit: int = 10) -> list[dict]:
    clause = "AND EmployeeId <> ?" if exclude_id is not None else ""
    params = (exclude_id,) if exclude_id is not None else ()
    rows = db.fetch_all(
        """SELECT TOP (?) EmployeeId AS id, Number AS number, EMail AS email
           FROM cimpl.Employee WHERE EmployeeStatusId = 1 AND EmployeeId IS NOT NULL
           AND EMail IS NOT NULL """ + clause + " ORDER BY EmployeeId DESC", (limit, *params))
    assert rows, "No active employee with an email is available for the requester"
    return [
        {"id": int(row["id"]), "number": str(row.get("number") or ""), "email": str(row["email"])}
        for row in rows
    ]


def _employee(db, exclude_id: int | None = None) -> dict:
    return _employees(db, exclude_id=exclude_id, limit=1)[0]


def _requester_for_case(db, case_id: str) -> dict:
    requesters = _employees(db)
    return requesters[int(case_id) % len(requesters)]


def _eligible_service(db, *, provider_id: int | None = None, service_type_id: int | None = None) -> dict:
    provider_filter = " AND si.ProviderId=?" if provider_id is not None else ""
    service_type_filter = " AND si.ServiceTypeId=?" if service_type_id is not None else ""
    params = tuple(value for value in (provider_id, service_type_id) if value is not None)
    row = db.fetch_one(
        """SELECT TOP 1 si.ServiceInventoryId AS serviceInventoryId, a.Number AS accountNumber,
                  si.ProviderId AS providerId, si.PrimaryNumber AS serviceNumber,
                  si.ServiceInventoryStatusId AS serviceInventoryStatusId,
                  latest.TransactId AS latestTransactId, status_lookup.Name AS latestTransactStatus
           FROM cimpl.ServiceInventory si
           INNER JOIN cimpl.Account a ON a.AccountId = si.AccountId
           OUTER APPLY (SELECT TOP 1 tsi.TransactId,tsi.TransactStatusId FROM cimpl.TransactServiceInventory tsi
                        WHERE tsi.ServiceInventoryId = si.ServiceInventoryId ORDER BY tsi.TransactId DESC) latest
           LEFT JOIN cimpl.Transact t ON t.TransactId = latest.TransactId
           LEFT JOIN cimpl.TransactStatus status_lookup ON status_lookup.TransactStatusId = t.TransactStatusId
                     WHERE si.ServiceInventoryStatusId <> 7 AND ISNULL(t.TransactStatusId,0)<>1
                         AND si.ProviderId IS NOT NULL AND si.PrimaryNumber IS NOT NULL AND a.Number IS NOT NULL
                         AND NOT EXISTS (SELECT 1 FROM cimpl.TransactWorkorderService tws INNER JOIN cimpl.WorkorderSummary ws ON ws.WorkorderId=tws.WorkorderId INNER JOIN cimpl.WorkorderStatus wos ON wos.WorkorderStatusId=ws.WorkorderStatusId AND wos.TenantId=ws.TenantId WHERE tws.ServiceInventoryId=si.ServiceInventoryId AND tws.WorkorderId=(SELECT TOP 1 latest_wo.WorkorderId FROM cimpl.TransactWorkorderService latest_wo WHERE latest_wo.ServiceInventoryId=si.ServiceInventoryId ORDER BY latest_wo.WorkorderId DESC) AND LOWER(wos.Name) NOT IN ('completed','cancelled','canceled'))"""
        + provider_filter + service_type_filter +
        """ ORDER BY si.ServiceInventoryId DESC""",
        params,
    )
    assert row, "No eligible service for the selected filters: require ServiceInventoryStatusId <> 7 and no latest transaction"
    return {key: row[key] for key in row}


def preview_seed_compatibility(db, *, provider_id: int | None = None, service_type_id: int | None = None, limit: int = 2) -> list[dict]:
    """Support has no catalog/contract compatibility rules; any eligible active service qualifies."""
    provider_filter = " AND si.ProviderId=?" if provider_id is not None else ""
    service_type_filter = " AND si.ServiceTypeId=?" if service_type_id is not None else ""
    params = [limit] + [value for value in (provider_id, service_type_id) if value is not None]
    rows = db.fetch_all(
        """SELECT DISTINCT TOP (?) si.ProviderId AS providerId, p.Name AS providerName, si.ServiceTypeId AS serviceTypeId, st.Name AS serviceTypeName
           FROM cimpl.ServiceInventory si
           INNER JOIN cimpl.Account a ON a.AccountId = si.AccountId
           INNER JOIN cimpl.Provider p ON p.ProviderId = si.ProviderId
           INNER JOIN cimpl.ServiceType st ON st.ServiceTypeId = si.ServiceTypeId
           OUTER APPLY (SELECT TOP 1 tsi.TransactId,tsi.TransactStatusId FROM cimpl.TransactServiceInventory tsi
                        WHERE tsi.ServiceInventoryId = si.ServiceInventoryId ORDER BY tsi.TransactId DESC) latest
                       WHERE si.ServiceInventoryStatusId <> 7 AND ISNULL(latest.TransactStatusId,0)<>1
                         AND si.ProviderId IS NOT NULL AND si.PrimaryNumber IS NOT NULL AND a.Number IS NOT NULL"""
        + provider_filter + service_type_filter +
        """ ORDER BY si.ProviderId DESC""",
        tuple(params),
    )
    return [
        {"providerId": int(row["providerId"]), "providerName": str(row.get("providerName") or ""), "serviceTypeId": int(row["serviceTypeId"]), "serviceTypeName": str(row.get("serviceTypeName") or "")}
        for row in rows
    ]


def _service_payload(row: dict) -> dict:
    return {
        "id": int(row["serviceInventoryId"]),
        "accountNumber": str(row["accountNumber"]),
        "providerId": int(row["providerId"]),
        "serviceNumber": str(row["serviceNumber"]),
    }


def _deactivated_service(db) -> dict:
    row = db.fetch_one(
        """SELECT TOP 1 si.ServiceInventoryId AS serviceInventoryId, a.Number AS accountNumber,
                  si.ProviderId AS providerId, si.PrimaryNumber AS serviceNumber
           FROM cimpl.ServiceInventory si INNER JOIN cimpl.Account a ON a.AccountId = si.AccountId
           WHERE si.ServiceInventoryStatusId = 2 AND si.ProviderId IS NOT NULL
             AND si.PrimaryNumber IS NOT NULL AND a.Number IS NOT NULL
           ORDER BY si.ServiceInventoryId DESC""")
    assert row, "No deactivated service inventory record is available"
    return _service_payload(row)


def _service_with_open_workorder(db) -> dict:
    row = db.fetch_one(
        """SELECT TOP 1 si.ServiceInventoryId AS serviceInventoryId, a.Number AS accountNumber,
                  si.ProviderId AS providerId, si.PrimaryNumber AS serviceNumber, tws.WorkorderId AS workorderId
           FROM cimpl.TransactWorkorderService tws
           INNER JOIN cimpl.WorkorderSummary ws ON ws.WorkorderId = tws.WorkorderId
           INNER JOIN cimpl.OpenWorkorderStatusView open_status ON open_status.WorkorderStatusId = ws.WorkorderStatusId
           INNER JOIN cimpl.ServiceInventory si ON si.ServiceInventoryId = tws.ServiceInventoryId
           INNER JOIN cimpl.Account a ON a.AccountId = si.AccountId
           WHERE si.ProviderId IS NOT NULL AND si.PrimaryNumber IS NOT NULL AND a.Number IS NOT NULL
           ORDER BY tws.WorkorderId DESC""")
    assert row, "No service linked to an open workorder is available"
    return _service_payload(row)


def _payload(db, case: SupportCase) -> tuple[dict, dict]:
    requester = _requester_for_case(db, case.case_id)
    provider_id = int(os.getenv("TEST_PROVIDER_ID")) if os.getenv("TEST_PROVIDER_ID", "").strip() else None
    service_type_id = int(os.getenv("TEST_SERVICE_TYPE_ID")) if os.getenv("TEST_SERVICE_TYPE_ID", "").strip() else None
    service = _eligible_service(db, provider_id=provider_id, service_type_id=service_type_id)
    marker = f"AUTO-SUPPORT-C{case.case_id}-{date.today():%Y%m%d}"
    body = {
        "requester": requester, "dueDate": (date.today() + timedelta(days=2)).isoformat(),
        "comments": marker, "service": _service_payload(service),
        "action": "Support", "description": f"{marker} support workorder",
    }
    return body, service


def _apply_scenario(case: SupportCase, body: dict, db) -> dict | None:
    title = case.title.lower()
    if "valid provider from another service" in title or "valid account from another provider" in title or "valid service number from another service" in title:
        other = db.fetch_one("SELECT TOP 1 si.ProviderId AS providerId, a.Number AS accountNumber, si.PrimaryNumber AS serviceNumber FROM cimpl.ServiceInventory si INNER JOIN cimpl.Account a ON a.AccountId=si.AccountId WHERE si.ServiceInventoryId<>? AND si.ServiceInventoryStatusId=1 AND si.ProviderId IS NOT NULL AND si.PrimaryNumber IS NOT NULL AND a.Number IS NOT NULL ORDER BY si.ServiceInventoryId DESC", (body["service"]["id"],))
        assert other, "No alternate service compatibility seed available"
        if "provider" in title: body["service"]["providerId"] = int(other["providerId"])
        elif "account" in title: body["service"]["accountNumber"] = str(other["accountNumber"])
        else: body["service"]["serviceNumber"] = str(other["serviceNumber"])
    elif "requester null" in title: body["requester"] = None
    elif "requester id only" in title: body["requester"] = {"id": body["requester"]["id"]}
    elif "requester email only" in title: body["requester"] = {"email": body["requester"]["email"]}
    elif "requester number only" in title: body["requester"] = {"number": body["requester"]["number"]}
    elif "inactive requester" in title: body["requester"] = _employee(db, active=False)
    if "description missing" in title: body.pop("description")
    elif "description is null" in title: body["description"] = None
    elif "description is empty" in title: body["description"] = ""
    elif "maximum supported description" in title: body["description"] = "D" * 1000
    elif "exceeds max length" in title: body["description"] = "D" * 1001
    elif "sql injection" in title: body["description"] = "' OR 1=1 --"
    elif "unicode characters" in title: body["description"] = "\u65e5\u672c\u8a9e \u0627\u0644\u0639\u0631\u0628\u064a\u0629 \U0001f600"
    if "service null" in title: body["service"] = None
    elif "service id only" in title: body["service"] = {"id": body["service"]["id"]}
    elif "service id and providerid only" in title: body["service"] = {"id": body["service"]["id"], "providerId": body["service"]["providerId"]}
    elif "service missing" in title: body.pop("service")
    elif "empty service" in title: body["service"] = {}
    elif "invalid providerid" in title: body["service"]["providerId"] = 999999999
    elif "invalid accountnumber" in title: body["service"]["accountNumber"] = "INVALID-ACCOUNT"
    elif "invalid servicenumber" in title or "service does not exist" in title: body["service"]["serviceNumber"] = "INVALID-SERVICE"
    elif "providerid incompatible" in title: body["service"]["providerId"] = 999999999
    elif "accountnumber incompatible" in title: body["service"]["accountNumber"] = "INVALID-ACCOUNT"
    elif "service already has open workorder" in title: body["service"] = _service_with_open_workorder(db)
    elif "inactive service" in title: body["service"] = _deactivated_service(db)
    if "requester missing" in title: body.pop("requester")
    elif "empty requester" in title: body["requester"] = {}
    elif "invalid requester email" in title: body["requester"] = {"email": "abc@"}
    elif "non-existing requester" in title: body["requester"] = {"email": "unknown-auto@example.invalid"}
    elif "invalid requester id" in title: body["requester"] = {"id": -1}
    elif "invalid requester number" in title: body["requester"] = {"number": "INVALID"}
    elif "identifiers belong" in title or "valid requester id with invalid" in title:
        other = _employee(db, exclude_id=body["requester"]["id"])
        body["requester"].update({"email": other["email"], "number": other["number"]})
    elif "invalid requester id with valid" in title:
        body["requester"]["id"] = -1
    if "action is null" in title: body["action"] = None
    elif "invalid action" in title: body["action"] = "InvalidValue"
    elif "lowercase support" in title: body["action"] = "support"
    elif "lowercase repair" in title: body["action"] = "repair"
    elif "action contains numeric" in title: body["action"] = "123"
    elif "without action" in title: body.pop("action")
    elif "action=repair" in title or "action value in response" in title: body["action"] = "Repair"
    if "due date null" in title: body["dueDate"] = None
    elif "due date current" in title: body["dueDate"] = date.today().isoformat()
    elif "due date leap" in title: body["dueDate"] = "2028-02-29"
    elif "without duedate" in title: body.pop("dueDate")
    elif "invalid month" in title: body["dueDate"] = "2022-13-01"
    elif "invalid day" in title: body["dueDate"] = "2022-02-31"
    elif "alphabetic duedate" in title: body["dueDate"] = "abcd-ef-gh"
    elif "blank duedate" in title: body["dueDate"] = ""
    elif "invalid leap" in title: body["dueDate"] = "2023-02-29"
    elif "valid leap" in title: body["dueDate"] = "2028-02-29"
    elif "mm-dd" in title: body["dueDate"] = "06-14-2022"
    elif "dd-mm" in title: body["dueDate"] = "14-06-2022"
    elif "yyyy/mm" in title: body["dueDate"] = "2022/06/14"
    if "comments null" in title: body["comments"] = None
    elif "comments unicode" in title: body["comments"] = "\u65e5\u672c\u8a9e \u0627\u0644\u0639\u0631\u0628\u064a\u0629"
    elif "comments over 4000" in title: body["comments"] = "C" * 4001
    elif "without comments" in title: body.pop("comments")
    elif "script injection" in title: body["comments"] = "<script>alert(1)</script>"
    return body.get("service")


def _expects_success(case: SupportCase) -> bool:
    title = case.title.lower()
    if "invalid leap" in title or "invalid duedate" in title:
        return False
    return case.label in {"Positive", "Contract"} or any(value in title for value in (
        "valid due", "valid leap", "maximum supported", "sql injection", "unicode",
        "lowercase support", "lowercase repair", "blank duedate", "action is null", "due date current", "due date leap"))


def _allows_either_outcome(case: SupportCase) -> bool:
    return "script injection" in case.title.lower()


class TestPostWorkordersSupport:
    @pytest.mark.parametrize("case", CASE_PARAMS)
    def test_post_workorders_support(self, case, api_context, db, request):
        body, service = _payload(db, case)
        selected_service = _apply_scenario(case, body, db)
        if selected_service is not None:
            service = selected_service
        scripts = PostApiDbScripts(db)
        before = scripts.count_rows(table="cimpl.Workorder")
        metrics = [
            f"CaseId: {case.case_id}", f"Expected: {case.expected}",
            "Requester: " + json.dumps(body["requester"], ensure_ascii=True),
            "Eligible service: " + json.dumps(service, default=str, ensure_ascii=True),
            f"Workorder count before: {before}", f"Request body: {json.dumps(body, ensure_ascii=True)}",
        ]
        if "inactive service" in case.title.lower():
            metrics.append(
                "Service status rule: ServiceInventoryStatusId 1 (Active) is accepted; "
                "statuses 2 (Deactivated), 3 (Suspended), 4 (Spare), and 7 (Disconnected) are rejected."
            )
        response = api_context.post("workorders/support", data=json.dumps(body, ensure_ascii=True),
                                    headers={"Content-Type": "application/json", "accept": "application/json"})
        try:
            payload = _json(response)
            if _expects_success(case) or (_allows_either_outcome(case) and response.status in {200, 201}):
                assert response.status in {200, 201}, f"{case.title}: expected success, got {response.status}: {_response_text(response)}"
                number = str(payload.get("workorderNumber") or "").strip()
                assert number, f"{case.title}: response has no workorderNumber: {payload}"
                title = case.title.lower()
                if "action value in response" in title:
                    assert payload.get("action") == "Repair", f"{case.title}: expected Repair action in response: {payload}"
                elif "comments in response" in title:
                    assert payload.get("comments") == body.get("comments"), f"{case.title}: comments missing or changed in response: {payload}"
                after = scripts.count_rows(table="cimpl.Workorder")
                scripts.assert_count_delta(before=before, after=after, expected_delta=1, context=case.title)
                row = scripts.assert_single_row(query="""SELECT w.WorkorderId AS workorderId, w.Number AS workorderNumber,
                    ws.ProviderId AS providerId, ws.Comment AS comments, ws.DueDate AS dueDate, wr.EmployeeId AS requesterEmployeeId,
                    action_type.Name AS action
                    FROM cimpl.Workorder w LEFT JOIN cimpl.WorkorderSummary ws ON ws.WorkorderId = w.WorkorderId
                    LEFT JOIN cimpl.WorkorderRequester wr ON wr.WorkorderRequesterId = ws.WorkorderRequesterId
                    OUTER APPLY (SELECT TOP 1 action_lookup.Name AS Name
                        FROM cimpl.TransactWorkorderService tws
                        INNER JOIN cimpl.TransactServiceInventory tsi ON tsi.TransactId = tws.TransactId
                            AND tsi.ServiceInventoryId = tws.ServiceInventoryId
                        LEFT JOIN cimpl.TransactActionType action_lookup ON action_lookup.TransactActionTypeId = tsi.TransactActionTypeId
                        WHERE tws.WorkorderId = w.WorkorderId ORDER BY tws.TransactId DESC) action_type
                    WHERE w.Number = ?""",
                    params=(number,), context=case.title)
                scripts.assert_field_values(row=row, expected={"workorderNumber": number}, context=case.title)
                if body.get("service"):
                    assert int(row["providerId"]) == int(body["service"]["providerId"]), f"{case.title}: DB provider does not match request"
                if body.get("requester", {}).get("id") is not None:
                    assert int(row["requesterEmployeeId"]) == int(body["requester"]["id"]), f"{case.title}: DB requester does not match request"
                if "duedate persistence" in title:
                    actual_due_date = row.get("dueDate")
                    assert actual_due_date and actual_due_date.date().isoformat() == body.get("dueDate"), (
                        f"{case.title}: persisted dueDate does not match request. "
                        f"Expected={body.get('dueDate')}, Actual={actual_due_date}"
                    )
                metrics.extend([f"Response status: {response.status}", f"Response payload: {json.dumps(payload, ensure_ascii=True)}",
                                f"Workorder count after: {after}", "DB validation: " + scripts.format_snapshot(row=row, keys=list(row))])
            else:
                assert response.status in {400, 404, 409, 500}, f"{case.title}: expected validation error, got {response.status}: {_response_text(response)}"
                after = scripts.count_rows(table="cimpl.Workorder")
                scripts.assert_count_delta(before=before, after=after, expected_delta=0, context=case.title)
                metrics.extend([f"Response status: {response.status}", f"Response payload: {json.dumps(payload, ensure_ascii=True)}", f"Workorder count after: {after}"])
        finally:
            if sys.exc_info()[0] is not None:
                metrics.extend([f"Response status: {response.status}", f"Response body: {_response_text(response)}"])
            _record_metrics(request, metrics)