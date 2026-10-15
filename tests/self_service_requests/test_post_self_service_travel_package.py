"""REQ05 coverage for POST /requestApi/SelfServiceRequests/TravelPackage (OperationTypeId=118)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID

import pytest
import requests

from utils.post_api_db_scripts import PostApiDbScripts


SELF_SERVICE_URL = os.getenv("SELF_SERVICE_BASE_URL", "https://cplca1webq01.uplandsoftware.net/requestApi").rstrip("/") + "/SelfServiceRequests/TravelPack"
OPERATION_TYPE_ID = 118


@dataclass(frozen=True)
class TravelPackageCase:
    case_id: str
    title: str
    label: str


_CASE_DATA = [
    ("17839451", "Create Travel Package request with compatible data", "Positive"),
    ("17839452", "Travel Package request with required fields only", "Positive"),
    ("17839453", "Multiple destination countries", "Positive"),
    ("17839454", "Missing destinationCountries", "Validation"),
    ("17839455", "Null destinationCountries", "Validation"),
    ("17839456", "Empty destinationCountries array", "Validation"),
    ("17839457", "Invalid destination country code", "Validation"),
    ("17839458", "Departure date today", "Positive"),
    ("17839459", "Departure date past", "Validation"),
    ("17839460", "Departure date invalid format", "Validation"),
    ("17839461", "Missing departureDate", "Validation"),
    ("17839462", "Null departureDate", "Validation"),
    ("17839463", "Return date equal to departure date", "Positive"),
    ("17839464", "Return date before departure date", "Validation"),
    ("17839465", "Return date invalid format", "Validation"),
    ("17839466", "Missing returnDate", "Validation"),
    ("17839467", "Null returnDate", "Validation"),
    ("17839468", "Multiple travelPackageIds", "Positive"),
    ("17839469", "Missing travelPackageIds", "Validation"),
    ("17839470", "Null travelPackageIds", "Validation"),
    ("17839471", "Empty travelPackageIds array", "Validation"),
    ("17839472", "Invalid travelPackageIds", "Validation"),
    ("17839473", "Duplicate travelPackageIds", "Validation"),
    ("17839474", "Travel package incompatible with provider or service type", "Validation"),
    ("17839475", "Travel package disabled for self-provisioning", "Validation"),
    ("17839476", "Service ID only", "Positive"),
    ("17839477", "Service account number, provider ID, service number only", "Positive"),
    ("17839478", "Missing service", "Validation"),
    ("17839479", "Null service", "Validation"),
    ("17839480", "Invalid service ID", "Validation"),
    ("17839481", "Service account number mismatch", "Validation"),
    ("17839482", "Service provider ID mismatch", "Validation"),
    ("17839483", "Service number mismatch", "Validation"),
    ("17839484", "Inactive service", "Validation"),
    ("17839485", "Service with pending transaction or open workorder", "Validation"),
    ("17839486", "Service with pending self service approval", "Validation"),
    ("17839487", "Requester ID only", "Positive"),
    ("17839488", "Requester number only", "Positive"),
    ("17839489", "Requester email only", "Positive"),
    ("17839490", "Inactive requester", "Validation"),
    ("17839491", "Missing requester", "Validation"),
    ("17839492", "Null requester", "Validation"),
    ("17839493", "Invalid requester", "Validation"),
    ("17839494", "Requester identifiers mismatch", "Validation"),
    ("17839495", "Due date omitted", "Positive"),
    ("17839496", "Due date null", "Positive"),
    ("17839497", "Due date blank", "Positive"),
    ("17839498", "Due date past", "Validation"),
    ("17839499", "Due date invalid calendar date", "Validation"),
    ("17839500", "Comments omitted", "Positive"),
    ("17839501", "Comments null", "Positive"),
    ("17839502", "Comments blank", "Positive"),
    ("17839503", "Comments SQL injection input", "Positive"),
    ("17839504", "Comments XSS input", "Positive"),
    ("17839505", "Unauthorized request", "Authorization"),
]

CASES = [TravelPackageCase(case_id, title, label) for case_id, title, label in _CASE_DATA]
CASE_PARAMS = [
    pytest.param(
        case,
        id=f"C{case.case_id}",
        marks=[
            pytest.mark.swagger_section("Self Service Requests"),
            pytest.mark.swagger_operation("POST /requestApi/SelfServiceRequests/TravelPack"),
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


def _seed(db, *, provider_id: int | None = None, service_type_id: int | None = None) -> dict:
    provider_filter = " AND comp.ProviderId=?" if provider_id is not None else ""
    service_type_filter = " AND comp.ServiceTypeId=?" if service_type_id is not None else ""
    seed_params = tuple(value for value in (provider_id, service_type_id) if value is not None)
    option_row = db.fetch_one(
        """SELECT TOP 1 ot.OptionTypeId AS option_type_id, comp.ProviderId AS provider_id, comp.ServiceTypeId AS service_type_id
        FROM cimpl.OptionType ot
        INNER JOIN cimpl.OptionTypeProviderServiceTypeCompatibility comp ON comp.OptionTypeId=ot.OptionTypeId AND comp.SelfProvisioningEnabled=1
        WHERE ot.OptionCategoryId=2 AND ot.Active=1"""
        + provider_filter + service_type_filter +
        """
          AND EXISTS (
              SELECT 1 FROM cimpl.ServiceInventory si INNER JOIN cimpl.Account a ON a.AccountId=si.AccountId
              WHERE si.ServiceInventoryStatusId=1 AND si.ProviderId=comp.ProviderId AND si.ServiceTypeId=comp.ServiceTypeId AND si.PrimaryNumber IS NOT NULL
                AND NOT EXISTS (SELECT 1 FROM cimpl.TransactServiceInventory tsi WHERE tsi.TransactId=(SELECT TOP 1 tsi_latest.TransactId FROM cimpl.TransactServiceInventory tsi_latest WHERE tsi_latest.ServiceInventoryId=si.ServiceInventoryId ORDER BY tsi_latest.TransactId DESC))
                AND NOT EXISTS (SELECT 1 FROM cimpl.TransactWorkorderService tws INNER JOIN cimpl.WorkorderSummary ws ON ws.WorkorderId=tws.WorkorderId INNER JOIN cimpl.WorkorderStatus wos ON wos.WorkorderStatusId=ws.WorkorderStatusId AND wos.TenantId=ws.TenantId WHERE tws.ServiceInventoryId=si.ServiceInventoryId AND tws.WorkorderId=(SELECT TOP 1 tws_latest.WorkorderId FROM cimpl.TransactWorkorderService tws_latest WHERE tws_latest.ServiceInventoryId=si.ServiceInventoryId ORDER BY tws_latest.WorkorderId DESC) AND LOWER(wos.Name) NOT IN ('completed','cancelled','canceled'))
                AND NOT EXISTS (SELECT 1 FROM cimpl.SelfServiceActionLog ssl WHERE ssl.ServiceInventoryId=si.ServiceInventoryId AND ssl.StatusId=1 AND ssl.WorkorderId IS NULL AND ssl.UpdatedDate IS NULL)
          )
          AND EXISTS (
              SELECT 1 FROM cimpl.OptionTypeRoamingZoneRelation rel
              INNER JOIN cimpl.RoamingZone rz ON rz.RoamingZoneId=rel.RoamingZoneId AND rz.Active=1
              INNER JOIN cimpl.RoamingZoneCountryRelation rzc ON rzc.RoamingZoneId=rel.RoamingZoneId
              WHERE rel.OptionTypeid=ot.OptionTypeId AND rel.ProviderId=comp.ProviderId
          )
        ORDER BY comp.ProviderId DESC""",
        seed_params,
    )
    assert option_row, "No self-service-enabled travel package with an available service and active roaming zone/country mapping for the selected filters"
    provider_id = int(option_row["provider_id"])
    service_type_id = int(option_row["service_type_id"])
    travel_package_id = int(option_row["option_type_id"])

    service_row = db.fetch_one(
        """SELECT TOP 1 si.ServiceInventoryId AS service_id, a.Number AS account_number, si.PrimaryNumber AS service_number
        FROM cimpl.ServiceInventory si
        INNER JOIN cimpl.Account a ON a.AccountId=si.AccountId
        WHERE si.ServiceInventoryStatusId=1 AND si.ProviderId=? AND si.ServiceTypeId=? AND si.PrimaryNumber IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM cimpl.TransactServiceInventory tsi WHERE tsi.TransactId=(SELECT TOP 1 tsi_latest.TransactId FROM cimpl.TransactServiceInventory tsi_latest WHERE tsi_latest.ServiceInventoryId=si.ServiceInventoryId ORDER BY tsi_latest.TransactId DESC))
          AND NOT EXISTS (SELECT 1 FROM cimpl.TransactWorkorderService tws INNER JOIN cimpl.WorkorderSummary ws ON ws.WorkorderId=tws.WorkorderId INNER JOIN cimpl.WorkorderStatus wos ON wos.WorkorderStatusId=ws.WorkorderStatusId AND wos.TenantId=ws.TenantId WHERE tws.ServiceInventoryId=si.ServiceInventoryId AND tws.WorkorderId=(SELECT TOP 1 tws_latest.WorkorderId FROM cimpl.TransactWorkorderService tws_latest WHERE tws_latest.ServiceInventoryId=si.ServiceInventoryId ORDER BY tws_latest.WorkorderId DESC) AND LOWER(wos.Name) NOT IN ('completed','cancelled','canceled'))
          AND NOT EXISTS (SELECT 1 FROM cimpl.SelfServiceActionLog ssl WHERE ssl.ServiceInventoryId=si.ServiceInventoryId AND ssl.StatusId=1 AND ssl.WorkorderId IS NULL AND ssl.UpdatedDate IS NULL)
        ORDER BY si.ServiceInventoryId DESC""",
        (provider_id, service_type_id),
    )
    assert service_row, "No compatible active service available for the seed provider/service type"

    country_codes = _compatible_country_codes(db, travel_package_id, provider_id, 2)

    return {
        "providerId": provider_id,
        "serviceTypeId": service_type_id,
        "travelPackageId": travel_package_id,
        "serviceId": int(service_row["service_id"]),
        "accountNumber": str(service_row["account_number"]),
        "serviceNumber": str(service_row["service_number"]),
        "countryCodes": country_codes,
    }


def _compatible_country_codes(db, travel_package_id: int, provider_id: int, count: int) -> list[str]:
    rows = db.fetch_all(
        """SELECT DISTINCT TOP (?) c.Iso2 AS country_code
        FROM cimpl.OptionTypeRoamingZoneRelation rel
        INNER JOIN cimpl.RoamingZone rz ON rz.RoamingZoneId=rel.RoamingZoneId AND rz.Active=1
        INNER JOIN cimpl.RoamingZoneCountryRelation rzc ON rzc.RoamingZoneId=rel.RoamingZoneId
        INNER JOIN cimpl.Country c ON c.CountryId=rzc.CountryId AND c.Active=1 AND c.Iso2 IS NOT NULL
        WHERE rel.OptionTypeid=? AND rel.ProviderId=?
        ORDER BY c.Iso2""",
        (count, travel_package_id, provider_id),
    )
    codes = [str(row["country_code"]) for row in rows]
    assert codes, f"No active roaming zone/country mapping for travel package {travel_package_id}/provider {provider_id}"
    return codes


def _service_body(seed: dict) -> dict:
    return {
        "id": int(seed["serviceId"]),
        "accountNumber": str(seed["accountNumber"]),
        "providerId": int(seed["providerId"]),
        "serviceNumber": str(seed["serviceNumber"]),
    }


def preview_seed_compatibility(db, *, provider_id: int | None = None, service_type_id: int | None = None, limit: int = 2) -> list[dict]:
    """Travel package requires a self-service-enabled compatibility row, an available service, and an active roaming zone/country mapping."""
    provider_filter = " AND comp.ProviderId=?" if provider_id is not None else ""
    service_type_filter = " AND comp.ServiceTypeId=?" if service_type_id is not None else ""
    params = [limit] + [value for value in (provider_id, service_type_id) if value is not None]
    rows = db.fetch_all(
        """SELECT DISTINCT TOP (?) comp.ProviderId AS providerId, p.Name AS providerName, comp.ServiceTypeId AS serviceTypeId, st.Name AS serviceTypeName
        FROM cimpl.OptionType ot
        INNER JOIN cimpl.OptionTypeProviderServiceTypeCompatibility comp ON comp.OptionTypeId=ot.OptionTypeId AND comp.SelfProvisioningEnabled=1
        INNER JOIN cimpl.Provider p ON p.ProviderId=comp.ProviderId
        INNER JOIN cimpl.ServiceType st ON st.ServiceTypeId=comp.ServiceTypeId
        WHERE ot.OptionCategoryId=2 AND ot.Active=1"""
        + provider_filter + service_type_filter +
        """
          AND EXISTS (
              SELECT 1 FROM cimpl.ServiceInventory si INNER JOIN cimpl.Account a ON a.AccountId=si.AccountId
              WHERE si.ServiceInventoryStatusId=1 AND si.ProviderId=comp.ProviderId AND si.ServiceTypeId=comp.ServiceTypeId AND si.PrimaryNumber IS NOT NULL
                AND NOT EXISTS (SELECT 1 FROM cimpl.TransactServiceInventory tsi WHERE tsi.TransactId=(SELECT TOP 1 tsi_latest.TransactId FROM cimpl.TransactServiceInventory tsi_latest WHERE tsi_latest.ServiceInventoryId=si.ServiceInventoryId ORDER BY tsi_latest.TransactId DESC))
                AND NOT EXISTS (SELECT 1 FROM cimpl.SelfServiceActionLog ssl WHERE ssl.ServiceInventoryId=si.ServiceInventoryId AND ssl.StatusId=1 AND ssl.WorkorderId IS NULL AND ssl.UpdatedDate IS NULL)
          )
          AND EXISTS (
              SELECT 1 FROM cimpl.OptionTypeRoamingZoneRelation rel
              INNER JOIN cimpl.RoamingZone rz ON rz.RoamingZoneId=rel.RoamingZoneId AND rz.Active=1
              INNER JOIN cimpl.RoamingZoneCountryRelation rzc ON rzc.RoamingZoneId=rel.RoamingZoneId
              WHERE rel.OptionTypeid=ot.OptionTypeId AND rel.ProviderId=comp.ProviderId
          )
        ORDER BY comp.ProviderId DESC""",
        tuple(params),
    )
    return [
        {"providerId": int(row["providerId"]), "providerName": str(row.get("providerName") or ""), "serviceTypeId": int(row["serviceTypeId"]), "serviceTypeName": str(row.get("serviceTypeName") or "")}
        for row in rows
    ]


def _compatible_travel_package_ids(db, provider_id: int, service_type_id: int, country_code: str, count: int) -> list[int]:
    rows = db.fetch_all(
        """SELECT DISTINCT TOP (?) rel.OptionTypeid AS option_type_id
        FROM cimpl.OptionTypeRoamingZoneRelation rel
        INNER JOIN cimpl.RoamingZone rz ON rz.RoamingZoneId=rel.RoamingZoneId AND rz.Active=1
        INNER JOIN cimpl.RoamingZoneCountryRelation rzc ON rzc.RoamingZoneId=rel.RoamingZoneId
        INNER JOIN cimpl.Country c ON c.CountryId=rzc.CountryId AND c.Iso2=?
        INNER JOIN cimpl.OptionTypeProviderServiceTypeCompatibility comp ON comp.OptionTypeId=rel.OptionTypeid AND comp.ProviderId=rel.ProviderId AND comp.ServiceTypeId=? AND comp.SelfProvisioningEnabled=1
        INNER JOIN cimpl.OptionType ot ON ot.OptionTypeId=rel.OptionTypeid AND ot.OptionCategoryId=2 AND ot.Active=1
        WHERE rel.ProviderId=?
        ORDER BY rel.OptionTypeid DESC""",
        (count, country_code, service_type_id, provider_id),
    )
    return [int(row["option_type_id"]) for row in rows]


def _incompatible_travel_package_id(db, provider_id: int, service_type_id: int) -> int:
    value = db.fetch_scalar(
        """SELECT TOP 1 ot.OptionTypeId FROM cimpl.OptionType ot
        WHERE ot.OptionCategoryId=2 AND ot.Active=1
          AND ot.OptionTypeId NOT IN (SELECT OptionTypeId FROM cimpl.OptionTypeProviderServiceTypeCompatibility WHERE ProviderId=? AND ServiceTypeId=?)
        ORDER BY ot.OptionTypeId DESC""",
        (provider_id, service_type_id),
    )
    assert value, "No provider/service-type-incompatible travel package available"
    return int(value)


def _self_provisioning_disabled_travel_package_id(db) -> int | None:
    value = db.fetch_scalar(
        """SELECT TOP 1 OptionTypeId FROM cimpl.OptionTypeProviderServiceTypeCompatibility
        WHERE SelfProvisioningEnabled=0
        ORDER BY OptionTypeId DESC"""
    )
    return int(value) if value else None


def _inactive_service_id(db) -> int:
    value = db.fetch_scalar("SELECT TOP 1 ServiceInventoryId FROM cimpl.ServiceInventory WHERE ServiceInventoryStatusId<>1 ORDER BY ServiceInventoryId DESC")
    assert value, "No inactive service available"
    return int(value)


def _in_transaction_service_body(db) -> dict:
    row = db.fetch_one(
        """SELECT TOP 1 si.ServiceInventoryId AS service_id, a.Number AS account_number, si.PrimaryNumber AS service_number, si.ProviderId AS provider_id
        FROM cimpl.ServiceInventory si
        INNER JOIN cimpl.Account a ON a.AccountId=si.AccountId
        INNER JOIN cimpl.TransactServiceInventory tsi ON tsi.ServiceInventoryId=si.ServiceInventoryId
        WHERE si.ServiceInventoryStatusId=1
        ORDER BY si.ServiceInventoryId DESC"""
    )
    assert row, "No service with a pending transaction/open workorder available"
    return {"id": int(row["service_id"]), "accountNumber": str(row["account_number"]), "providerId": int(row["provider_id"]), "serviceNumber": str(row["service_number"])}


def _pending_approval_service_body(db) -> dict:
    row = db.fetch_one(
        """SELECT TOP 1 si.ServiceInventoryId AS service_id, a.Number AS account_number, si.PrimaryNumber AS service_number, si.ProviderId AS provider_id
        FROM cimpl.ServiceInventory si
        INNER JOIN cimpl.Account a ON a.AccountId=si.AccountId
        INNER JOIN cimpl.SelfServiceActionLog ssl ON ssl.ServiceInventoryId=si.ServiceInventoryId AND ssl.StatusId=1 AND ssl.WorkorderId IS NULL AND ssl.UpdatedDate IS NULL
        WHERE si.ServiceInventoryStatusId=1
        ORDER BY si.ServiceInventoryId DESC"""
    )
    assert row, "No service with a pending Self Service approval available"
    return {"id": int(row["service_id"]), "accountNumber": str(row["account_number"]), "providerId": int(row["provider_id"]), "serviceNumber": str(row["service_number"])}


def _payload(db, case: TravelPackageCase) -> tuple[dict, dict]:
    provider_id = int(os.getenv("TEST_PROVIDER_ID")) if os.getenv("TEST_PROVIDER_ID", "").strip() else None
    service_type_id = int(os.getenv("TEST_SERVICE_TYPE_ID")) if os.getenv("TEST_SERVICE_TYPE_ID", "").strip() else None
    seed = _seed(db, provider_id=provider_id, service_type_id=service_type_id)
    marker = f"AUTO-SELF-SERVICE-REQ05-C{case.case_id}"
    body = {
        "destinationCountries": [seed["countryCodes"][0]],
        "departureDate": (date.today() + timedelta(days=5)).isoformat(),
        "returnDate": (date.today() + timedelta(days=15)).isoformat(),
        "travelPackageIds": [seed["travelPackageId"]],
        "ignoreWarning": False,
        "service": _service_body(seed),
        "requester": _employee(db),
        "dueDate": (date.today() + timedelta(days=2)).isoformat(),
        "comments": marker,
    }
    return body, {"seed": seed, "marker": marker}


def _mutate(case: TravelPackageCase, body: dict, context: dict, db):
    title = case.title.lower()
    seed = context["seed"]

    if "required fields only" in title:
        body.pop("dueDate", None)
        body.pop("comments", None)
    elif "multiple destination countries" in title:
        codes = seed["countryCodes"]
        body["destinationCountries"] = codes if len(codes) > 1 else [codes[0], codes[0]]
    elif "missing destinationcountries" in title:
        body.pop("destinationCountries", None)
    elif "null destinationcountries" in title:
        body["destinationCountries"] = None
    elif "empty destinationcountries" in title:
        body["destinationCountries"] = []
    elif "invalid destination country" in title:
        body["destinationCountries"] = ["ZZ"]
    elif "departure date today" in title:
        body["departureDate"] = date.today().isoformat()
    elif "departure date past" in title:
        body["departureDate"] = (date.today() - timedelta(days=2)).isoformat()
    elif "departure date invalid format" in title:
        body["departureDate"] = "05-15-2026"
    elif "missing departuredate" in title:
        body.pop("departureDate", None)
    elif "null departuredate" in title:
        body["departureDate"] = None
    elif "return date equal to departure date" in title:
        body["returnDate"] = body["departureDate"]
    elif "return date before departure date" in title:
        body["returnDate"] = (date.today() + timedelta(days=1)).isoformat()
        body["departureDate"] = (date.today() + timedelta(days=5)).isoformat()
    elif "return date invalid format" in title:
        body["returnDate"] = "05-30-2026"
    elif "missing returndate" in title:
        body.pop("returnDate", None)
    elif "null returndate" in title:
        body["returnDate"] = None
    elif "multiple travelpackageids" in title:
        ids = _compatible_travel_package_ids(db, seed["providerId"], seed["serviceTypeId"], seed["countryCodes"][0], 2)
        if len(ids) < 2:
            pytest.skip("No second travel package compatible with the seed provider/service type and destination country")
        body["travelPackageIds"] = ids
    elif "missing travelpackageids" in title:
        body.pop("travelPackageIds", None)
    elif "null travelpackageids" in title:
        body["travelPackageIds"] = None
    elif "empty travelpackageids" in title:
        body["travelPackageIds"] = []
    elif "invalid travelpackageids" in title:
        body["travelPackageIds"] = [999999999]
    elif "duplicate travelpackageids" in title:
        body["travelPackageIds"] = [seed["travelPackageId"], seed["travelPackageId"]]
    elif "incompatible with provider or service type" in title:
        body["travelPackageIds"] = [_incompatible_travel_package_id(db, seed["providerId"], seed["serviceTypeId"])]
    elif "disabled for self-provisioning" in title:
        disabled_id = _self_provisioning_disabled_travel_package_id(db)
        if disabled_id is None:
            pytest.skip("No self-provisioning-disabled travel package compatibility available in this environment")
        body["travelPackageIds"] = [disabled_id]
    elif "service id only" in title:
        body["service"] = {"id": body["service"]["id"]}
    elif "account number, provider id, service number only" in title:
        service = body["service"]
        body["service"] = {"accountNumber": service["accountNumber"], "providerId": service["providerId"], "serviceNumber": service["serviceNumber"]}
    elif "missing service" in title:
        body.pop("service", None)
    elif "null service" in title:
        body["service"] = None
    elif "invalid service id" in title:
        body["service"]["id"] = 999999999
    elif "service account number mismatch" in title:
        body["service"]["accountNumber"] = "INVALID-ACCOUNT"
    elif "service provider id mismatch" in title:
        body["service"]["providerId"] = 999999999
    elif "service number mismatch" in title:
        body["service"]["serviceNumber"] = "INVALID-SERVICE"
    elif "inactive service" in title:
        body["service"]["id"] = _inactive_service_id(db)
    elif "pending transaction or open workorder" in title:
        body["service"] = _in_transaction_service_body(db)
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
    elif "due date null" in title:
        body["dueDate"] = None
    elif "due date blank" in title:
        body["dueDate"] = ""
    elif "due date past" in title:
        body["dueDate"] = (date.today() - timedelta(days=1)).isoformat()
    elif "due date invalid calendar" in title:
        body["dueDate"] = "2026-02-30"
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


class TestPostSelfServiceTravelPackage:
    @pytest.mark.parametrize("case", CASE_PARAMS)
    def test_post_self_service_travel_package(self, case: TravelPackageCase, api_token, db, request):
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
                assert row and int(row["OperationTypeId"]) == OPERATION_TYPE_ID and int(row["StatusId"]) == 1
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
