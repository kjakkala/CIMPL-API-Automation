"""Data-driven validation for POST /v1/workorders/change-options."""

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
class ChangeOptionsCase:
    case_id: str
    title: str
    label: str


CASES = [
    ChangeOptionsCase("17799226", "Add single compatible option", "Positive"),
    ChangeOptionsCase("17799227", "Add multiple compatible options", "Positive"),
    ChangeOptionsCase("17799228", "Remove single existing additional option", "Positive"),
    ChangeOptionsCase("17799229", "Remove multiple existing additional options", "Positive"),
    ChangeOptionsCase("17799230", "Add and remove single options", "Positive"),
    ChangeOptionsCase("17799231", "Add and remove multiple options", "Positive"),
    ChangeOptionsCase("17799232", "Both option arrays empty", "Validation"),
    ChangeOptionsCase("17799233", "Missing both option arrays", "Validation"),
    ChangeOptionsCase("17799234", "Add existing included option", "Validation"),
    ChangeOptionsCase("17799235", "Add existing additional option", "Validation"),
    ChangeOptionsCase("17799236", "Add incompatible option", "Validation"),
    ChangeOptionsCase("17799237", "Remove and add same option", "Validation"),
    ChangeOptionsCase("17799238", "Missing service", "Validation"),
    ChangeOptionsCase("17799239", "Inactive service", "Validation"),
    ChangeOptionsCase("17799240", "Service with open workorder", "Validation"),
    ChangeOptionsCase("17799241", "Missing requester", "Validation"),
    ChangeOptionsCase("17799242", "Requester ID only", "Positive"),
    ChangeOptionsCase("17799243", "Invalid requester identifiers", "Validation"),
    ChangeOptionsCase("17799244", "Invalid cultureCode", "Positive"),
    ChangeOptionsCase("17799245", "Unauthorized change options request", "Authorization"),
    ChangeOptionsCase("17799246", "Requester email only", "Positive"),
    ChangeOptionsCase("17799247", "Requester number only", "Positive"),
    ChangeOptionsCase("17799248", "Requester email does not match requester ID", "Validation"),
    ChangeOptionsCase("17799249", "Requester number does not match requester ID", "Validation"),
    ChangeOptionsCase("17799250", "Inactive requester", "Validation"),
    ChangeOptionsCase("17799251", "Requester is null", "Validation"),
    ChangeOptionsCase("17799252", "Empty requester object", "Validation"),
    ChangeOptionsCase("17799253", "Service ID only", "Positive"),
    ChangeOptionsCase("17799254", "Service ID and providerId only", "Positive"),
    ChangeOptionsCase("17799255", "Missing service providerId", "Validation"),
    ChangeOptionsCase("17799256", "Missing service accountNumber", "Validation"),
    ChangeOptionsCase("17799257", "Missing service serviceNumber", "Validation"),
    ChangeOptionsCase("17799258", "Service primary number only", "Validation"),
    ChangeOptionsCase("17799259", "Null option arrays", "Validation"),
    ChangeOptionsCase("17799260", "Duplicate option in optionsToAdd", "Validation"),
    ChangeOptionsCase("17799261", "Duplicate option in optionsToRemove", "Validation"),
    ChangeOptionsCase("17799262", "Invalid option in optionsToRemove", "Validation"),
    ChangeOptionsCase("17799263", "Missing cultureCode", "Positive"),
    ChangeOptionsCase("17799264", "Blank cultureCode", "Positive"),
    ChangeOptionsCase("17799265", "Null cultureCode", "Positive"),
    ChangeOptionsCase("17799266", "Missing dueDate", "Positive"),
    ChangeOptionsCase("17799267", "Blank dueDate", "Positive"),
    ChangeOptionsCase("17799268", "Past dueDate", "Validation"),
    ChangeOptionsCase("17799269", "Current-date dueDate", "Positive"),
    ChangeOptionsCase("17799270", "Missing comments", "Positive"),
    ChangeOptionsCase("17799271", "Blank comments", "Positive"),
    ChangeOptionsCase("17838375", "Requester null", "Validation"), ChangeOptionsCase("17838376", "Requester empty object", "Validation"), ChangeOptionsCase("17838377", "Inactive requester", "Validation"),
    ChangeOptionsCase("17838378", "Service null", "Validation"), ChangeOptionsCase("17838379", "Service empty object", "Validation"), ChangeOptionsCase("17838380", "Service invalid account number", "Validation"), ChangeOptionsCase("17838381", "Null option element", "Validation"),
    ChangeOptionsCase("17838419", "Service with valid provider from another service", "Validation"), ChangeOptionsCase("17838420", "Service with valid account from another provider", "Validation"), ChangeOptionsCase("17838421", "Service with valid service number from another service", "Validation"),
]

CASE_PARAMS = [
    pytest.param(case, id=f"C{case.case_id}", marks=[
        pytest.mark.swagger_section("Workorders"), pytest.mark.swagger_operation("POST /v1/workorders/change-options"),
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


def _text(response) -> str:
    value = response.text
    return str(value() if callable(value) else value)


def _status(response) -> int:
    return int(response.status if hasattr(response, "status") else response.status_code)


def _record(request, metrics: list[str]):
    request.node.user_properties.append(("dashboard_metrics", "\n".join(metrics)))


def _employee(db, exclude_id: int | None = None, *, active: bool = True) -> dict:
    exclusion = "AND EmployeeId <> ?" if exclude_id is not None else ""
    status_sql = "= 1" if active else "<> 1"
    row = db.fetch_one("""SELECT TOP 1 EmployeeId AS id, Number AS number, EMail AS email FROM cimpl.Employee
        WHERE EmployeeStatusId """ + status_sql + " AND EMail IS NOT NULL " + exclusion + " ORDER BY EmployeeId DESC", (exclude_id,) if exclude_id else ())
    assert row, "No active requester employee available"
    return {"id": int(row["id"]), "number": str(row.get("number") or ""), "email": str(row["email"])}


def _service(db, *, mode: str = "addable", provider_id: int | None = None, service_type_id: int | None = None) -> dict:
    common = "si.ServiceInventoryStatusId = 1 AND si.ProviderId IS NOT NULL AND si.PrimaryNumber IS NOT NULL AND a.Number IS NOT NULL"
    if mode == "inactive":
        common = common.replace("= 1", "<> 1", 1)
    transaction_clause = "" if mode == "open" else "AND NOT EXISTS (SELECT 1 FROM cimpl.TransactServiceInventory tsi WHERE tsi.TransactId = (SELECT TOP 1 latest.TransactId FROM cimpl.TransactServiceInventory latest WHERE latest.ServiceInventoryId = si.ServiceInventoryId ORDER BY latest.TransactId DESC)) AND NOT EXISTS (SELECT 1 FROM cimpl.TransactWorkorderService tws INNER JOIN cimpl.WorkorderSummary ws ON ws.WorkorderId=tws.WorkorderId INNER JOIN cimpl.WorkorderStatus wos ON wos.WorkorderStatusId=ws.WorkorderStatusId AND wos.TenantId=ws.TenantId WHERE tws.ServiceInventoryId=si.ServiceInventoryId AND tws.WorkorderId=(SELECT TOP 1 latest.WorkorderId FROM cimpl.TransactWorkorderService latest WHERE latest.ServiceInventoryId=si.ServiceInventoryId ORDER BY latest.WorkorderId DESC) AND LOWER(wos.Name) NOT IN ('completed','cancelled','canceled'))"
    extra = ""
    if mode == "addable":
        extra = "AND EXISTS (SELECT 1 FROM cimpl.OptionTypeProviderServiceTypeCompatibility c INNER JOIN cimpl.OptionType o ON o.OptionTypeId=c.OptionTypeId AND o.Active=1 AND ISNULL(o.OptionCategoryId, 0) <> 2 WHERE c.ProviderId=si.ProviderId AND c.ServiceTypeId=si.ServiceTypeId AND NOT EXISTS (SELECT 1 FROM cimpl.ServiceInventoryOption x WHERE x.ServiceInventoryId=si.ServiceInventoryId AND x.OptionTypeId=c.OptionTypeId))"
    elif mode == "additional":
        extra = "AND EXISTS (SELECT 1 FROM cimpl.ServiceInventoryOption sio WHERE sio.ServiceInventoryId=si.ServiceInventoryId AND sio.IsAuto=0 AND sio.Active=1)"
    elif mode == "included":
        extra = "AND EXISTS (SELECT 1 FROM cimpl.ServiceInventoryOption sio WHERE sio.ServiceInventoryId=si.ServiceInventoryId AND sio.IsAuto=1 AND sio.Active=1)"
    provider_filter = " AND si.ProviderId=?" if provider_id is not None else ""
    service_type_filter = " AND si.ServiceTypeId=?" if service_type_id is not None else ""
    seed_params = tuple(value for value in (provider_id, service_type_id) if value is not None)
    if mode == "open":
        query = """SELECT TOP 1 si.ServiceInventoryId AS id, a.Number AS accountNumber, si.ProviderId AS providerId, si.ServiceTypeId AS serviceTypeId, si.PrimaryNumber AS serviceNumber
            FROM cimpl.TransactWorkorderService tws INNER JOIN cimpl.WorkorderSummary ws ON ws.WorkorderId=tws.WorkorderId
            INNER JOIN cimpl.OpenWorkorderStatusView os ON os.WorkorderStatusId=ws.WorkorderStatusId
            INNER JOIN cimpl.ServiceInventory si ON si.ServiceInventoryId=tws.ServiceInventoryId INNER JOIN cimpl.Account a ON a.AccountId=si.AccountId
            WHERE si.ProviderId IS NOT NULL AND si.PrimaryNumber IS NOT NULL AND a.Number IS NOT NULL""" + provider_filter + service_type_filter + " ORDER BY tws.WorkorderId DESC"
    else:
        query = """SELECT TOP 1 si.ServiceInventoryId AS id, a.Number AS accountNumber, si.ProviderId AS providerId, si.ServiceTypeId AS serviceTypeId, si.PrimaryNumber AS serviceNumber
            FROM cimpl.ServiceInventory si INNER JOIN cimpl.Account a ON a.AccountId=si.AccountId WHERE """ + common + " " + transaction_clause + " " + extra + provider_filter + service_type_filter + " ORDER BY si.ServiceInventoryId DESC"
    row = db.fetch_one(query, seed_params)
    if not row:
        pytest.skip(f"No {mode} service inventory seed is available")
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
           WHERE si.ServiceInventoryStatusId = 1 AND si.ProviderId IS NOT NULL AND si.PrimaryNumber IS NOT NULL AND a.Number IS NOT NULL
             AND NOT EXISTS (SELECT 1 FROM cimpl.TransactServiceInventory tsi WHERE tsi.TransactId = (SELECT TOP 1 latest.TransactId FROM cimpl.TransactServiceInventory latest WHERE latest.ServiceInventoryId = si.ServiceInventoryId ORDER BY latest.TransactId DESC))
             AND EXISTS (SELECT 1 FROM cimpl.OptionTypeProviderServiceTypeCompatibility c INNER JOIN cimpl.OptionType o ON o.OptionTypeId=c.OptionTypeId AND o.Active=1 AND ISNULL(o.OptionCategoryId, 0) <> 2 WHERE c.ProviderId=si.ProviderId AND c.ServiceTypeId=si.ServiceTypeId AND NOT EXISTS (SELECT 1 FROM cimpl.ServiceInventoryOption x WHERE x.ServiceInventoryId=si.ServiceInventoryId AND x.OptionTypeId=c.OptionTypeId))"""
        + provider_filter + service_type_filter + " ORDER BY si.ProviderId DESC",
        tuple(params),
    )
    return [
        {"providerId": int(row["providerId"]), "providerName": str(row.get("providerName") or ""), "serviceTypeId": int(row["serviceTypeId"]), "serviceTypeName": str(row.get("serviceTypeName") or "")}
        for row in rows
    ]


def _service_body(service: dict) -> dict:
    return {"id": int(service["id"]), "accountNumber": str(service["accountNumber"]), "providerId": int(service["providerId"]), "serviceNumber": str(service["serviceNumber"])}


def _addable_options(db, service: dict, count: int) -> list[int]:
    rows = db.fetch_all("""SELECT TOP (?) c.OptionTypeId AS id FROM cimpl.OptionTypeProviderServiceTypeCompatibility c
        INNER JOIN cimpl.OptionType o ON o.OptionTypeId=c.OptionTypeId AND o.Active=1 AND ISNULL(o.OptionCategoryId, 0) <> 2
        WHERE c.ProviderId=? AND c.ServiceTypeId=? AND NOT EXISTS (SELECT 1 FROM cimpl.ServiceInventoryOption sio WHERE sio.ServiceInventoryId=? AND sio.OptionTypeId=c.OptionTypeId)
        ORDER BY c.OptionTypeId DESC""", (count, service["providerId"], service["serviceTypeId"], service["id"]))
    if len(rows) < count:
        pytest.skip(f"Service {service['id']} has fewer than {count} compatible unassigned options")
    return [int(row["id"]) for row in rows]


def _existing_options(db, service: dict, *, is_auto: bool, count: int) -> list[int]:
    rows = db.fetch_all("""SELECT TOP (?) OptionTypeId AS id FROM cimpl.ServiceInventoryOption
        WHERE ServiceInventoryId=? AND IsAuto=? AND Active=1 ORDER BY OptionTypeId DESC""", (count, service["id"], 1 if is_auto else 0))
    if len(rows) < count:
        pytest.skip(f"Service {service['id']} has fewer than {count} existing {'included' if is_auto else 'additional'} options")
    return [int(row["id"]) for row in rows]


def _payload(db, case: ChangeOptionsCase) -> tuple[dict, dict]:
    provider_id = int(os.getenv("TEST_PROVIDER_ID")) if os.getenv("TEST_PROVIDER_ID", "").strip() else None
    service_type_id = int(os.getenv("TEST_SERVICE_TYPE_ID")) if os.getenv("TEST_SERVICE_TYPE_ID", "").strip() else None
    service = _service(db, provider_id=provider_id, service_type_id=service_type_id)
    marker = f"AUTO-OPTIONS-C{case.case_id}-{datetime.now():%Y%m%d%H%M%S%f}"
    body = {"requester": _employee(db), "dueDate": (date.today() + timedelta(days=2)).isoformat(), "comments": marker,
            "service": _service_body(service), "optionsToAdd": _addable_options(db, service, 1), "optionsToRemove": [], "cultureCode": "en-US"}
    return body, {"service": service, "marker": marker}


def _mutate(case: ChangeOptionsCase, body: dict, context: dict, db):
    title = case.title.lower()
    if "valid provider from another service" in title or "valid account from another provider" in title or "valid service number from another service" in title:
        other = db.fetch_one("SELECT TOP 1 si.ProviderId AS providerId, a.Number AS accountNumber, si.PrimaryNumber AS serviceNumber FROM cimpl.ServiceInventory si INNER JOIN cimpl.Account a ON a.AccountId=si.AccountId WHERE si.ServiceInventoryId<>? AND si.ServiceInventoryStatusId=1 AND si.ProviderId IS NOT NULL AND si.PrimaryNumber IS NOT NULL AND a.Number IS NOT NULL ORDER BY si.ServiceInventoryId DESC", (body["service"]["id"],))
        assert other, "No alternate service compatibility seed available"
        if "provider" in title: body["service"]["providerId"] = int(other["providerId"])
        elif "account" in title: body["service"]["accountNumber"] = str(other["accountNumber"])
        else: body["service"]["serviceNumber"] = str(other["serviceNumber"])
    elif "service null" in title: body["service"] = None
    elif "service empty" in title: body["service"] = {}
    elif "service invalid account" in title: body["service"]["accountNumber"] = "INVALID-ACCOUNT"
    elif "null option" in title: body["optionsToAdd"] = [*body["optionsToAdd"], None]
    elif "multiple compatible" in title:
        body["optionsToAdd"] = _addable_options(db, context["service"], 2)
    elif "existing included" in title:
        service = _service(db, mode="included"); body["service"] = _service_body(service); body["optionsToAdd"] = _existing_options(db, service, is_auto=True, count=1)
    elif "existing additional" in title or "remove and add same" in title:
        service = _service(db, mode="additional"); option = _existing_options(db, service, is_auto=False, count=1)[0]; body["service"] = _service_body(service); body["optionsToAdd"] = [option]
        if "remove and add same" in title: body["optionsToRemove"] = [option]
    elif "remove single" in title or "remove multiple" in title or "add and remove" in title:
        service = _service(db, mode="additional"); count = 2 if "multiple" in title else 1; removable = _existing_options(db, service, is_auto=False, count=count); body["service"] = _service_body(service); body["optionsToRemove"] = removable
        if "add and remove" in title: body["optionsToAdd"] = _addable_options(db, service, count)
        else: body["optionsToAdd"] = []
    elif "both option arrays empty" in title: body["optionsToAdd"] = []; body["optionsToRemove"] = []
    elif "missing both" in title: body.pop("optionsToAdd"); body.pop("optionsToRemove")
    elif "incompatible option" in title: body["optionsToAdd"] = [999999999]
    elif "missing service" in title: body.pop("service")
    elif "inactive service" in title: body["service"] = _service_body(_service(db, mode="inactive"))
    elif "open workorder" in title: body["service"] = _service_body(_service(db, mode="open"))
    elif "missing requester" in title: body.pop("requester")
    elif "requester id only" in title: body["requester"] = {"id": body["requester"]["id"]}
    elif "invalid requester" in title:
        other = _employee(db, exclude_id=body["requester"]["id"]); body["requester"]["email"] = other["email"]
    elif "invalid culture" in title: body["cultureCode"] = "invalid-culture"
    elif "requester email only" in title: body["requester"] = {"email": body["requester"]["email"]}
    elif "requester number only" in title: body["requester"] = {"number": body["requester"]["number"]}
    elif "email does not match" in title:
        other = _employee(db, exclude_id=body["requester"]["id"]); body["requester"]["email"] = other["email"]
    elif "number does not match" in title:
        other = _employee(db, exclude_id=body["requester"]["id"]); body["requester"]["number"] = other["number"]
    elif "inactive requester" in title: body["requester"] = _employee(db, active=False)
    elif "requester is null" in title: body["requester"] = None
    elif "empty requester" in title: body["requester"] = {}
    elif "service id only" in title: body["service"] = {"id": body["service"]["id"]}
    elif "id and providerid only" in title: body["service"] = {"id": body["service"]["id"], "providerId": body["service"]["providerId"]}
    elif "missing service providerid" in title: body["service"].pop("providerId")
    elif "missing service accountnumber" in title: body["service"].pop("accountNumber")
    elif "missing service servicenumber" in title: body["service"].pop("serviceNumber")
    elif "primary number only" in title: body["service"] = {"serviceNumber": body["service"]["serviceNumber"]}
    elif "null option arrays" in title: body["optionsToAdd"] = None; body["optionsToRemove"] = None
    elif "duplicate option in optionstoadd" in title: body["optionsToAdd"] = body["optionsToAdd"] * 2
    elif "duplicate option in optionstoremove" in title:
        service = _service(db, mode="additional"); option = _existing_options(db, service, is_auto=False, count=1)[0]; body["service"] = _service_body(service); body["optionsToAdd"] = []; body["optionsToRemove"] = [option, option]
    elif "invalid option in optionstoremove" in title: body["optionsToAdd"] = []; body["optionsToRemove"] = [999999999]
    elif "missing culturecode" in title: body.pop("cultureCode")
    elif "blank culturecode" in title: body["cultureCode"] = ""
    elif "null culturecode" in title: body["cultureCode"] = None
    elif "missing duedate" in title: body.pop("dueDate")
    elif "blank duedate" in title: body["dueDate"] = ""
    elif "past duedate" in title: body["dueDate"] = (date.today() - timedelta(days=1)).isoformat()
    elif "current-date duedate" in title: body["dueDate"] = date.today().isoformat()
    elif "missing comments" in title: body.pop("comments")
    elif "blank comments" in title: body["comments"] = ""


class TestPostWorkordersChangeOptions:
    @pytest.mark.parametrize("case", CASE_PARAMS)
    def test_post_workorders_change_options(self, case, api_context, db, request):
        body, context = _payload(db, case)
        _mutate(case, body, context, db)
        scripts = PostApiDbScripts(db); marker = str(body.get("comments") or "")
        marker_before = scripts.count_rows(table="cimpl.WorkorderSummary", where_sql="Comment LIKE ?", params=(f"%{marker}%",))
        metrics = [f"CaseId: {case.case_id}", "Service: " + json.dumps(context["service"], default=str), f"Request marker rows before: {marker_before}", f"Request body: {json.dumps(body, ensure_ascii=True)}"]
        response = requests.post(f"{os.environ['BASE_URL'].rstrip('/')}/workorders/change-options", json=body, headers={"accept": "application/json"}, timeout=int(os.getenv("API_REQUEST_TIMEOUT_MS", "120000")) / 1000) if case.label == "Authorization" else api_context.post("workorders/change-options", data=json.dumps(body), headers={"Content-Type": "application/json", "accept": "application/json"})
        try:
            payload = _json(response)
            if case.label == "Authorization":
                assert _status(response) in {401, 403}, f"{case.title}: expected 401/403, got {_status(response)}: {_text(response)}"
            elif case.label == "Positive":
                assert _status(response) in {200, 201}, f"{case.title}: expected success, got {_status(response)}: {_text(response)}"
                number = str(payload.get("workorderNumber") or "").strip(); assert number, f"{case.title}: missing workorderNumber"
                rows = db.fetch_all("""SELECT tws.TransactId AS transactId, tsio.ServiceInventoryId AS serviceId, tsio.OptionTypeId AS optionId, tsio.IsAuto AS isAuto
                    FROM cimpl.Workorder w INNER JOIN cimpl.TransactWorkorderService tws ON tws.WorkorderId=w.WorkorderId
                    INNER JOIN cimpl.TransactServiceInventoryOption tsio ON tsio.TransactId=tws.TransactId AND tsio.ServiceInventoryId=tws.ServiceInventoryId
                    WHERE w.Number=?""", (number,))
                assert rows, f"{case.title}: no pending option rows for workorder {number}"
                assert all(int(row["serviceId"]) == int(body["service"]["id"]) for row in rows), f"{case.title}: pending option service mismatch"
                actual_options = {int(row["optionId"]) for row in rows}
                assert set(body.get("optionsToAdd", [])).issubset(actual_options), f"{case.title}: pending options omit additions. Expected={body.get('optionsToAdd')}, Actual={actual_options}"
                metrics.extend([f"Response status: {_status(response)}", f"Response payload: {json.dumps(payload)}", f"DB pending options: {json.dumps(rows, default=str)}"])
            else:
                assert _status(response) in {400, 404, 406, 409, 500}, f"{case.title}: expected validation error, got {_status(response)}: {_text(response)}"
                marker_after = scripts.count_rows(table="cimpl.WorkorderSummary", where_sql="Comment LIKE ?", params=(f"%{marker}%",))
                scripts.assert_count_delta(before=marker_before, after=marker_after, expected_delta=0, context=case.title)
                metrics.extend([f"Response status: {_status(response)}", f"Response payload: {json.dumps(payload)}", f"Request marker rows after: {marker_after}"])
        finally:
            if sys.exc_info()[0] is not None: metrics.extend([f"Response status: {_status(response)}", f"Response body: {_text(response)}"])
            _record(request, metrics)