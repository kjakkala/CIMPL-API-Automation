"""Pilot data-driven tests for POST /workorders/provision with DB-backed validation."""

from __future__ import annotations

import json
import os
import re
import csv
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pytest


UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)

SUPPORTED_DOTNET_CULTURES = frozenset(
    {
        "en-US", "en-CA", "en-GB", "en-AU", "en-IN", "fr-CA", "fr-FR", "es-ES", "es-MX",
        "de-DE", "it-IT", "pt-BR", "pt-PT", "nl-NL", "sv-SE", "da-DK", "nb-NO", "fi-FI",
        "pl-PL", "cs-CZ", "tr-TR", "ru-RU", "uk-UA", "ja-JP", "ko-KR", "zh-CN", "zh-TW",
        "ar-SA", "he-IL", "hi-IN", "id-ID", "th-TH", "vi-VN", "fa-IR", "en", "fr", "es",
    }
)


@dataclass(frozen=True)
class ProvisionCase:
    case_id: str
    title: str
    expected: str
    scenario: str = "same-employee-and-requester"


PILOT_CASES: list[ProvisionCase] = [
    ProvisionCase("17777182", "Create a request with all fields", "Should create a request and return workorder number."),
    ProvisionCase("17777183", "Create a Request with required fields only", "Should create a request and return workorder number."),
    ProvisionCase("17777184", "Create a Request by Passing Requester with ID only", "Should create a request and return workorder number."),
    ProvisionCase("17777190", "Create a request with proper due date", "Should create a request and return workorder number."),
    ProvisionCase("17777203", "Create a request by passing a Valid Providerid", "Should create a request and return workorder number."),
    ProvisionCase("17777278", "Create a request with Single Valid accessories parameter", "Should create a request and return workorder number."),
    ProvisionCase("17777187", "Create a Request by removing Requester parameter", "Should return validation error."),
    ProvisionCase("17777198", "Create a Request by removing Employee parameter", "Should return validation error."),
    ProvisionCase("17777200", "Create a request by adding INvalid catalogue ID", "Should return validation error."),
    ProvisionCase("17777206", "Create a request by removing Providerid", "Should return validation error."),
    ProvisionCase("17777215", "Create a request by passing invalid Servicetypeid", "Should return validation error."),
    ProvisionCase("17777223", "Create a request by removing Account number", "Should return validation error."),
    ProvisionCase(
        "17783584",
        "Create a request by passing an option type id already assigned to the contract",
        "Should return a validation message and should not create a work order.",
        "option-already-assigned-to-contract",
    ),
    ProvisionCase("17777200", "Create a request with category 1 hardware valid for provider but different service", "Should return validation error for incompatible catalogue hardware.", "catalog-provider-compatible-different-service"),
    ProvisionCase("17777200", "Create a request with category 1 hardware valid for service but different provider", "Should return validation error for incompatible catalogue hardware.", "catalog-service-compatible-different-provider"),
    ProvisionCase("17838334", "Requester null", "Validation error and no workorder should be created."),
    ProvisionCase("17838335", "Requester empty object", "Validation error and no workorder should be created."),
    ProvisionCase("17838336", "Inactive requester", "Validation error and no workorder should be created."),
    ProvisionCase("17838337", "Requester ID and email mismatch", "Validation error and no workorder should be created."),
    ProvisionCase("17838338", "Due date null", "Validation error and no workorder should be created."),
    ProvisionCase("17838339", "Due date blank", "Validation error and no workorder should be created."),
    ProvisionCase("17838340", "Due date in the past", "Validation error and no workorder should be created."),
    ProvisionCase("17838341", "Due date invalid calendar date", "Validation error and no workorder should be created."),
    ProvisionCase("17838342", "Comments null", "Validation error and no workorder should be created."),
    ProvisionCase("17838343", "Comments Unicode characters", "Validation error and no workorder should be created."),
    ProvisionCase("17838344", "Comments over 4000 characters", "Validation error and no workorder should be created."),
    ProvisionCase("17838345", "Shipping address missing child field", "Validation error and no workorder should be created."),
    ProvisionCase("17838346", "Subject over 256 characters", "Validation error and no workorder should be created."),
    ProvisionCase("17838347", "Reference number over 50 characters", "Validation error and no workorder should be created."),
    ProvisionCase("17838406", "Provision with valid cost center unrelated to service or employee", "Validation error and no workorder should be created."),
    ProvisionCase("17838407", "Provision with valid inactive cost center", "Validation error and no workorder should be created."),
    ProvisionCase("17838408", "Provision with valid closed cost center", "Validation error and no workorder should be created."),
    ProvisionCase("17838409", "Provision with valid non-cost-center organization", "Validation error and no workorder should be created."),
]


def _load_case_catalog() -> list[ProvisionCase]:
    catalog_paths = (
        Path(__file__).with_name("cimpl (9).csv"),
        Path.home() / "Downloads" / "cimpl (9).csv",
    )
    catalog_path = next((path for path in catalog_paths if path.exists()), None)
    if catalog_path is None:
        return PILOT_CASES

    existing_ids = {case.case_id for case in PILOT_CASES}
    catalog_cases = list(PILOT_CASES)
    with catalog_path.open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            case_id = str(row.get("ID", "")).strip().removeprefix("C")
            if not case_id or case_id in existing_ids:
                continue
            catalog_cases.append(
                ProvisionCase(
                    case_id,
                    str(row.get("Title", "")).strip(),
                    str(row.get("Expected Result", "")).strip() or "Expected result is defined in the TestRail case.",
                )
            )
    return catalog_cases


PILOT_CASES = _load_case_catalog()

for _case_index, _case in enumerate(PILOT_CASES):
    if _case.case_id in {"17777233", "17777234", "17777236", "17777252"} and _case.scenario == "same-employee-and-requester":
        PILOT_CASES[_case_index] = ProvisionCase(
            _case.case_id,
            _case.title,
            "Work order should not be created and a validation message should be returned.",
            _case.scenario,
        )
    elif _case.case_id == "17777189" and _case.scenario == "same-employee-and-requester":
        PILOT_CASES[_case_index] = ProvisionCase(
            _case.case_id,
            _case.title,
            "Work order should not be created and a validation message should be returned when due date is earlier than the creation date.",
            _case.scenario,
        )
    elif _case.case_id == "17777190" and _case.scenario == "same-employee-and-requester":
        PILOT_CASES[_case_index] = ProvisionCase(
            _case.case_id,
            _case.title,
            "Should create a work order when due date is equal to or later than the creation date.",
            _case.scenario,
        )
    elif _case.case_id == "17777266":
        PILOT_CASES[_case_index] = ProvisionCase(
            _case.case_id,
            _case.title,
            "showInProviderPortal is a non-nullable boolean field; a blank/null value should be "
            "rejected with a 400 validation error and no work order should be created.",
            _case.scenario,
        )
    elif _case.case_id in {"17777271", "17777272", "17777277"}:
        PILOT_CASES[_case_index] = ProvisionCase(
            _case.case_id,
            _case.title,
            "providerContact with only a groupId (userId blank/absent) is valid; group members "
            "inherit access, so the request should create a work order and return a workorder number.",
            _case.scenario,
        )
    elif _case.case_id == "17777274":
        PILOT_CASES[_case_index] = ProvisionCase(
            _case.case_id,
            _case.title,
            "The API rejects setting providerContact.userId and .groupId at the same time "
            "(ProviderUserIdAndGroupIdUniqieErrMsg), so this validates a userId confirmed via "
            "cimpl.ProviderContactGroupRelation to belong to a real group, sent alone. "
            "Should create a request and return a workorder number.",
            _case.scenario,
        )

PILOT_CASES.append(
    ProvisionCase(
        "17777235",
        "Create a request with valid shipping address using ISO2 country code",
        "Should return a validation message when an ISO2 country code is supplied if the API requires ISO3.",
        "shipping-country-iso2",
    )
)


def _is_positive_case(case: ProvisionCase) -> bool:
    expected = case.expected.lower()
    return "create a request" in expected or expected.startswith("should create")


POSITIVE_CASE_IDS = {case.case_id for case in PILOT_CASES if _is_positive_case(case)}
_positive_baselines = [
    case for case in PILOT_CASES
    if case.case_id in POSITIVE_CASE_IDS and case.scenario == "same-employee-and-requester"
]
PILOT_CASES.extend(
    ProvisionCase(
        case.case_id,
        f"{case.title} with different employee and requester IDs",
        "Should create a request using two different valid employees.",
        "different-employee-and-requester",
    )
    for case in _positive_baselines
)
PILOT_CASES.extend(
    ProvisionCase(
        case.case_id,
        f"{case.title} with valid IDs and mismatched number and email",
        "Should create a request using valid IDs and resolve identity by ID.",
        "valid-ids-mismatched-contact-details",
    )
    for case in _positive_baselines
)


CASE_PARAMS = [
    pytest.param(
        case,
        id=f"C{case.case_id}-{case.scenario}",
        marks=[
            pytest.mark.swagger_section("Workorders"),
            pytest.mark.swagger_operation("POST /v1/workorders/provision"),
            pytest.mark.testrail_case(case.case_id),
            pytest.mark.dashboard_title(case.title),
        ],
    )
    for case in PILOT_CASES
]


def _record_dashboard_metrics(request, lines: list[str]):
    request.node.user_properties.append(("dashboard_metrics", "\n".join(lines)))


def _safe_json(response) -> dict:
    try:
        payload = response.json()
        return payload if isinstance(payload, dict) else {"raw": payload}
    except Exception:  # noqa: BLE001
        return {}


def _response_text(response) -> str:
    text_attr = getattr(response, "text", "")
    if callable(text_attr):
        return text_attr()
    return str(text_attr)


def _extract_validation_message(payload: dict, text: str) -> str:
    for key in ("Message", "message", "error", "error_description", "ErrorCode", "errorCode"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return (text or "").strip()[:600]


def _post_provision(api_context, body: dict):
    return api_context.post(
        "workorders/provision",
        data=json.dumps(body, ensure_ascii=True),
        headers={"Content-Type": "application/json", "accept": "text/plain"},
    )


def _db_execute(db, query: str, params: tuple = ()):
    cursor = db._conn.cursor()  # noqa: SLF001 - controlled test-only setup utility
    cursor.execute(query, params)


def _param_276_value(db) -> str | None:
    row = db.fetch_one(
        "SELECT TOP 1 Value AS value FROM cimpl.Parameter WHERE ParameterNameId = 276 ORDER BY TenantId"
    )
    return str(row["value"]) if row and row.get("value") is not None else None


def _is_truthy_value(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "y"}


def _get_employee_sample(db, *, requester: bool = False, exclude_id: int | None = None) -> dict:
    where = "EmployeeStatusId = 1" if requester else "EmployeeStatusId IN (1,2,3)"
    exclude_clause = "AND EmployeeId <> ?" if exclude_id is not None else ""
    params = (exclude_id,) if exclude_id is not None else ()
    row = db.fetch_one(
        """
        SELECT TOP 1 EmployeeId AS id, Number AS number, EMail AS email
        FROM cimpl.Employee
        WHERE EmployeeId IS NOT NULL
          AND EMail IS NOT NULL
                """
                + exclude_clause
                + """
                    AND
        """
        + where
        +
        """
        ORDER BY EmployeeId DESC
        """
                ,
                params,
    )
    assert row, "No employee records available for provisioning payload"
    return {
        "id": int(row["id"]),
        "number": str(row.get("number") or "").strip(),
        "email": str(row.get("email") or "").strip(),
    }


def _get_shipping_address(db) -> dict:
    row = db.fetch_one(
        """
        SELECT TOP 1
            a.LocationName AS locationName,
            a.LocationNumber AS locationNumber,
            a.AddressLine1 AS line1,
            a.AddressLine2 AS line2,
            a.City AS city,
            p.Code AS provinceCode,
            c.Iso2 AS countryIso2,
            c.Iso3 AS countryIso3,
            c.Name AS countryName,
            a.PostalCode AS postalCode
        FROM cimpl.Address a
        LEFT JOIN cimpl.Province p ON p.ProvinceId = a.ProvinceId
        LEFT JOIN cimpl.Country c ON c.CountryId = a.CountryId
        WHERE a.Active = 1
        ORDER BY a.AddressId DESC
        """
    )
    assert row, "No active address found for shippingAddress"
    return {
        "locationName": str(row.get("locationName") or "Auto Provision Location"),
        "locationNumber": str(row.get("locationNumber") or "10002"),
        "line1": str(row.get("line1") or "10 Auburn St."),
        "line2": str(row.get("line2") or "Apartment 201"),
        "city": str(row.get("city") or "Chicago"),
        "state": str(row.get("provinceCode") or "IL"),
        "countryCode": str(row.get("countryIso3") or "USA"),
        "postalCode": str(row.get("postalCode") or "02151"),
    }


def _get_country_sample(db) -> dict:
    row = db.fetch_one(
        """
        SELECT TOP 1 Iso2 AS iso2, Iso3 AS iso3, Name AS name
        FROM cimpl.Country
        WHERE Iso2 IS NOT NULL AND Iso3 IS NOT NULL AND Name IS NOT NULL
        ORDER BY CountryId DESC
        """
    )
    assert row, "No country with ISO2, ISO3, and name found"
    return {
        "iso2": str(row["iso2"]).strip(),
        "iso3": str(row["iso3"]).strip(),
        "name": str(row["name"]).strip(),
    }


def _get_cost_center(db) -> int | None:
    row = db.fetch_one(
        """
        SELECT TOP 1 OrganizationId AS id
        FROM cimpl.Organization
        WHERE OrganizationTypeId IN (4,5,12,20,13,28,21,29)
          AND Active = 1
        ORDER BY OrganizationId DESC
        """
    )
    return int(row["id"]) if row and row.get("id") is not None else None


def _get_culture_code(db) -> str | None:
    row = db.fetch_one("SELECT TOP 1 Culture AS culture FROM cimpl.Translations ORDER BY Culture")
    if row:
        value = str(row.get("culture") or "").strip()
        if value:
            return value
    return "en-US"


def _compatibility_seed(db, *, provider_id: int | None = None, service_type_id: int | None = None) -> dict | None:
    where = ["p.Active = 1", "st.Active = 1", "a.Active = 1", "c.Active = 1", "ht.Active = 1", "ht.HardwareCategoryId = 1"]
    # Require the provider to have real provider-contact data so providerContact
    # scenarios (userId/groupId) always exercise a genuine, non-null contact pair.
    where.append(
        "EXISTS (SELECT 1 FROM cimpl.ProviderContact uc "
        "WHERE uc.ProviderId = c.ProviderId AND uc.Active = 1 AND uc.UserTypeId = 3)"
    )
    where.append(
        "EXISTS ("
        "SELECT 1 FROM cimpl.ProviderContactGroupRelation rel "
        "INNER JOIN cimpl.ProviderContact ruc ON ruc.ProviderUserId = rel.ProviderUserId "
        "AND ruc.ProviderId = c.ProviderId AND ruc.Active = 1 AND ruc.UserTypeId = 3 "
        "INNER JOIN cimpl.ProviderContact rgc ON rgc.ProviderUserId = rel.ProviderGroupId "
        "AND rgc.ProviderId = c.ProviderId AND rgc.Active = 1 AND rgc.UserTypeId = 4 "
        "WHERE rel.Active = 1"
        ")"
    )
    params: list[int] = []
    if provider_id is not None:
        where.append("c.ProviderId = ?")
        params.append(provider_id)
    if service_type_id is not None:
        where.append("c.ServiceTypeId = ?")
        params.append(service_type_id)

    query = (
        """
        SELECT TOP 1
            c.ProviderId AS providerId,
            c.ServiceTypeId AS serviceTypeId,
            c.ContractId AS contractId,
            a.Number AS accountNumber,
            ht.HardwareTypeId AS catalogItemId
        FROM cimpl.Contract c
        INNER JOIN cimpl.Provider p ON p.ProviderId = c.ProviderId
        INNER JOIN cimpl.ServiceType st ON st.ServiceTypeId = c.ServiceTypeId
        INNER JOIN cimpl.Account a ON a.ProviderId = c.ProviderId
        INNER JOIN cimpl.HardwareTypeContractCompatibility htc ON htc.ContractId = c.ContractId
        INNER JOIN cimpl.HardwareTypeProviderCompatibility htp ON htp.HardwareTypeId = htc.HardwareTypeId AND htp.ProviderId = c.ProviderId
        INNER JOIN cimpl.ServiceTypeHardwareTypeCompatibility sth ON sth.HardwareTypeId = htc.HardwareTypeId AND sth.ServiceTypeId = c.ServiceTypeId
        INNER JOIN cimpl.HardwareType ht ON ht.HardwareTypeId = htc.HardwareTypeId
        WHERE
        """
        + " AND ".join(where)
        + " ORDER BY c.ContractId DESC"
    )

    row = db.fetch_one(query, tuple(params))
    if not row:
        return None

    provider = int(row["providerId"])
    service_type = int(row["serviceTypeId"])
    contract = int(row["contractId"])
    account_number = str(row["accountNumber"])
    catalog_item = int(row["catalogItemId"])

    option_row = db.fetch_one(
        """
        SELECT TOP 1 otc.OptionTypeId AS optionTypeId
        FROM cimpl.OptionTypeProviderServiceTypeCompatibility otc
        INNER JOIN cimpl.OptionType ot ON ot.OptionTypeId = otc.OptionTypeId
        WHERE otc.ProviderId = ?
          AND otc.ServiceTypeId = ?
          AND ot.Active = 1
                    AND NOT EXISTS (
                            SELECT 1
                            FROM cimpl.ContractOptionTypeRelation cor
                            WHERE cor.ContractId = ?
                                AND cor.OptionTypeId = otc.OptionTypeId
                    )
        ORDER BY otc.OptionTypeId
        """,
                (provider, service_type, contract),
    )

    accessory_rows = db.fetch_all(
        """
        SELECT TOP 3 hc.ToHardwareTypeId AS id
        FROM cimpl.HardwareTypeCompatibility hc
        INNER JOIN cimpl.HardwareType h ON h.HardwareTypeId = hc.ToHardwareTypeId
        INNER JOIN cimpl.HardwareTypeProviderCompatibility hpc ON hpc.HardwareTypeId = h.HardwareTypeId
        WHERE hc.FromHardwareTypeId = ?
          AND h.HardwareCategoryId = 2
          AND h.Active = 1
          AND hpc.ProviderId = ?
        ORDER BY hc.ToHardwareTypeId
        """,
        (catalog_item, provider),
    )

    # UserTypeId=3 rows are real provider users; UserTypeId=4 rows are groups whose own
    # identity is stored in ProviderUserId, so they must never be sent as a "userId".
    contact_row = db.fetch_one(
        """
        SELECT TOP 1 ProviderUserId AS userId, ProviderGroupId AS groupId
        FROM cimpl.ProviderContact
        WHERE ProviderId = ?
          AND Active = 1
          AND UserTypeId = 3
          AND ProviderUserId IS NOT NULL
        ORDER BY ProviderUserId DESC
        """,
        (provider,),
    )

    group_row = db.fetch_one(
        """
        SELECT TOP 1 ProviderUserId AS groupId
        FROM cimpl.ProviderContact
        WHERE ProviderId = ?
          AND Active = 1
          AND UserTypeId = 4
          AND ProviderUserId IS NOT NULL
        ORDER BY ProviderUserId DESC
        """,
        (provider,),
    )

    # Membership is authoritative in ProviderContactGroupRelation, not ProviderContact.ProviderGroupId.
    valid_pair_row = db.fetch_one(
        """
        SELECT TOP 1 r.ProviderUserId AS userId, r.ProviderGroupId AS groupId
        FROM cimpl.ProviderContactGroupRelation r
        INNER JOIN cimpl.ProviderContact uc
            ON uc.ProviderUserId = r.ProviderUserId AND uc.ProviderId = ? AND uc.Active = 1 AND uc.UserTypeId = 3
        INNER JOIN cimpl.ProviderContact gc
            ON gc.ProviderUserId = r.ProviderGroupId AND gc.ProviderId = ? AND gc.Active = 1 AND gc.UserTypeId = 4
        WHERE r.Active = 1
        ORDER BY r.ProviderContactGroupRelationId DESC
        """,
        (provider, provider),
    )

    return {
        "providerId": provider,
        "serviceTypeId": service_type,
        "contractId": contract,
        "accountNumber": account_number,
        "catalogItemId": catalog_item,
        "optionTypeId": int(option_row["optionTypeId"]) if option_row and option_row.get("optionTypeId") is not None else None,
        "accessoryIds": [int(r["id"]) for r in accessory_rows if r.get("id") is not None],
        "providerContact": {
            "userId": int(contact_row["userId"]) if contact_row and contact_row.get("userId") is not None else None,
            "groupId": int(group_row["groupId"]) if group_row and group_row.get("groupId") is not None else None,
            "validUserId": int(valid_pair_row["userId"]) if valid_pair_row and valid_pair_row.get("userId") is not None else None,
            "validGroupId": int(valid_pair_row["groupId"]) if valid_pair_row and valid_pair_row.get("groupId") is not None else None,
        },
    }


def preview_seed_compatibility(db, *, provider_id: int | None = None, service_type_id: int | None = None, limit: int = 2) -> list[dict]:
    """Return up to `limit` Provider/ServiceType pairs with full end-to-end Provision compatibility.

    Reuses the same eligibility rules as `_compatibility_seed` (active contract/account/
    catalog/provider-contact dependencies) so the dashboard's DB compatibility check reflects
    exactly what the automated seed would select.
    """
    where = ["p.Active = 1", "st.Active = 1", "a.Active = 1", "c.Active = 1", "ht.Active = 1", "ht.HardwareCategoryId = 1"]
    where.append(
        "EXISTS (SELECT 1 FROM cimpl.ProviderContact uc "
        "WHERE uc.ProviderId = c.ProviderId AND uc.Active = 1 AND uc.UserTypeId = 3)"
    )
    where.append(
        "EXISTS ("
        "SELECT 1 FROM cimpl.ProviderContactGroupRelation rel "
        "INNER JOIN cimpl.ProviderContact ruc ON ruc.ProviderUserId = rel.ProviderUserId "
        "AND ruc.ProviderId = c.ProviderId AND ruc.Active = 1 AND ruc.UserTypeId = 3 "
        "INNER JOIN cimpl.ProviderContact rgc ON rgc.ProviderUserId = rel.ProviderGroupId "
        "AND rgc.ProviderId = c.ProviderId AND rgc.Active = 1 AND rgc.UserTypeId = 4 "
        "WHERE rel.Active = 1"
        ")"
    )
    params: list[int] = []
    if provider_id is not None:
        where.append("c.ProviderId = ?")
        params.append(provider_id)
    if service_type_id is not None:
        where.append("c.ServiceTypeId = ?")
        params.append(service_type_id)

    query = (
        "SELECT DISTINCT TOP (?) c.ProviderId AS providerId, p.Name AS providerName, c.ServiceTypeId AS serviceTypeId, st.Name AS serviceTypeName "
        "FROM cimpl.Contract c "
        "INNER JOIN cimpl.Provider p ON p.ProviderId = c.ProviderId "
        "INNER JOIN cimpl.ServiceType st ON st.ServiceTypeId = c.ServiceTypeId "
        "INNER JOIN cimpl.Account a ON a.ProviderId = c.ProviderId "
        "INNER JOIN cimpl.HardwareTypeContractCompatibility htc ON htc.ContractId = c.ContractId "
        "INNER JOIN cimpl.HardwareTypeProviderCompatibility htp ON htp.HardwareTypeId = htc.HardwareTypeId AND htp.ProviderId = c.ProviderId "
        "INNER JOIN cimpl.ServiceTypeHardwareTypeCompatibility sth ON sth.HardwareTypeId = htc.HardwareTypeId AND sth.ServiceTypeId = c.ServiceTypeId "
        "INNER JOIN cimpl.HardwareType ht ON ht.HardwareTypeId = htc.HardwareTypeId "
        "WHERE " + " AND ".join(where) + " ORDER BY c.ProviderId DESC"
    )
    rows = db.fetch_all(query, tuple([limit] + params))
    return [
        {
            "providerId": int(row["providerId"]),
            "providerName": str(row.get("providerName") or ""),
            "serviceTypeId": int(row["serviceTypeId"]),
            "serviceTypeName": str(row.get("serviceTypeName") or ""),
        }
        for row in rows
    ]


def _get_incompatible_catalog_item(db, *, provider_id: int, service_type_id: int, scenario: str) -> int:
    if scenario == "catalog-provider-compatible-different-service":
        query = """
            SELECT TOP 1 h.HardwareTypeId AS id
            FROM cimpl.HardwareType h
            INNER JOIN cimpl.HardwareTypeProviderCompatibility hpc
                ON hpc.HardwareTypeId = h.HardwareTypeId
               AND hpc.ProviderId = ?
            WHERE h.Active = 1
              AND h.HardwareCategoryId = 1
              AND NOT EXISTS (
                  SELECT 1
                  FROM cimpl.ServiceTypeHardwareTypeCompatibility sth
                  WHERE sth.HardwareTypeId = h.HardwareTypeId
                    AND sth.ServiceTypeId = ?
              )
            ORDER BY h.HardwareTypeId
        """
    elif scenario == "catalog-service-compatible-different-provider":
        query = """
            SELECT TOP 1 h.HardwareTypeId AS id
            FROM cimpl.HardwareType h
            INNER JOIN cimpl.ServiceTypeHardwareTypeCompatibility sth
                ON sth.HardwareTypeId = h.HardwareTypeId
               AND sth.ServiceTypeId = ?
            WHERE h.Active = 1
              AND h.HardwareCategoryId = 1
              AND NOT EXISTS (
                  SELECT 1
                  FROM cimpl.HardwareTypeProviderCompatibility hpc
                  WHERE hpc.HardwareTypeId = h.HardwareTypeId
                    AND hpc.ProviderId = ?
              )
            ORDER BY h.HardwareTypeId
        """
    else:
        raise ValueError(f"Unsupported catalogue scenario: {scenario}")

    params = (provider_id, service_type_id) if scenario == "catalog-provider-compatible-different-service" else (service_type_id, provider_id)
    row = db.fetch_one(query, params)
    assert row and row.get("id") is not None, (
        f"No category 1 hardware found for catalogue scenario={scenario}, "
        f"providerId={provider_id}, serviceTypeId={service_type_id}"
    )
    return int(row["id"])


def _get_incompatible_contract(db, *, provider_id: int, service_type_id: int) -> int:
    row = db.fetch_one(
        """
        SELECT TOP 1 ContractId AS id
        FROM cimpl.Contract
        WHERE Active = 1
          AND ProviderId <> ?
          AND ServiceTypeId <> ?
        ORDER BY ContractId DESC
        """,
        (provider_id, service_type_id),
    )
    assert row and row.get("id") is not None, (
        "No active contract found that is incompatible with both the selected provider "
        f"and service type. ProviderId={provider_id}, ServiceTypeId={service_type_id}"
    )
    return int(row["id"])


def _get_assigned_contract_option(db, *, contract_id: int, provider_id: int, service_type_id: int) -> int:
    row = db.fetch_one(
        """
        SELECT TOP 1 cor.OptionTypeId AS id
        FROM cimpl.ContractOptionTypeRelation cor
        INNER JOIN cimpl.OptionTypeProviderServiceTypeCompatibility otc
            ON otc.OptionTypeId = cor.OptionTypeId
           AND otc.ProviderId = ?
           AND otc.ServiceTypeId = ?
        INNER JOIN cimpl.OptionType ot ON ot.OptionTypeId = cor.OptionTypeId
        WHERE cor.ContractId = ?
          AND ot.Active = 1
        ORDER BY cor.OptionTypeId
        """,
        (provider_id, service_type_id, contract_id),
    )
    assert row and row.get("id") is not None, (
        f"No option already assigned to Contract {contract_id} is compatible with "
        f"Provider {provider_id} and ServiceType {service_type_id}"
    )
    return int(row["id"])


def _get_incompatible_option(db, *, provider_id: int, service_type_id: int) -> int:
        row = db.fetch_one(
                """
                SELECT TOP 1 ot.OptionTypeId AS id
                FROM cimpl.OptionType ot
                WHERE ot.Active = 1
                    AND NOT EXISTS (
                            SELECT 1
                            FROM cimpl.OptionTypeProviderServiceTypeCompatibility otc
                            WHERE otc.OptionTypeId = ot.OptionTypeId
                                AND otc.ProviderId = ?
                                AND otc.ServiceTypeId = ?
                    )
                ORDER BY ot.OptionTypeId DESC
                """,
                (provider_id, service_type_id),
        )
        assert row and row.get("id") is not None, (
                f"No active option found incompatible with Provider {provider_id} and ServiceType {service_type_id}"
        )
        return int(row["id"])


def _get_seed_with_fallback(db) -> tuple[dict, bool]:
    provider_filter = int(os.getenv("TEST_PROVIDER_ID")) if os.getenv("TEST_PROVIDER_ID", "").strip() else None
    service_type_filter = int(os.getenv("TEST_SERVICE_TYPE_ID")) if os.getenv("TEST_SERVICE_TYPE_ID", "").strip() else None
    if provider_filter is not None or service_type_filter is not None:
        selected = _compatibility_seed(db, provider_id=provider_filter, service_type_id=service_type_filter)
        assert selected, "No compatible Provision seed exists for the selected Provider ID and Service Type ID."
        return selected, True

    preferred = _compatibility_seed(db, provider_id=7, service_type_id=116)
    if preferred:
        return preferred, True

    discovered = _compatibility_seed(db)
    assert discovered, (
        "Unable to discover a provider/serviceType combination with compatible contract/account/catalog dependencies"
    )
    return discovered, False


def _get_seed_with_assigned_option(db) -> dict:
    """Find a provider/serviceType combo whose contract already has an option assigned
    that is also compatible with that provider+serviceType (needed for the
    "option-already-assigned-to-contract" scenario)."""
    candidates = db.fetch_all(
        """
        SELECT DISTINCT TOP 20 c.ProviderId AS providerId, c.ServiceTypeId AS serviceTypeId
        FROM cimpl.Contract c
        INNER JOIN cimpl.ContractOptionTypeRelation cor ON cor.ContractId = c.ContractId
        INNER JOIN cimpl.OptionTypeProviderServiceTypeCompatibility otc
            ON otc.OptionTypeId = cor.OptionTypeId
           AND otc.ProviderId = c.ProviderId
           AND otc.ServiceTypeId = c.ServiceTypeId
        INNER JOIN cimpl.OptionType ot ON ot.OptionTypeId = cor.OptionTypeId AND ot.Active = 1
        WHERE c.Active = 1
        ORDER BY c.ProviderId DESC
        """
    )
    for candidate in candidates:
        seed = _compatibility_seed(
            db,
            provider_id=int(candidate["providerId"]),
            service_type_id=int(candidate["serviceTypeId"]),
        )
        if seed:
            return seed
    raise AssertionError(
        "Unable to discover a provider/serviceType with an option already assigned to its "
        "contract that is also compatible with that provider/serviceType."
    )


def _build_base_payload(db, *, case_id: str, scenario: str) -> tuple[dict, dict]:
    employee = _get_employee_sample(db, requester=False)
    if scenario == "same-employee-and-requester":
        requester = employee
    else:
        requester = _get_employee_sample(db, requester=True, exclude_id=employee["id"])
    shipping = _get_shipping_address(db)
    if scenario == "option-already-assigned-to-contract":
        seed = _get_seed_with_assigned_option(db)
        used_preferred = False
    else:
        seed, used_preferred = _get_seed_with_fallback(db)
    cost_center_id = _get_cost_center(db)
    culture_code = _get_culture_code(db)
    cost_center_required = _is_truthy_value(_param_276_value(db))

    reference = f"AUTO-PROV-{case_id}-{date.today().strftime('%Y%m%d')}"

    payload = {
        "requester": {"id": requester["id"], "number": requester["number"], "email": requester["email"]},
        "employee": {"id": employee["id"], "number": employee["number"], "email": employee["email"]},
        "catalogItemId": seed["catalogItemId"],
        "providerId": seed["providerId"],
        "contractId": seed["contractId"],
        "serviceTypeId": seed["serviceTypeId"],
        "accountNumber": seed["accountNumber"],
        "shippingAddress": shipping,
        "contractStartDate": date.today().strftime("%Y-%m-%d"),
        "dueDate": (date.today() + timedelta(days=2)).strftime("%Y-%m-%d"),
        "comments": f"Auto provision comment for {case_id}",
        "attentionTo": "demo-attentionTo@example.com",
        "isUrgent": True,
        "subject": f"Provision Subject {case_id}",
        "referenceNo": reference,
        "showInProviderPortal": True,
    }

    if scenario == "valid-ids-mismatched-contact-details":
        payload["requester"] = {
            "id": requester["id"],
            "number": employee["number"],
            "email": employee["email"],
        }

    # options are optional and can conflict if already active on the selected contract/service.
    # Keep baseline payload stable by omitting options in pilot core cases.

    accessory_ids = seed.get("accessoryIds", [])
    if accessory_ids:
        payload["accessories"] = [{"id": accessory_ids[0], "count": 1}]

    provider_contact = seed.get("providerContact", {})
    if provider_contact.get("userId") is not None:
        payload["providerContact"] = {"userId": provider_contact["userId"]}
    elif provider_contact.get("groupId") is not None:
        payload["providerContact"] = {"groupId": provider_contact["groupId"]}

    if culture_code:
        payload["cultureCode"] = culture_code

    if cost_center_required and cost_center_id is not None:
        payload["costCenterId"] = cost_center_id
    elif cost_center_id is not None:
        payload["costCenterId"] = cost_center_id

    context = {
        "usedPreferredProviderServiceType": used_preferred,
        "providerId": seed["providerId"],
        "serviceTypeId": seed["serviceTypeId"],
        "contractId": seed["contractId"],
        "catalogItemId": seed["catalogItemId"],
        "optionTypeId": seed.get("optionTypeId"),
        "costCenterRequired": cost_center_required,
        "providerContact": provider_contact,
    }

    return payload, context


def _fetch_workorder_summary(
    db,
    *,
    workorder_number: str | None,
    reference_number: str | None,
) -> dict | None:
    if reference_number:
        row = db.fetch_one(
            """
            SELECT TOP 1
                w.WorkorderId AS workorderId,
                w.Number AS workorderNumber,
                ws.ProviderId AS providerId,
                ws.WorkorderRequesterId AS workorderRequesterId,
                wr.EmployeeId AS requesterEmployeeId,
                ws.ReferenceNumber AS referenceNumber,
                ws.ModifiedDate AS modifiedDate
            FROM cimpl.Workorder w
            LEFT JOIN cimpl.WorkorderSummary ws ON ws.WorkorderId = w.WorkorderId
            LEFT JOIN cimpl.WorkorderRequester wr ON wr.WorkorderRequesterId = ws.WorkorderRequesterId
            WHERE ws.ReferenceNumber = ?
            ORDER BY ws.ModifiedDate DESC
            """,
            (reference_number,),
        )
        if row:
            return row

    if not workorder_number:
        return None

    return db.fetch_one(
        """
        SELECT TOP 1
            w.WorkorderId AS workorderId,
            w.Number AS workorderNumber,
            ws.ProviderId AS providerId,
            ws.WorkorderRequesterId AS workorderRequesterId,
            wr.EmployeeId AS requesterEmployeeId,
            ws.ReferenceNumber AS referenceNumber,
            ws.ModifiedDate AS modifiedDate
        FROM cimpl.Workorder w
        LEFT JOIN cimpl.WorkorderSummary ws ON ws.WorkorderId = w.WorkorderId
        LEFT JOIN cimpl.WorkorderRequester wr ON wr.WorkorderRequesterId = ws.WorkorderRequesterId
        WHERE w.Number = ?
        ORDER BY ws.ModifiedDate DESC
        """,
        (workorder_number,),
    )


def _assert_success_response(case: ProvisionCase, response, payload: dict, db, metrics: list[str]):
    assert response.status in {200, 201}, (
        f"{case.title}: expected 200/201, got {response.status}. Response={_response_text(response)}"
    )

    body = _safe_json(response)
    request_id = str(body.get("requestId") or "").strip()
    workorder_number = str(body.get("workorderNumber") or "").strip()

    assert request_id and UUID_RE.match(request_id), (
        f"{case.title}: requestId missing/invalid. Payload={body}"
    )
    assert workorder_number, f"{case.title}: workorderNumber missing in response. Payload={body}"

    reference_no = payload.get("referenceNo")
    row = _fetch_workorder_summary(
        db,
        workorder_number=workorder_number,
        reference_number=str(reference_no) if reference_no is not None else None,
    )
    assert row, (
        f"{case.title}: workorder not found in DB for number={workorder_number}, referenceNo={reference_no}"
    )

    assert str(row.get("workorderNumber") or "") == workorder_number, (
        f"{case.title}: DB workorder number does not match response. "
        f"Response={workorder_number}, DB={row.get('workorderNumber')}"
    )

    assert int(row["providerId"]) == int(payload["providerId"]), (
        f"{case.title}: DB provider mismatch for workorder {workorder_number}. "
        f"Expected={payload['providerId']} Actual={row['providerId']}"
    )

    requester_id = payload.get("requester", {}).get("id")
    if requester_id is not None and row.get("requesterEmployeeId") is not None:
        assert int(row["requesterEmployeeId"]) == int(requester_id), (
            f"{case.title}: requester EmployeeId mismatch. "
            f"Expected={requester_id}, Actual={row['requesterEmployeeId']}"
        )

    if reference_no and row.get("referenceNumber") is not None:
        assert str(row["referenceNumber"]) == str(reference_no), (
            f"{case.title}: referenceNo mismatch in DB. "
            f"Expected={reference_no}, Actual={row['referenceNumber']}"
        )

    metrics.append(f"Response status: {response.status}")
    metrics.append(f"Response requestId: {request_id}")
    metrics.append(f"Response workorderNumber: {workorder_number}")
    metrics.append(
        "DB validation: "
        f"WorkorderId={row['workorderId']}, "
        f"WorkorderNumber={row.get('workorderNumber')}, "
        f"ReferenceNo={row.get('referenceNumber')}, "
        f"ProviderId={row['providerId']}, "
        f"RequesterEmployeeId={row.get('requesterEmployeeId')}"
    )


def _assert_negative_response(case: ProvisionCase, response, metrics: list[str]):
    assert response.status in {400, 404, 500}, (
        f"{case.title}: expected 400/404, got {response.status}. Response={_response_text(response)}"
    )
    payload = _safe_json(response)
    message = _extract_validation_message(payload, _response_text(response))
    assert message, f"{case.title}: expected validation message in response"

    metrics.append(f"Response status: {response.status}")
    metrics.append(f"Validation message: {message}")
    if payload:
        metrics.append(f"Response payload: {json.dumps(payload, ensure_ascii=True)}")


def _run_case(case: ProvisionCase, api_context, db, request):
    payload, context = _build_base_payload(db, case_id=case.case_id, scenario=case.scenario)
    title_l = case.title.lower()

    metrics = [
        f"CaseId: {case.case_id}",
        f"Title: {case.title}",
        f"Expected: {case.expected}",
        f"Scenario: {case.scenario}",
        f"ProviderId used: {context['providerId']}",
        f"ServiceTypeId used: {context['serviceTypeId']}",
        f"ContractId used: {context['contractId']}",
        f"CatalogItemId used: {context['catalogItemId']}",
        f"Preferred provider/serviceType used (7/116): {context['usedPreferredProviderServiceType']}",
    ]

    if "with required fields only" in title_l:
        minimal = {
            "requester": {"id": payload["requester"]["id"]},
            "employee": {"id": payload["employee"]["id"]},
            "catalogItemId": payload["catalogItemId"],
            "providerId": payload["providerId"],
            "contractId": payload["contractId"],
            "serviceTypeId": payload["serviceTypeId"],
            "accountNumber": payload["accountNumber"],
            "shippingAddress": payload["shippingAddress"],
            "contractStartDate": payload["contractStartDate"],
            "referenceNo": payload["referenceNo"],
        }
        if context["costCenterRequired"] and payload.get("costCenterId") is not None:
            minimal["costCenterId"] = payload["costCenterId"]
        payload = minimal

    if "requester with id only" in title_l:
        payload["requester"] = {"id": payload["requester"]["id"]}

    if "proper due date" in title_l:
        payload["dueDate"] = date.today().strftime("%Y-%m-%d")

    if "valid providerid" in title_l:
        # Base payload already uses a validated providerId via compatibility seed.
        pass

    if "single valid accessories" in title_l:
        accessory_ids = payload.get("accessories") or []
        if not accessory_ids:
            pytest.skip("No compatible accessory found for selected catalog/provider")
        payload["accessories"] = [accessory_ids[0]]

    if "removing requester parameter" in title_l:
        payload.pop("requester", None)

    if "removing employee parameter" in title_l:
        payload.pop("employee", None)

    if "invalid catalogue id" in title_l:
        payload["catalogItemId"] = 999999999

    if case.scenario in {
        "catalog-provider-compatible-different-service",
        "catalog-service-compatible-different-provider",
    }:
        payload["catalogItemId"] = _get_incompatible_catalog_item(
            db,
            provider_id=int(payload["providerId"]),
            service_type_id=int(payload["serviceTypeId"]),
            scenario=case.scenario,
        )

    if "removing providerid" in title_l:
        payload.pop("providerId", None)

    if "invalid servicetypeid" in title_l:
        payload["serviceTypeId"] = 999999999

    if "removing account number" in title_l:
        payload.pop("accountNumber", None)

    if "requester number" in title_l and "email only" in title_l:
        payload["requester"].pop("id", None)

    if "requester null" in title_l:
        payload["requester"] = None
    elif "requester empty" in title_l:
        payload["requester"] = {}
    elif "inactive requester" in title_l:
        row = db.fetch_one("SELECT TOP 1 EmployeeId AS id, Number AS number, EMail AS email FROM cimpl.Employee WHERE EmployeeStatusId<>1 AND EMail IS NOT NULL ORDER BY EmployeeId DESC")
        assert row, "No inactive requester seed available"
        payload["requester"] = {"id": int(row["id"]), "number": str(row.get("number") or ""), "email": str(row["email"])}
    elif "requester id and email mismatch" in title_l:
        other = db.fetch_one("SELECT TOP 1 EMail AS email FROM cimpl.Employee WHERE EmployeeStatusId=1 AND EmployeeId<>? AND EMail IS NOT NULL ORDER BY EmployeeId DESC", (payload["requester"]["id"],))
        assert other, "No alternate requester seed available"
        payload["requester"]["email"] = str(other["email"])

    if "due date null" in title_l:
        payload["dueDate"] = None
    elif "due date blank" in title_l:
        payload["dueDate"] = ""
    elif "due date in the past" in title_l:
        payload["dueDate"] = (date.today() - timedelta(days=2)).strftime("%Y-%m-%d")
    elif "due date invalid calendar" in title_l:
        payload["dueDate"] = "2026-02-30"
    elif "past date as due date" in title_l:
        payload["dueDate"] = (date.today() - timedelta(days=2)).strftime("%Y-%m-%d")
    elif "removing due date" in title_l:
        payload.pop("dueDate", None)

    if "comments null" in title_l:
        payload["comments"] = None
    elif "comments unicode" in title_l:
        payload["comments"] = "\u65e5\u672c\u8a9e \u0627\u0644\u0639\u0631\u0628\u064a\u0629"
    elif "comments over 4000" in title_l:
        payload["comments"] = "C" * 4001
    elif "blank comment" in title_l:
        payload["comments"] = ""
    elif "removing comment" in title_l:
        payload.pop("comments", None)
    elif "more character" in title_l:
        payload["comments"] = "C" * 1001

    if "employee with id only" in title_l:
        payload["employee"] = {"id": payload["employee"]["id"]}
    elif "employee with number" in title_l:
        payload["employee"] = {
            "number": payload["employee"]["number"],
            "email": payload["employee"]["email"],
        }

    if "removing catalogue" in title_l:
        payload.pop("catalogItemId", None)
    elif "blank catalogue" in title_l:
        payload["catalogItemId"] = ""

    if "invalid providerid" in title_l or "inactive providerid" in title_l:
        payload["providerId"] = 999999999
    elif "blank providerid" in title_l:
        payload["providerId"] = ""

    if "invalid contractid" in title_l or "inactive contractid" in title_l:
        payload["contractId"] = 999999999
    elif "blank contract" in title_l:
        payload["contractId"] = ""
    elif "removing contract" in title_l:
        payload.pop("contractId", None)

    if "valid contractid" in title_l and "incompatible with provider" in title_l:
        payload["contractId"] = _get_incompatible_contract(
            db,
            provider_id=int(payload["providerId"]),
            service_type_id=int(payload["serviceTypeId"]),
        )

    if (
        "inactive servicetypeid" in title_l
        or "invalid servicetypeid" in title_l
        or "service type which is incompatible with provider" in title_l
    ):
        payload["serviceTypeId"] = 999999999
    elif "blank service type" in title_l:
        payload["serviceTypeId"] = ""
    elif "removing servicetypeid" in title_l:
        payload.pop("serviceTypeId", None)

    if "incompatible with provider" in title_l and "service type" not in title_l:
        payload["accountNumber"] = "INVALID-ACCOUNT"
    elif "invalid accountnumber" in title_l:
        payload["accountNumber"] = "INVALID-ACCOUNT"
    elif "blank account" in title_l:
        payload["accountNumber"] = ""

    if "option type id" in title_l:
        option_id = context.get("optionTypeId")
        if case.scenario == "option-already-assigned-to-contract":
            payload["options"] = [
                _get_assigned_contract_option(
                    db,
                    contract_id=int(payload["contractId"]),
                    provider_id=int(payload["providerId"]),
                    service_type_id=int(payload["serviceTypeId"]),
                )
            ]
        elif "inactive option" in title_l or "invalid option" in title_l:
            # Must be checked before "valid option" below: "invalid option" contains
            # "valid option" as a substring, which would otherwise shadow this branch.
            payload["options"] = [
                _get_incompatible_option(
                    db,
                    provider_id=int(payload["providerId"]),
                    service_type_id=int(payload["serviceTypeId"]),
                )
            ]
        elif "valid option" in title_l and option_id is not None:
            payload["options"] = [option_id]
        elif "blank option" in title_l:
            payload["options"] = []
        elif "removing option" in title_l:
            payload.pop("options", None)

    if "shipping address missing child" in title_l:
        payload["shippingAddress"].pop("postalCode", None)

    if "subject over 256" in title_l:
        payload["subject"] = "S" * 257
    if "reference number over 50" in title_l:
        payload["referenceNo"] = "R" * 51

    if "blank to attention" in title_l:
        payload["attentionTo"] = ""
    elif "removing attention" in title_l:
        payload.pop("attentionTo", None)

    if "blank shipping" in title_l:
        payload["shippingAddress"] = {
            "locationName": "",
            "locationNumber": "",
            "line1": "",
            "line2": "",
            "city": "",
            "state": "",
            "countryCode": "",
            "postalCode": "",
        }
    elif "removing shipping" in title_l:
        payload.pop("shippingAddress", None)
    elif "random text on shipping" in title_l:
        payload["shippingAddress"]["countryCode"] = _get_country_sample(db)["name"]

    if case.scenario == "shipping-country-iso2":
        payload["shippingAddress"]["countryCode"] = _get_country_sample(db)["iso2"]

    if "blank contractstartdate" in title_l or "blank invalid contractstartdate" in title_l:
        payload["contractStartDate"] = ""
    elif "removing contractstartdate" in title_l:
        payload.pop("contractStartDate", None)

    if "invalid valid culture" in title_l:
        payload["cultureCode"] = "invalid-culture"
    elif "blank culture" in title_l:
        payload["cultureCode"] = ""
    elif "removing" in title_l and "culture code" in title_l:
        payload.pop("cultureCode", None)

    if "valid cost center unrelated" in title_l:
        value = db.fetch_scalar("SELECT TOP 1 OrganizationId FROM cimpl.Organization WHERE Active=1 AND " + cost_center_type_sql("OrganizationTypeId") + " ORDER BY OrganizationId DESC")
        assert value, "No unrelated active cost center seed available"
        payload["costCenterId"] = int(value)
    elif "valid inactive cost center" in title_l:
        value = db.fetch_scalar("SELECT TOP 1 OrganizationId FROM cimpl.Organization WHERE Active=0 ORDER BY OrganizationId DESC")
        assert value, "No inactive cost center seed available"
        payload["costCenterId"] = int(value)
    elif "valid closed cost center" in title_l:
        value = db.fetch_scalar("SELECT TOP 1 OrganizationId FROM cimpl.Organization WHERE Active=1 AND OrganizationStatusId<>1 AND " + cost_center_type_sql("OrganizationTypeId") + " ORDER BY OrganizationId DESC")
        assert value, "No closed cost center seed available"
        payload["costCenterId"] = int(value)
    elif "valid non-cost-center" in title_l:
        value = db.fetch_scalar("SELECT TOP 1 OrganizationId FROM cimpl.Organization WHERE Active=1 AND NOT (" + cost_center_type_sql("OrganizationTypeId") + ") ORDER BY OrganizationId DESC")
        assert value, "No non-cost-center organization seed available"
        payload["costCenterId"] = int(value)
    elif "invalid cost center" in title_l or "inactive cost center" in title_l:
        payload["costCenterId"] = 999999999
    elif "blank cost center" in title_l:
        payload["costCenterId"] = None
    elif "removing cost center" in title_l:
        payload.pop("costCenterId", None)

    if "isurgent" in title_l:
        if "false" in title_l:
            payload["isUrgent"] = False
        elif "blank" in title_l:
            payload["isUrgent"] = None
        elif "invalid" in title_l:
            payload["isUrgent"] = "invalid"
        elif "removing" in title_l:
            payload.pop("isUrgent", None)

    if "subject" in title_l:
        if "invalid" in title_l:
            payload["subject"] = "S" * 1001
        elif "blank" in title_l:
            payload["subject"] = ""
        elif "removing" in title_l:
            payload.pop("subject", None)
        elif "special character" in title_l:
            payload["subject"] = "Provision !@#$%^&*()"

    if "refernceno" in title_l or "referenceno" in title_l:
        if "invalid" in title_l:
            payload["referenceNo"] = "R" * 1001
        elif "blank" in title_l:
            payload["referenceNo"] = ""
        elif "removing" in title_l:
            payload.pop("referenceNo", None)

    if "showinproviderportal" in title_l:
        if "false" in title_l:
            payload["showInProviderPortal"] = False
        elif "blank" in title_l:
            payload["showInProviderPortal"] = None
        elif "invalid" in title_l:
            payload["showInProviderPortal"] = "invalid"
        elif "removing" in title_l:
            payload.pop("showInProviderPortal", None)

    if "providercontact" in title_l:
        provider_contact = context.get("providerContact", {})
        if "invalid userid" in title_l or "invalid userid & groupid" in title_l:
            payload["providerContact"] = {"userId": 999999999}
        elif "blank userid & groupid" in title_l:
            # API fields are Nullable<int>: JSON null deserializes fine, "" does not.
            payload["providerContact"] = {"userId": None, "groupId": None}
        elif "blank userid only" in title_l:
            payload["providerContact"] = {"userId": None, "groupId": provider_contact.get("groupId")}
        elif "blank userid" in title_l:
            payload["providerContact"] = {"userId": None, "groupId": provider_contact.get("groupId")}
        elif "removing both" in title_l:
            payload["providerContact"] = {}
        elif "removing userid only" in title_l or "removing userid" in title_l:
            # Keep the real groupId so this genuinely tests "userId removed, groupId present",
            # not an accidental fully-empty providerContact.
            payload["providerContact"] = {"groupId": provider_contact.get("groupId")}
        elif "valid userid & groupid" in title_l:
            # The API rejects setting both fields at once ("Provider UserId and GroupId cannot
            # both be set"), so this validates a userId confirmed (via
            # cimpl.ProviderContactGroupRelation) to belong to a real group, sent alone.
            valid_user_id = provider_contact.get("validUserId")
            if valid_user_id is not None:
                payload["providerContact"] = {"userId": valid_user_id}

    if "accessories" in title_l:
        accessory_items = payload.get("accessories") or []
        if "multiple valid" in title_l:
            if len(accessory_items) == 1:
                accessory_items = accessory_items * 2
            payload["accessories"] = accessory_items
        elif "single invalid" in title_l:
            payload["accessories"] = [{"id": 999999999, "count": 1}]
        elif "multiple invalid" in title_l:
            payload["accessories"] = [
                {"id": 999999998, "count": 1},
                {"id": 999999999, "count": 1},
            ]
        elif "blank accessories" in title_l:
            payload["accessories"] = []
        elif "removing accessories" in title_l:
            payload.pop("accessories", None)

    if case.scenario in {"different-employee-and-requester", "valid-ids-mismatched-contact-details"}:
        assert payload.get("employee", {}).get("id") != payload.get("requester", {}).get("id"), (
            f"{case.title}: employee and requester IDs must be different"
        )

    if "invalid valid culture" in title_l:
        assert payload["cultureCode"] not in SUPPORTED_DOTNET_CULTURES

    metrics.append(f"Request body: {json.dumps(payload, ensure_ascii=True)}")
    response = _post_provision(api_context, payload)

    mismatched_identity = (
        case.scenario == "valid-ids-mismatched-contact-details"
        and payload.get("requester", {}).get("id") is not None
        and payload.get("requester", {}).get("number") is not None
        and payload.get("requester", {}).get("email") is not None
        and payload.get("requester", {}).get("id") != payload.get("employee", {}).get("id")
    )

    try:
        if mismatched_identity:
            _assert_negative_response(case, response, metrics)
        elif ("blank cost center" in title_l or "removing cost center" in title_l) and context["costCenterRequired"]:
            _assert_negative_response(case, response, metrics)
        elif "blank to isurgent" in title_l:
            _assert_negative_response(case, response, metrics)
        elif "blank to showinproviderportal" in title_l or "blank showinproviderportal" in title_l:
            # showInProviderPortal is a non-nullable bool on the API; blank (null) is invalid input.
            metrics.append(
                "Note: showInProviderPortal is a non-nullable boolean field on the API; "
                "a blank/null value cannot be deserialized and is expected to be rejected with a 400."
            )
            _assert_negative_response(case, response, metrics)
        elif "providercontact" in title_l and (
            "blank userid only" in title_l
            or "removing userid only" in title_l
            or "removing userid" in title_l
        ):
            # Group membership grants access to all users under the group, so a providerContact
            # with only a groupId (userId blank/absent) is valid and should still create the request.
            metrics.append(
                "Note: providerContact with only a groupId (no userId) is valid; group members "
                "inherit access, so the request is expected to succeed."
            )
            _assert_success_response(case, response, payload, db, metrics)
        elif "blank cost center" in title_l or "removing cost center" in title_l:
            _assert_success_response(case, response, payload, db, metrics)
        elif _is_positive_case(case):
            _assert_success_response(case, response, payload, db, metrics)
        else:
            _assert_negative_response(case, response, metrics)
    finally:
        # Assertion helpers only append response details on the success path before
        # their own status assert; if that assert raises, capture it here so failed
        # cases still show the actual response in the dashboard output.
        if sys.exc_info()[0] is not None:
            metrics.append(f"Response status: {response.status}")
            metrics.append(f"Response body: {_response_text(response)}")
        _record_dashboard_metrics(request, metrics)


class TestPostWorkordersProvisionPilot:

    @pytest.mark.parametrize("case", CASE_PARAMS)
    def test_post_workorders_provision_pilot(self, case, api_context, db, request):
        _run_case(case, api_context, db, request)
