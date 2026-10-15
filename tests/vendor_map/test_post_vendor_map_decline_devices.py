from __future__ import annotations

import time

import pytest

from tests.vendor_map._vendor_map_common import (
    VendorMapCase,
    assert_error,
    assert_generic_success,
    case_param,
    db_devices,
    elapsed_ms,
    payload_for_device,
    prepare_device_candidate,
    post,
    record_metrics,
    safe_json,
    unauthenticated_request,
)


API_PATH = "vendormap/declineDevices"
PREFIX = "VMP03"

CASES = [
    VendorMapCase("01", "Functional", "Decline one device", "POST a valid vendorId and one map ID", "HTTP 200/201 and the device is Declined/Not Active"),
    VendorMapCase("02", "Functional", "Decline multiple devices", "POST one vendorId and multiple map IDs from the same vendor", "All selected devices are Declined/Not Active"),
    VendorMapCase("03", "Validation", "Reject missing vendorId", "POST only vendorEquipmentMapId", "HTTP 400 or equivalent validation error"),
    VendorMapCase("04", "Validation", "Reject missing map IDs", "POST vendorId with an empty ID list", "HTTP 400 or equivalent validation error"),
    VendorMapCase("05", "Compatibility", "Persist declined device state", "Read VendorEquipmentMap and HardwareType before and after decline", "Map status changes and HardwareType.Active becomes false"),
    VendorMapCase("06", "Security", "Reject unauthenticated decline", "POST without Authorization", "HTTP 401 or 403"),
    VendorMapCase("07", "Security", "Reject cross-vendor device IDs", "Use a map ID belonging to a different vendor", "Request is rejected and the other vendor's row is unchanged"),
    VendorMapCase("08", "Non-functional", "Return generic success response promptly", "Decline one valid device and measure duration", "HTTP 200/201 with a generic response within 60 seconds"),
]
CASE_PARAMS = [case_param(case, prefix=PREFIX, operation="POST /v1/vendormap/DeclineDevices") for case in CASES]


def _candidates(db) -> list[dict]:
    return db_devices(db, status_id=2, limit=20)


def _run(case: VendorMapCase, api_context, db, request):
    title = case.title.lower()
    is_positive = not any(keyword in title for keyword in ("missing vendorid", "missing map ids", "unauthenticated", "cross-vendor"))
    if is_positive:
        prepare_device_candidate(db)
    candidates = _candidates(db)
    assert candidates, "No VendorEquipmentMap rows available for decline tests"
    device = candidates[0]

    if "missing vendorid" in title:
        response = post(api_context, API_PATH, {"vendorEquipmentMapIds": [int(device["vendorEquipmentMapId"])]})
        assert_error(response, case)
    elif "missing map ids" in title:
        response = post(api_context, API_PATH, {"vendorId": int(device["vendorId"]), "vendorEquipmentMapIds": []})
        assert_error(response, case)
    elif "unauthenticated" in title:
        response = unauthenticated_request("POST", f"v1/{API_PATH}", payload_for_device(device))
        from tests.vendor_map._vendor_map_common import assert_unauthenticated_rejected
        assert_unauthenticated_rejected(response)
        record_metrics(request, case, [f"Response status: {response.status_code}"])
        return
    elif "cross-vendor" in title:
        other = next((row for row in candidates if int(row["vendorId"]) != int(device["vendorId"])), None)
        if not other:
            pytest.skip("No second vendor exists for cross-vendor validation")
        response = post(api_context, API_PATH, payload_for_device(device, vendorEquipmentMapIds=[int(other["vendorEquipmentMapId"])]))
        assert_error(response, case)
        after = db.fetch_one("SELECT Status AS statusId FROM cimpl.VendorEquipmentMap WHERE VendorEquipmentMapId = ?", (int(other["vendorEquipmentMapId"]),))
        assert after and int(after["statusId"]) == int(other["statusId"])
    else:
        selected = candidates[:2] if "multiple" in title and len(candidates) > 1 else [device]
        if len(selected) > 1 and len({int(row["vendorId"]) for row in selected}) != 1:
            selected = [device]
        before = {int(row["vendorEquipmentMapId"]): row for row in selected}
        for row in before.values():
            assert str(row.get("hardwareTypeName") or "").strip(), (
                f"Missing HardwareType.Name for VendorEquipmentMapId={row['vendorEquipmentMapId']}"
            )
        start = time.perf_counter()
        response = post(api_context, API_PATH, payload_for_device(selected[0], vendorEquipmentMapIds=list(before)))
        duration = elapsed_ms(start)
        assert_generic_success(response, case)
        for map_id, row in before.items():
            after = db.fetch_one("SELECT Status AS statusId, vem.HardwareTypeId AS hardwareTypeId, ht.Name AS hardwareTypeName, ht.Active AS hardwareTypeActive FROM cimpl.VendorEquipmentMap vem LEFT JOIN cimpl.HardwareType ht ON ht.HardwareTypeId = vem.HardwareTypeId WHERE vem.VendorEquipmentMapId = ?", (map_id,))
            assert after, f"Declined row {map_id} was not found"
            assert int(after["hardwareTypeId"]) == int(row["hardwareTypeId"])
            assert str(after["hardwareTypeName"]).strip() == str(row["hardwareTypeName"]).strip(), (
                f"HardwareType.Name changed or mismatched for VendorEquipmentMapId={map_id}: "
                f"before={row['hardwareTypeName']!r}, after={after['hardwareTypeName']!r}"
            )
            assert int(after["statusId"]) != int(row["statusId"]) or bool(after.get("hardwareTypeActive")) is False
            if "persist declined" in title:
                assert bool(after.get("hardwareTypeActive")) is False
        if "promptly" in title:
            assert duration <= 60000
        record_metrics(request, case, [f"Response status: {response.status}", f"Response duration: {duration} ms", f"Response payload: {safe_json(response)}"])
        return

    record_metrics(request, case, [f"Response status: {response.status}", f"Response payload: {safe_json(response)}"])


@pytest.mark.parametrize("case", CASE_PARAMS)
def test_decline_devices(case, api_context, db, request):
    _run(case, api_context, db, request)
