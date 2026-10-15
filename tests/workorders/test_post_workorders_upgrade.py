"""Data-driven validation for POST /v1/workorders/upgrade."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import UUID

import pytest
import requests

from utils.post_api_db_scripts import PostApiDbScripts
from utils.organization_types import cost_center_type_sql


@dataclass(frozen=True)
class UpgradeCase:
    case_id: str
    title: str
    label: str


CASES = [
    UpgradeCase(case_id, title, label)
    for case_id, title, label in [
        ("17799857", "Create Upgrade workorder with compatible data", "Positive"), ("17799858", "Upgrade with ignoreWarnings false and ineligible service", "Validation"), ("17799859", "Upgrade with ignoreWarnings true and ineligible service", "Positive"),
        ("17799860", "Missing requester", "Validation"), ("17799861", "Requester ID only", "Positive"), ("17799862", "Requester email only", "Positive"), ("17799863", "Requester number only", "Positive"), ("17799864", "Requester identifiers mismatch", "Validation"),
        ("17799865", "Missing service", "Validation"), ("17799866", "Service ID only", "Positive"), ("17799867", "Service ID and providerId only", "Positive"), ("17799868", "Inactive service", "Validation"), ("17799869", "Service with open workorder", "Validation"),
        ("17799870", "Missing newCatalogItemId", "Validation"), ("17799871", "Incompatible newCatalogItemId", "Validation"), ("17799872", "Missing newContractId", "Validation"), ("17799873", "Incompatible newContractId", "Validation"),
        ("17799874", "Missing shippingAddress", "Validation"), ("17799875", "Blank shippingAddress fields", "Validation"), ("17799876", "Missing newContractStartDate", "Validation"), ("17799877", "Invalid newContractStartDate format", "Validation"),
        ("17799878", "Compatible new option", "Positive"), ("17799879", "Incompatible new option", "Validation"), ("17799880", "Existing contract option as new option", "Validation"),
        ("17799881", "Provider contact valid for provider", "Positive"), ("17799882", "Provider contact invalid for provider", "Validation"), ("17799883", "Provider contact inactive", "Validation"),
        ("17799884", "Warehouse CanAdd enabled", "Positive"), ("17799885", "Warehouse CanAdd disabled", "Validation"), ("17799886", "Current asset omitted", "Positive"), ("17799887", "Current asset not related to service", "Validation"),
        ("17799888", "CostCenter valid", "Positive"), ("17799889", "CostCenter invalid", "Validation"), ("17799890", "CultureCode invalid", "Positive"), ("17799891", "isUrgent null", "Positive"), ("17799892", "showInProviderPortal null", "Validation"),
        ("17799893", "Accessories compatible count 3", "Positive"), ("17799894", "Accessories count exceeds 3", "Validation"), ("17799895", "Unauthorized Upgrade request", "Authorization"),
        ("17838261", "Requester null", "Validation"), ("17838262", "Requester empty object", "Validation"), ("17838263", "Requester invalid ID", "Validation"), ("17838264", "Requester invalid email", "Validation"), ("17838265", "Requester invalid number", "Validation"),
        ("17838266", "Inactive requester", "Validation"), ("17838267", "Requester ID and number mismatch", "Validation"),
        ("17838268", "Due date omitted", "Positive"), ("17838269", "Due date null", "Positive"), ("17838270", "Due date blank", "Positive"), ("17838271", "Due date in the past", "Validation"), ("17838272", "Due date current date", "Positive"), ("17838273", "Due date invalid calendar date", "Validation"), ("17838274", "Due date leap year", "Positive"),
        ("17838275", "Comments omitted", "Positive"), ("17838276", "Comments null", "Positive"), ("17838277", "Comments blank", "Positive"), ("17838278", "Comments SQL injection input", "Positive"), ("17838279", "Comments XSS input", "Positive"), ("17838280", "Comments Unicode characters", "Positive"),
        ("17838281", "Service null", "Validation"), ("17838282", "Service missing providerId", "Validation"), ("17838283", "Service missing accountNumber", "Validation"), ("17838284", "Service missing serviceNumber", "Validation"), ("17838285", "Service invalid individual values", "Validation"), ("17838286", "Service invalid service ID", "Validation"), ("17838287", "Service invalid account number with service ID", "Validation"), ("17838288", "Service invalid providerId with service ID", "Validation"), ("17838289", "Service invalid primary number with service ID", "Validation"),
        ("17838290", "Catalog contract incompatibility", "Validation"), ("17838291", "Catalog provider incompatibility", "Validation"), ("17838292", "Unsupported hardware category", "Validation"), ("17838293", "Inactive catalog item", "Validation"),
        ("17838294", "Contract inactive", "Validation"), ("17838295", "Contract invalid", "Validation"), ("17838296", "Contract wrong provider", "Positive"), ("17838297", "Contract wrong service type", "Validation"), ("17838298", "Contract existing option", "Validation"),
        ("17838299", "Shipping address missing child field", "Validation"), ("17838300", "Shipping address invalid country state postal", "Validation"), ("17838301", "AttentionTo special characters", "Positive"),
        ("17838302", "Warehouse omitted", "Positive"), ("17838303", "Warehouse null", "Positive"), ("17838304", "Warehouse invalid", "Validation"), ("17838305", "Warehouse inactive", "Validation"),
        ("17838306", "Current asset valid different service", "Validation"), ("17838307", "New options omitted", "Positive"), ("17838308", "New options null", "Positive"), ("17838309", "Duplicate new options", "Validation"), ("17838310", "Self provisioning options only false", "Positive"),
        ("17838311", "Cost center omitted", "Validation"), ("17838312", "Cost center null", "Validation"), ("17838313", "Cost center inactive", "Positive"), ("17838314", "Cost center closed", "Validation"), ("17838315", "Cost center zero", "Validation"), ("17838316", "Cost center non-cost-center organization", "Validation"),
        ("17838317", "Culture omitted", "Positive"), ("17838318", "Culture null", "Positive"), ("17838319", "Culture blank", "Positive"), ("17838320", "Culture fr", "Positive"), ("17838321", "isUrgent omitted", "Positive"), ("17838322", "isUrgent false", "Positive"), ("17838323", "showInProviderPortal omitted", "Positive"), ("17838324", "showInProviderPortal false", "Positive"),
        ("17838325", "Subject omitted", "Positive"), ("17838326", "Subject blank", "Positive"), ("17838327", "Subject over 256 characters", "Validation"), ("17838328", "Reference number over 50 characters", "Validation"),
        ("17838329", "Provider contact omitted", "Positive"), ("17838330", "Provider contact null", "Positive"), ("17838331", "Provider contact invalid", "Validation"), ("17838332", "Provider contact wrong provider", "Validation"), ("17838333", "Work order owner", "Positive"),
        ("17838395", "Catalog valid for provider but wrong service type", "Validation"), ("17838396", "Catalog valid for service type but wrong provider", "Validation"), ("17838397", "Catalog item inactive", "Validation"), ("17838398", "Catalog unsupported hardware category", "Validation"),
        ("17838399", "Contract valid but wrong provider", "Validation"), ("17838400", "Contract valid but wrong service type", "Validation"), ("17838401", "Existing contract option", "Validation"),
        ("17838402", "Provider contact valid user from another provider", "Validation"), ("17838403", "Provider contact inactive for selected provider", "Validation"),
        ("17838404", "Cost center omitted when required", "Validation"), ("17838405", "Cost center is a valid organization but not a cost center", "Validation"),
    ]
]
PARAMS = [pytest.param(case, id=f"C{case.case_id}", marks=[pytest.mark.swagger_section("Workorders"), pytest.mark.swagger_operation("POST /v1/workorders/upgrade"), pytest.mark.testrail_case(case.case_id), pytest.mark.dashboard_title(case.title)]) for case in CASES]


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
    row = db.fetch_one("SELECT TOP 1 EmployeeId AS id,Number AS number,EMail AS email FROM cimpl.Employee WHERE EmployeeStatusId " + ("=1" if active else "<>1") + " AND EMail IS NOT NULL " + ("AND EmployeeId<>?" if exclude_id else "") + " ORDER BY EmployeeId DESC", (exclude_id,) if exclude_id else ())
    assert row, "No employee seed available"
    return {"id": int(row["id"]), "number": str(row.get("number") or ""), "email": str(row["email"])}


def _seed(db, *, self_service: bool = False, provider_id: int | None = None, service_type_id: int | None = None) -> dict:
    self_service_filter = " AND c.SelfProvisioningEnabled=1" if self_service else ""
    provider_filter = " AND si.ProviderId=?" if provider_id is not None else ""
    service_type_filter = " AND si.ServiceTypeId=?" if service_type_id is not None else ""
    seed_params = tuple(value for value in (provider_id, service_type_id) if value is not None)
    row = db.fetch_one("""SELECT TOP 1 si.ServiceInventoryId AS serviceId,si.ProviderId AS providerId,si.ServiceTypeId AS serviceTypeId,a.Number AS accountNumber,si.PrimaryNumber AS serviceNumber,hir.HardwareInventoryId AS currentAssetId,c.ContractId AS newContractId,new_hw.HardwareTypeId AS newCatalogItemId
    FROM cimpl.ServiceInventory si INNER JOIN cimpl.Account a ON a.AccountId=si.AccountId INNER JOIN cimpl.ServiceInventoryHardwareInventoryRelation hir ON hir.ServiceInventoryId=si.ServiceInventoryId AND hir.IsPrimary=1 INNER JOIN cimpl.Contract c ON c.ProviderId=si.ProviderId AND c.ServiceTypeId=si.ServiceTypeId AND c.Active=1 AND c.ContractId<>si.ContractId""" + self_service_filter + """ INNER JOIN cimpl.HardwareTypeContractCompatibility htc ON htc.ContractId=c.ContractId INNER JOIN cimpl.HardwareTypeProviderCompatibility htp ON htp.HardwareTypeId=htc.HardwareTypeId AND htp.ProviderId=si.ProviderId INNER JOIN cimpl.ServiceTypeHardwareTypeCompatibility sth ON sth.HardwareTypeId=htc.HardwareTypeId AND sth.ServiceTypeId=si.ServiceTypeId INNER JOIN cimpl.HardwareType new_hw ON new_hw.HardwareTypeId=htc.HardwareTypeId AND new_hw.Active=1 AND new_hw.HardwareCategoryId IN (1,3,4) WHERE si.ServiceInventoryStatusId=1 AND si.ProviderId IS NOT NULL AND si.PrimaryNumber IS NOT NULL""" + provider_filter + service_type_filter + """ AND NOT EXISTS (SELECT 1 FROM cimpl.TransactServiceInventory tsi WHERE tsi.TransactId=(SELECT TOP 1 latest.TransactId FROM cimpl.TransactServiceInventory latest WHERE latest.ServiceInventoryId=si.ServiceInventoryId ORDER BY latest.TransactId DESC)) AND NOT EXISTS (SELECT 1 FROM cimpl.TransactWorkorderService tws INNER JOIN cimpl.WorkorderSummary ws ON ws.WorkorderId=tws.WorkorderId INNER JOIN cimpl.WorkorderStatus wos ON wos.WorkorderStatusId=ws.WorkorderStatusId AND wos.TenantId=ws.TenantId WHERE tws.ServiceInventoryId=si.ServiceInventoryId AND tws.WorkorderId=(SELECT TOP 1 latest.WorkorderId FROM cimpl.TransactWorkorderService latest WHERE latest.ServiceInventoryId=si.ServiceInventoryId ORDER BY latest.WorkorderId DESC) AND LOWER(wos.Name) NOT IN ('completed','cancelled','canceled')) AND NOT EXISTS (SELECT 1 FROM cimpl.SelfServiceActionLog ssl WHERE ssl.ServiceInventoryId=si.ServiceInventoryId AND ssl.StatusId=1 AND ssl.WorkorderId IS NULL AND ssl.UpdatedDate IS NULL) ORDER BY si.ServiceInventoryId DESC""", seed_params)
    assert row, "No fully compatible active transaction-free Upgrade seed available for the selected filters"
    address = db.fetch_one("""SELECT TOP 1 a.LocationName AS locationName,a.LocationNumber AS locationNumber,a.AddressLine1 AS line1,a.AddressLine2 AS line2,a.City AS city,p.Code AS state,c.Iso3 AS countryCode,a.PostalCode AS postalCode FROM cimpl.Address a INNER JOIN cimpl.Province p ON p.ProvinceId=a.ProvinceId INNER JOIN cimpl.Country c ON c.CountryId=a.CountryId WHERE a.Active=1 AND a.LocationName IS NOT NULL AND a.LocationNumber IS NOT NULL AND a.AddressLine1 IS NOT NULL AND a.City IS NOT NULL AND a.PostalCode IS NOT NULL ORDER BY a.AddressId DESC""")
    assert address, "No complete active shipping address available"
    row["shippingAddress"] = {key: str(value or "") for key, value in address.items()}
    row["warehouseId"] = int(db.fetch_scalar("SELECT TOP 1 WarehouseId FROM cimpl.Warehouse WHERE Active=1 AND CanAdd=1 ORDER BY WarehouseId DESC"))
    row["costCenterId"] = int(db.fetch_scalar("SELECT TOP 1 OrganizationId FROM cimpl.Organization WHERE Active=1 AND OrganizationStatusId=1 AND " + cost_center_type_sql("OrganizationTypeId") + " ORDER BY OrganizationId DESC"))
    return row


def _service_body(seed: dict) -> dict:
    return {"id": int(seed["serviceId"]), "accountNumber": str(seed["accountNumber"]), "providerId": int(seed["providerId"]), "serviceNumber": str(seed["serviceNumber"])}


def _seed_filters_from_env() -> tuple[int | None, int | None]:
    provider_id = int(os.getenv("TEST_PROVIDER_ID")) if os.getenv("TEST_PROVIDER_ID", "").strip() else None
    service_type_id = int(os.getenv("TEST_SERVICE_TYPE_ID")) if os.getenv("TEST_SERVICE_TYPE_ID", "").strip() else None
    return provider_id, service_type_id


def preview_seed_compatibility(db, *, provider_id: int | None = None, service_type_id: int | None = None, limit: int = 2) -> list[dict]:
    """Return up to `limit` Provider/ServiceType pairs with full end-to-end Upgrade compatibility."""
    provider_filter = " AND si.ProviderId=?" if provider_id is not None else ""
    service_type_filter = " AND si.ServiceTypeId=?" if service_type_id is not None else ""
    params: list[int] = [limit] + [value for value in (provider_id, service_type_id) if value is not None]
    rows = db.fetch_all(
        """SELECT DISTINCT TOP (?) si.ProviderId AS providerId, p.Name AS providerName, si.ServiceTypeId AS serviceTypeId, st.Name AS serviceTypeName
        FROM cimpl.ServiceInventory si
        INNER JOIN cimpl.Account a ON a.AccountId=si.AccountId
        INNER JOIN cimpl.Provider p ON p.ProviderId=si.ProviderId
        INNER JOIN cimpl.ServiceType st ON st.ServiceTypeId=si.ServiceTypeId
        INNER JOIN cimpl.ServiceInventoryHardwareInventoryRelation hir ON hir.ServiceInventoryId=si.ServiceInventoryId AND hir.IsPrimary=1
        INNER JOIN cimpl.Contract c ON c.ProviderId=si.ProviderId AND c.ServiceTypeId=si.ServiceTypeId AND c.Active=1 AND c.ContractId<>si.ContractId
        INNER JOIN cimpl.HardwareTypeContractCompatibility htc ON htc.ContractId=c.ContractId
        INNER JOIN cimpl.HardwareTypeProviderCompatibility htp ON htp.HardwareTypeId=htc.HardwareTypeId AND htp.ProviderId=si.ProviderId
        INNER JOIN cimpl.ServiceTypeHardwareTypeCompatibility sth ON sth.HardwareTypeId=htc.HardwareTypeId AND sth.ServiceTypeId=si.ServiceTypeId
        INNER JOIN cimpl.HardwareType new_hw ON new_hw.HardwareTypeId=htc.HardwareTypeId AND new_hw.Active=1 AND new_hw.HardwareCategoryId IN (1,3,4)
        WHERE si.ServiceInventoryStatusId=1 AND si.ProviderId IS NOT NULL AND si.PrimaryNumber IS NOT NULL"""
        + provider_filter + service_type_filter +
        """ AND NOT EXISTS (SELECT 1 FROM cimpl.TransactServiceInventory tsi WHERE tsi.TransactId=(SELECT TOP 1 latest.TransactId FROM cimpl.TransactServiceInventory latest WHERE latest.ServiceInventoryId=si.ServiceInventoryId ORDER BY latest.TransactId DESC))
        AND NOT EXISTS (SELECT 1 FROM cimpl.SelfServiceActionLog ssl WHERE ssl.ServiceInventoryId=si.ServiceInventoryId AND ssl.StatusId=1 AND ssl.WorkorderId IS NULL AND ssl.UpdatedDate IS NULL)
        ORDER BY si.ProviderId DESC""",
        tuple(params),
    )
    return [
        {"providerId": int(row["providerId"]), "providerName": str(row.get("providerName") or ""), "serviceTypeId": int(row["serviceTypeId"]), "serviceTypeName": str(row.get("serviceTypeName") or "")}
        for row in rows
    ]


def _payload(db, case: UpgradeCase) -> tuple[dict, dict]:
    provider_id, service_type_id = _seed_filters_from_env()
    seed = _seed(db, provider_id=provider_id, service_type_id=service_type_id); marker = f"AUTO-UPGRADE-C{case.case_id}-{datetime.now():%Y%m%d%H%M%S%f}"
    body = {"newCatalogItemId": int(seed["newCatalogItemId"]), "newContractId": int(seed["newContractId"]), "newContractStartDate": date.today().isoformat(), "shippingAddress": seed["shippingAddress"], "attentionTo": "Upgrade Receiver", "warehouseId": seed["warehouseId"], "currentAssetId": int(seed["currentAssetId"]), "ignoreWarnings": True, "costCenterId": seed["costCenterId"], "cultureCode": "en-US", "isUrgent": True, "subject": "AUTOMATION UPGRADE", "referenceNo": marker, "showInProviderPortal": True, "service": _service_body(seed), "requester": _employee(db), "dueDate": (date.today() + timedelta(days=2)).isoformat(), "comments": marker}
    return body, {"seed": seed, "marker": marker}


def _mutate(case: UpgradeCase, body: dict, context: dict, db):
    title = case.title.lower(); seed = context["seed"]
    if "requester null" in title: body["requester"] = None
    elif "requester empty" in title: body["requester"] = {}
    elif "requester invalid id" in title: body["requester"]["id"] = 999999999
    elif "requester invalid email" in title: body["requester"] = {"email": "missing@example.invalid"}
    elif "requester invalid number" in title: body["requester"] = {"number": "INVALID-REQUESTER"}
    elif "inactive requester" in title: body["requester"] = _employee(db, active=False)
    elif "requester id and number mismatch" in title: body["requester"]["number"] = _employee(db, exclude_id=body["requester"]["id"])["number"]
    elif "missing requester" in title: body.pop("requester")
    elif "requester id only" in title: body["requester"] = {"id": body["requester"]["id"]}
    elif "requester email only" in title: body["requester"] = {"email": body["requester"]["email"]}
    elif "requester number only" in title: body["requester"] = {"number": body["requester"]["number"]}
    elif "identifiers mismatch" in title: body["requester"]["email"] = _employee(db, exclude_id=body["requester"]["id"])["email"]
    elif "service null" in title: body["service"] = None
    elif "service missing providerid" in title: body["service"].pop("providerId")
    elif "service missing accountnumber" in title: body["service"].pop("accountNumber")
    elif "service missing servicenumber" in title: body["service"].pop("serviceNumber")
    elif "service invalid individual" in title: body["service"] = {"id": 999999999, "accountNumber": "INVALID", "providerId": 999999999, "serviceNumber": "INVALID"}
    elif "service invalid service id" in title: body["service"]["id"] = 999999999
    elif "invalid account number with service" in title: body["service"]["accountNumber"] = "INVALID-ACCOUNT"
    elif "invalid providerid with service" in title: body["service"]["providerId"] = 999999999
    elif "invalid primary number with service" in title: body["service"]["serviceNumber"] = "INVALID-SERVICE"
    elif "missing service" in title: body.pop("service")
    elif "service id only" in title: body["service"] = {"id": body["service"]["id"]}
    elif "id and providerid only" in title: body["service"] = {"id": body["service"]["id"], "providerId": body["service"]["providerId"]}
    elif "inactive service" in title: body["service"]["id"] = 311
    elif "catalog valid for provider but wrong service" in title:
        value = db.fetch_scalar("SELECT TOP 1 h.HardwareTypeId FROM cimpl.HardwareType h INNER JOIN cimpl.HardwareTypeProviderCompatibility p ON p.HardwareTypeId=h.HardwareTypeId AND p.ProviderId=? WHERE h.Active=1 AND h.HardwareCategoryId IN (1,3,4) AND NOT EXISTS (SELECT 1 FROM cimpl.ServiceTypeHardwareTypeCompatibility s WHERE s.HardwareTypeId=h.HardwareTypeId AND s.ServiceTypeId=?) ORDER BY h.HardwareTypeId DESC", (seed["providerId"], seed["serviceTypeId"]))
        assert value, "No provider-compatible, service-incompatible catalog seed available"; body["newCatalogItemId"] = int(value)
    elif "catalog valid for service type but wrong provider" in title:
        value = db.fetch_scalar("SELECT TOP 1 h.HardwareTypeId FROM cimpl.HardwareType h INNER JOIN cimpl.ServiceTypeHardwareTypeCompatibility s ON s.HardwareTypeId=h.HardwareTypeId AND s.ServiceTypeId=? WHERE h.Active=1 AND h.HardwareCategoryId IN (1,3,4) AND NOT EXISTS (SELECT 1 FROM cimpl.HardwareTypeProviderCompatibility p WHERE p.HardwareTypeId=h.HardwareTypeId AND p.ProviderId=?) ORDER BY h.HardwareTypeId DESC", (seed["serviceTypeId"], seed["providerId"]))
        assert value, "No service-compatible, provider-incompatible catalog seed available"; body["newCatalogItemId"] = int(value)
    elif "catalog item inactive" in title: body["newCatalogItemId"] = int(db.fetch_scalar("SELECT TOP 1 HardwareTypeId FROM cimpl.HardwareType WHERE Active=0 ORDER BY HardwareTypeId DESC"))
    elif "catalog unsupported" in title: body["newCatalogItemId"] = int(db.fetch_scalar("SELECT TOP 1 HardwareTypeId FROM cimpl.HardwareType WHERE Active=1 AND HardwareCategoryId NOT IN (1,3,4) ORDER BY HardwareTypeId DESC"))
    elif "invalid newcatalog" in title: body["newCatalogItemId"] = 999999999
    elif "missing newcatalog" in title: body.pop("newCatalogItemId")
    elif "missing newcontract" in title: body.pop("newContractId")
    elif "contract valid but wrong provider" in title: body["newContractId"] = int(db.fetch_scalar("SELECT TOP 1 ContractId FROM cimpl.Contract WHERE Active=1 AND ProviderId<>? ORDER BY ContractId DESC", (seed["providerId"],)))
    elif "contract valid but wrong service" in title: body["newContractId"] = int(db.fetch_scalar("SELECT TOP 1 ContractId FROM cimpl.Contract WHERE Active=1 AND ServiceTypeId<>? ORDER BY ContractId DESC", (seed["serviceTypeId"],)))
    elif "contract inactive" in title: body["newContractId"] = int(db.fetch_scalar("SELECT TOP 1 ContractId FROM cimpl.Contract WHERE Active=0 ORDER BY ContractId DESC"))
    elif "incompatible newcontract" in title: body["newContractId"] = 999999999
    elif "missing shipping" in title: body.pop("shippingAddress")
    elif "blank shipping" in title: body["shippingAddress"] = {key: "" for key in body["shippingAddress"]}
    elif "missing newcontractstart" in title: body.pop("newContractStartDate")
    elif "invalid newcontractstart" in title: body["newContractStartDate"] = "12/31/2026"
    elif "due date omitted" in title: body.pop("dueDate")
    elif "due date null" in title: body["dueDate"] = None
    elif "due date blank" in title: body["dueDate"] = ""
    elif "due date in the past" in title: body["dueDate"] = (date.today() - timedelta(days=1)).isoformat()
    elif "due date current date" in title: body["dueDate"] = date.today().isoformat()
    elif "due date invalid calendar" in title: body["dueDate"] = "2026-02-30"
    elif "due date leap year" in title: body["dueDate"] = "2028-02-29"
    elif "comments omitted" in title: body.pop("comments")
    elif "comments null" in title: body["comments"] = None
    elif "comments blank" in title: body["comments"] = ""
    elif "comments sql injection" in title: body["comments"] = "'; DROP TABLE WorkorderSummary;--"
    elif "comments xss" in title: body["comments"] = "<script>alert('x')</script>"
    elif "comments unicode" in title: body["comments"] = "Mise a niveau cafe - 日本語"
    elif "warehouse canadd disabled" in title: body["warehouseId"] = int(db.fetch_scalar("SELECT TOP 1 WarehouseId FROM cimpl.Warehouse WHERE Active=1 AND CanAdd=0 ORDER BY WarehouseId DESC"))
    elif "current asset omitted" in title: body.pop("currentAssetId")
    elif "current asset not" in title: body["currentAssetId"] = 999999999
    elif "cost center omitted" in title: body.pop("costCenterId")
    elif "cost center inactive" in title: body["costCenterId"] = int(db.fetch_scalar("SELECT TOP 1 OrganizationId FROM cimpl.Organization WHERE Active=0 ORDER BY OrganizationId DESC"))
    elif "cost center closed" in title: body["costCenterId"] = int(db.fetch_scalar("SELECT TOP 1 OrganizationId FROM cimpl.Organization WHERE Active=1 AND OrganizationStatusId<>1 AND " + cost_center_type_sql("OrganizationTypeId") + " ORDER BY OrganizationId DESC"))
    elif "valid organization but not" in title: body["costCenterId"] = int(db.fetch_scalar("SELECT TOP 1 OrganizationId FROM cimpl.Organization WHERE Active=1 AND NOT (" + cost_center_type_sql("OrganizationTypeId") + ") ORDER BY OrganizationId DESC"))
    elif "costcenter invalid" in title: body["costCenterId"] = int(db.fetch_scalar("SELECT TOP 1 OrganizationId FROM cimpl.Organization WHERE Active=1 AND " + cost_center_type_sql("OrganizationTypeId", required=False) + " ORDER BY OrganizationId DESC"))
    elif "culturecode invalid" in title: body["cultureCode"] = "invalid-culture"
    elif "isurgent null" in title: body["isUrgent"] = None
    elif "showinproviderportal null" in title: body["showInProviderPortal"] = None
    elif "warehouse omitted" in title: body.pop("warehouseId")
    elif "warehouse null" in title: body["warehouseId"] = None
    elif "warehouse invalid" in title: body["warehouseId"] = 999999999
    elif "warehouse inactive" in title: body["warehouseId"] = int(db.fetch_scalar("SELECT TOP 1 WarehouseId FROM cimpl.Warehouse WHERE Active=0 ORDER BY WarehouseId DESC"))
    elif "shipping address missing child" in title: body["shippingAddress"].pop("postalCode")
    elif "shipping address invalid" in title: body["shippingAddress"].update({"countryCode": "ZZZ", "state": "INVALID", "postalCode": "INVALID"})
    elif "attentionto special" in title: body["attentionTo"] = "A&B / O'Neil - 日本語"
    elif "current asset valid different" in title: body["currentAssetId"] = int(db.fetch_scalar("SELECT TOP 1 HardwareInventoryId FROM cimpl.HardwareInventory WHERE HardwareInventoryId<>? ORDER BY HardwareInventoryId DESC", (seed["currentAssetId"],)))
    elif "culture omitted" in title: body.pop("cultureCode")
    elif "culture null" in title: body["cultureCode"] = None
    elif "culture blank" in title: body["cultureCode"] = ""
    elif "culture fr" in title: body["cultureCode"] = "fr"
    elif "isurgent omitted" in title: body.pop("isUrgent")
    elif "isurgent false" in title: body["isUrgent"] = False
    elif "showinproviderportal omitted" in title: body.pop("showInProviderPortal")
    elif "showinproviderportal false" in title: body["showInProviderPortal"] = False
    elif "subject omitted" in title: body.pop("subject")
    elif "subject blank" in title: body["subject"] = ""
    elif "subject over" in title: body["subject"] = "S" * 257
    elif "reference number over" in title: body["referenceNo"] = "R" * 51
    elif "provider contact omitted" in title: body.pop("providerContact", None)
    elif "provider contact null" in title: body["providerContact"] = None
    elif "provider contact invalid" in title: body["providerContact"] = {"groupId": 9998}
    elif "work order owner" in title: body["workOrderOwnerId"] = body["requester"]["id"]
    elif "unauthorized" in title: pass


class TestPostWorkordersUpgrade:
    @pytest.mark.parametrize("case", PARAMS)
    def test_post_workorders_upgrade(self, case, api_context, db, request):
        body, context = _payload(db, case); _mutate(case, body, context, db)
        scripts = PostApiDbScripts(db); marker = str(body.get("comments") or ""); before = scripts.count_rows(table="cimpl.WorkorderSummary", where_sql="Comment LIKE ?", params=(f"%{marker}%",)) if marker else None
        metrics = [f"CaseId: {case.case_id}", "Seed: " + json.dumps(context["seed"], default=str), f"Request body: {json.dumps(body, ensure_ascii=True)}"]
        response = requests.post(f"{os.environ['BASE_URL'].rstrip('/')}/workorders/upgrade", json=body, headers={"accept":"application/json"}, timeout=int(os.getenv("API_REQUEST_TIMEOUT_MS","120000"))/1000) if case.label == "Authorization" else api_context.post("workorders/upgrade", data=json.dumps(body), headers={"Content-Type":"application/json","accept":"application/json"})
        try:
            payload = _json(response)
            if case.label == "Positive":
                assert _status(response) in {200,201}, f"{case.title}: expected success, got {_status(response)}: {_text(response)}"
                number = str(payload.get("workorderNumber") or "").strip(); assert number, f"{case.title}: missing workorderNumber"
                if "compatible data" in case.title.lower(): UUID(str(payload.get("requestId") or ""))
                query = (
                    "SELECT w.Number AS workorderNumber, tws.TransactId AS transactId, "
                    "tsi.ServiceInventoryId AS serviceId, tsi.ContractId AS contractId, "
                    "tsi.OperationTypeId AS operationTypeId "
                    "FROM cimpl.Workorder w "
                    "INNER JOIN cimpl.TransactWorkorderService tws ON tws.WorkorderId = w.WorkorderId "
                    "INNER JOIN cimpl.TransactServiceInventory tsi ON tsi.TransactId = tws.TransactId "
                    "AND tsi.ServiceInventoryId = tws.ServiceInventoryId WHERE w.Number = ?"
                )
                row = scripts.assert_single_row(query=query, params=(number,), context=case.title)
                assert int(row["serviceId"]) == int(body["service"]["id"]), f"{case.title}: pending service mismatch"
                assert int(row["contractId"]) == int(body["newContractId"]), f"{case.title}: pending new contract mismatch"
                metrics.extend([f"Response status: {_status(response)}",f"Response payload: {json.dumps(payload)}","DB validation: "+scripts.format_snapshot(row=row,keys=list(row))])
            elif case.label == "Authorization": assert _status(response) in {401,403}, f"{case.title}: expected 401/403, got {_status(response)}"
            else:
                assert _status(response) in {400,404,406,409,500}, f"{case.title}: expected validation error, got {_status(response)}: {_text(response)}"
                if marker and before is not None:
                    after=scripts.count_rows(table="cimpl.WorkorderSummary",where_sql="Comment LIKE ?",params=(f"%{marker}%",)); scripts.assert_count_delta(before=before,after=after,expected_delta=0,context=case.title)
                metrics.extend([f"Response status: {_status(response)}",f"Response payload: {json.dumps(payload)}"])
        finally:
            if sys.exc_info()[0] is not None: metrics.extend([f"Response status: {_status(response)}",f"Response body: {_text(response)}"])
            _record(request,metrics)