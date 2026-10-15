"""Sync pilot Workorder Provision case Steps and Expected Result in TestRail."""

from __future__ import annotations

import json
from pathlib import Path

from dotenv import load_dotenv

from tests.workorders.test_post_workorders_provision import PILOT_CASES, POSITIVE_CASE_IDS as POSITIVE_CASE_IDS_TEXT
from utils.testrail_client import TestRailClient, TestRailError


PILOT_CASE_IDS = {int(case.case_id) for case in PILOT_CASES}
POSITIVE_CASE_IDS = {int(case_id) for case_id in POSITIVE_CASE_IDS_TEXT}

DASHBOARD_JSON = Path(__file__).resolve().parents[1] / "reports" / "test_dashboard.json"


def _load_dashboard_results() -> dict[int, dict]:
    payload = json.loads(DASHBOARD_JSON.read_text(encoding="utf-8"))
    out: dict[int, dict] = {}
    for section in payload.get("sections", []):
        for result in section.get("results", []):
            case_raw = str(result.get("testrail_case_id", "")).strip()
            if not case_raw.isdigit():
                continue
            case_id = int(case_raw)
            if case_id in PILOT_CASE_IDS:
                current = out.setdefault(case_id, {"title": "", "details": ""})
                title = str(result.get("title", "")).strip()
                details = str(result.get("details", "")).strip()
                current["title"] = " | ".join(
                    value for value in (current["title"], title) if value
                )
                current["details"] = "\n\n--- Additional dataset ---\n\n".join(
                    value for value in (current["details"], details) if value
                )
    return out


def _extract_line(details: str, prefix: str) -> str:
    for line in details.splitlines():
        if line.lower().startswith(prefix.lower()):
            return line.split(":", 1)[1].strip() if ":" in line else ""
    return ""


def _build_steps(case_id: int, title: str) -> str:
    t = title.lower()
    explicit_steps = {
        17777248: (
            "Query cimpl.Parameter for ParameterNameId 276. Call POST /v1/workorders/provision with costCenterId set to null. "
            "Execute once with parameter 276=True and once with parameter 276=False."
        ),
        17777252: "Call POST /v1/workorders/provision with isUrgent present as a blank/null Boolean value and verify the response and database.",
        17777249: (
            "Query cimpl.Parameter for ParameterNameId 276. Call POST /v1/workorders/provision without costCenterId. "
            "Execute once with parameter 276=True and once with parameter 276=False."
        ),
        17777189: (
            "Call POST /v1/workorders/provision with dueDate earlier than the creation date. "
            "Use the otherwise valid request body and verify the response and database."
        ),
        17777190: (
            "Call POST /v1/workorders/provision with dueDate equal to the creation date. "
            "A future dueDate is also valid. Verify the work order is created."
        ),
        17777210: (
            "Select an active ContractId from cimpl.Contract that is incompatible with the selected ProviderId and ServiceTypeId. "
            "Call POST /v1/workorders/provision and verify no work order is created."
        ),
        17777225: (
            "Select an active option compatible with ProviderId and ServiceTypeId but absent from cimpl.ContractOptionTypeRelation for the selected ContractId. "
            "Call POST /v1/workorders/provision."
        ),
        17777233: (
            "Call POST /v1/workorders/provision with shippingAddress present and every child field blank: "
            "locationName, locationNumber, line1, line2, city, state, countryCode, and postalCode."
        ),
        17777234: "Call POST /v1/workorders/provision after removing the mandatory shippingAddress object.",
        17777235: (
            "Call POST /v1/workorders/provision with a valid address, state from cimpl.Province.Code, "
            "and countryCode from cimpl.Country.Iso3. Also validate the country-code format using the database country values."
        ),
        17777236: (
            "Call POST /v1/workorders/provision with a valid address but pass the country name instead of cimpl.Country.Iso3 countryCode."
        ),
        17783584: (
            "Select an option already assigned to the selected ContractId in cimpl.ContractOptionTypeRelation. "
            "Call POST /v1/workorders/provision and verify no work order is created."
        ),
    }
    if case_id in explicit_steps:
        return explicit_steps[case_id]
    steps = []
    if "different employee and requester" in t:
        steps.append("Include a second valid employee record so employee.id and requester.id are different.")
    if "mismatched number and email" in t:
        steps.append("Use valid employee and requester IDs but provide mismatched number and email values; verify the persisted identity follows the IDs.")
    if "provider but different service" in t:
        steps.append("Use active HardwareType hardwareCategoryId=1 compatible with the selected provider but not the selected service type.")
    if "service but different provider" in t:
        steps.append("Use active HardwareType hardwareCategoryId=1 compatible with the selected service type but not the selected provider.")
    if steps:
        steps.insert(0, "Call POST /v1/workorders/provision with the valid baseline request body.")
        return "\n".join(steps)
    if "invalid catalogue" in t:
        return "Call POST /v1/workorders/provision with invalid catalogItemId in request body."
    if "removing requester" in t:
        return "Call POST /v1/workorders/provision without requester in request body."
    if "removing employee" in t:
        return "Call POST /v1/workorders/provision without employee in request body."
    if "removing providerid" in t:
        return "Call POST /v1/workorders/provision without providerId in request body."
    if "invalid servicetypeid" in t:
        return "Call POST /v1/workorders/provision with invalid serviceTypeId in request body."
    if "removing account number" in t:
        return "Call POST /v1/workorders/provision without accountNumber in request body."
    if "required fields only" in t:
        return "Call POST /v1/workorders/provision with required fields only."
    if "requester with id only" in t:
        return "Call POST /v1/workorders/provision with requester containing id only."
    if "proper due date" in t:
        return "Call POST /v1/workorders/provision with valid dueDate in YYYY-MM-DD format."
    if "valid providerid" in t:
        return "Call POST /v1/workorders/provision with compatible valid providerId."
    if "single valid accessories" in t:
        return "Call POST /v1/workorders/provision with one compatible accessories item."
    return "Call POST /v1/workorders/provision with full valid request body."


def _build_expected(case_id: int, details: str) -> str:
    response_status = _extract_line(details, "Response status") or "n/a"
    validation_message = _extract_line(details, "Validation message") or "n/a"

    explicit_expected = {
        17777189: "Response should be 400/404 with a validation message when dueDate is earlier than the creation date. No work order should be created.",
        17777190: "Response should be 200/201 and include requestId and workorderNumber when dueDate equals the creation date or is in the future. The work order should be persisted.",
        17777210: "Response should be 400/404 with a validation message because the active contract is incompatible with the selected provider and/or service type. No work order should be created.",
        17777225: "Response should be 200/201 and include requestId and workorderNumber when the option is compatible and not already present in cimpl.ContractOptionTypeRelation for the contract.",
        17777233: "Response should be 400/404 with a validation message because shippingAddress is mandatory and all supplied child values are blank. No work order should be created.",
        17777234: "Response should be 400/404 with a validation message because shippingAddress is mandatory and was removed. No work order should be created.",
        17777235: "Response should be 200/201 when countryCode is a valid ISO3 value from cimpl.Country. The work order should be persisted.",
        17777236: "Response should be 400/404 with a validation message when a country name is supplied instead of the ISO3 countryCode. No work order should be created.",
        17783584: "Response should be 400/404 with a validation message because the option is already assigned to the contract. No work order should be created.",
        17777248: "When cimpl.Parameter.ParameterNameId 276 is True, response should be 400/404 with a validation message and no work order should be created. When it is False, a blank/null costCenterId should be accepted and the work order should be created.",
        17777249: "When cimpl.Parameter.ParameterNameId 276 is True, response should be 400/404 with a validation message and no work order should be created. When it is False, omitting costCenterId should be accepted and the work order should be created.",
        17777252: "Response should be 400 with a validation message because isUrgent is a Boolean field and blank/null cannot be converted to System.Boolean. No work order should be created.",
    }
    if case_id in explicit_expected:
        return explicit_expected[case_id]

    if case_id in POSITIVE_CASE_IDS:
        return (
            f"Response code should be 200/201 (latest observed: {response_status}). "
            "Response should include requestId and workorderNumber. "
            "Expected DB output: workorderNumber exists in cimpl.Workorder and provider/requester linkage is valid in related workorder tables."
        )

    return (
        f"Response code should be 400/404 for validation failures (latest observed: {response_status}). "
        f"Expected validation message: {validation_message}. "
        "No workorder should be created for invalid payload."
    )


def _target_expected_field(case_payload: dict) -> str:
    for key in ("custom_expected", "custom_expectedresult", "custom_expected_result"):
        if key in case_payload:
            return key
    raise RuntimeError("No expected-result field found on TestRail case payload")


def _target_steps_field(case_payload: dict) -> str:
    for key in ("custom_steps", "custom_steps_separated"):
        if key in case_payload:
            return key
    raise RuntimeError("No steps field found on TestRail case payload")


def main():
    case_results = _load_dashboard_results()
    missing = sorted(PILOT_CASE_IDS - set(case_results.keys()))
    if missing:
        raise RuntimeError(
            f"Missing pilot case output in dashboard JSON for case IDs: {missing}. "
            "Run pilot pytest module first."
        )

    client = TestRailClient.from_env()
    updated = 0

    for case_id in sorted(PILOT_CASE_IDS):
        result = case_results[case_id]
        title = str(result.get("title", "")).strip()
        details = str(result.get("details", "")).strip()

        case_payload = client.get_case(case_id)
        expected_field = _target_expected_field(case_payload)
        steps_field = _target_steps_field(case_payload)

        new_expected = _build_expected(case_id, details)
        new_steps = _build_steps(case_id, title)

        current_expected = str(case_payload.get(expected_field, "") or "").strip()
        current_steps = str(case_payload.get(steps_field, "") or "").strip()

        payload = {}
        if current_expected != new_expected:
            payload[expected_field] = new_expected
        if current_steps != new_steps:
            payload[steps_field] = new_steps

        if not payload:
            print(f"C{case_id}: unchanged")
            continue

        client.update_case(case_id, payload)
        updated += 1
        print(f"C{case_id}: updated {', '.join(payload.keys())}")

    print(f"Done. Updated {updated} pilot workorder provision cases.")


if __name__ == "__main__":
    try:
        load_dotenv()
        main()
    except TestRailError as exc:
        raise SystemExit(f"TestRail error: {exc}") from exc
