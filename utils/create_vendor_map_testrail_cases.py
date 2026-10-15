"""Create and map the Vendor Map API automation cases in TestRail."""

from __future__ import annotations

import os

from dotenv import load_dotenv

from tests.vendor_map.test_post_vendor_map_approve_devices import CASES as APPROVE_CASES
from tests.vendor_map.test_post_vendor_map_approve_accessories import CASES as APPROVE_ACCESSORY_CASES
from tests.vendor_map.test_post_vendor_map_decline_devices import CASES as DECLINE_CASES
from tests.vendor_map.test_post_vendor_map_decline_accessories import CASES as DECLINE_ACCESSORY_CASES
from tests.vendor_map.test_post_vendor_map_devices import CASES as FETCH_CASES
from tests.vendor_map.test_post_vendor_map_accessories import CASES as FETCH_ACCESSORY_CASES
from utils.testrail_client import TestRailClient


SECTIONS = {
    "VMP01": 890587,
    "VMP02": 890588,
    "VMP03": 890589,
    "VMP04": 890591,
    "VMP05": 890592,
    "VMP18": 890593,
}

CASE_GROUPS = {
    "VMP01": FETCH_CASES,
    "VMP02": APPROVE_CASES,
    "VMP03": DECLINE_CASES,
    "VMP04": FETCH_ACCESSORY_CASES,
    "VMP05": APPROVE_ACCESSORY_CASES,
    "VMP18": DECLINE_ACCESSORY_CASES,
}


def _payload(case, operation: str) -> dict:
    return {
        "title": f"{case.category} - {case.title}",
        "template_id": 1,
        "type_id": 1,
        "priority_id": 4,
        "refs": operation,
        "labels": ["API Automation", "Vendor Map", case.category],
        "custom_preconds": f"Vendor Map API is available and the database contains suitable cimpl.VendorEquipmentMap data.\nEndpoint: {operation}",
        "custom_steps": case.steps,
        "custom_expected": case.expected,
    }


def main():
    load_dotenv()
    client = TestRailClient.from_env()
    mapping: dict[str, dict[str, int]] = {}

    for prefix, section_id in SECTIONS.items():
        existing = {str(item.get("title", "")).strip(): int(item["id"]) for item in client.get_cases(section_id=section_id) if item.get("id") is not None}
        operation = {
            "VMP01": "POST /v1/vendormap/devices",
            "VMP02": "POST /v1/vendormap/ApproveDevices",
            "VMP03": "POST /v1/vendormap/DeclineDevices",
            "VMP04": "POST /v1/vendormap/accessories",
            "VMP05": "POST /v1/vendormap/approveAccessories",
            "VMP18": "POST /v1/vendormap/declineAccessories",
        }[prefix]
        mapping[prefix] = {}
        for case in CASE_GROUPS[prefix]:
            title = f"{case.category} - {case.title}"
            case_id = existing.get(title)
            if case_id is None:
                created = client.add_case(section_id, _payload(case, operation))
                case_id = int(created["id"])
                print(f"{prefix} C{case.key}: created C{case_id} - {title}")
            else:
                desired = _payload(case, operation)
                current = client.get_case(case_id)
                updates = {
                    field: value
                    for field, value in desired.items()
                    if field in {"custom_preconds", "custom_steps", "custom_expected", "refs"}
                    and str(current.get(field, "") or "") != str(value)
                }
                if updates:
                    client.update_case(case_id, updates)
                    print(f"{prefix} C{case.key}: existing C{case_id}, updated {', '.join(updates)} - {title}")
                else:
                    print(f"{prefix} C{case.key}: existing C{case_id} - {title}")
            mapping[prefix][case.key] = case_id

    print("\nCASE_MAPPING")
    for prefix, cases in mapping.items():
        print(f"{prefix}=" + ",".join(f"{key}:{value}" for key, value in cases.items()))


if __name__ == "__main__":
    main()
