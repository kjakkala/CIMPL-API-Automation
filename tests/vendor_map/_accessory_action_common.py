from __future__ import annotations

import time

import pytest

from tests.vendor_map._vendor_map_common import (
    VendorMapCase, assert_error, assert_generic_success, assert_unauthenticated_rejected, db_accessories,
    elapsed_ms, first_accessory, payload_for_accessory, post, record_metrics,
    safe_json, unauthenticated_request,
)


def run_accessory_action(case: VendorMapCase, api_context, db, request, *, path: str, map_key: str, active_expected: bool):
    title = case.title.lower()
    candidates = db_accessories(db, limit=20)
    assert candidates, "No category-2 VendorAccessoryMap rows available"
    accessory = candidates[0]
    if "missing vendorid" in title:
        response = post(api_context, path, {map_key: [int(accessory["vendorAccessoryMapId"])]})
        assert_error(response, case)
        record_metrics(request, case, [f"Response status: {response.status}"])
        return
    if "missing map ids" in title:
        response = post(api_context, path, {"vendorId": int(accessory["vendorId"]), map_key: []})
        assert_error(response, case)
        record_metrics(request, case, [f"Response status: {response.status}"])
        return
    if "unauthenticated" in title:
        response = unauthenticated_request("POST", f"v1/{path}", payload_for_accessory(accessory, map_key=map_key))
        assert_unauthenticated_rejected(response)
        record_metrics(request, case, [f"Response status: {response.status_code}"])
        return
    if "cross-vendor" in title:
        other = next((row for row in candidates if int(row["vendorId"]) != int(accessory["vendorId"])), None)
        if not other:
            pytest.skip("No second vendor exists for cross-vendor validation")
        response = post(api_context, path, payload_for_accessory(accessory, map_key=map_key, map_ids=[int(other["vendorAccessoryMapId"])]))
        assert_error(response, case)
        after = db.fetch_one("SELECT Status AS statusId FROM cimpl.VendorAccessoryMap WHERE VendorAccessoryMapId = ?", (int(other["vendorAccessoryMapId"]),))
        assert after and int(after["statusId"]) == int(other["statusId"])
        record_metrics(request, case, [f"Response status: {response.status}"])
        return
    selected = candidates[:2] if "multiple" in title and len(candidates) > 1 else [accessory]
    if len(selected) > 1 and len({int(row["vendorId"]) for row in selected}) != 1:
        selected = [accessory]
    before = {int(row["vendorAccessoryMapId"]): row for row in selected}
    start = time.perf_counter()
    response = post(api_context, path, payload_for_accessory(selected[0], map_key=map_key, map_ids=list(before)))
    duration = elapsed_ms(start)
    assert_generic_success(response, case)
    for map_id, row in before.items():
        after = db.fetch_one("SELECT Status AS statusId, ht.Active AS hardwareTypeActive FROM cimpl.VendorAccessoryMap vam INNER JOIN cimpl.HardwareType ht ON ht.HardwareTypeId = vam.HardwareTypeId WHERE vam.VendorAccessoryMapId = ?", (map_id,))
        assert after, f"Accessory map row {map_id} was not found after action"
        assert int(after["statusId"]) != int(row["statusId"]) or bool(after["hardwareTypeActive"]) is active_expected
        if "persist" in title:
            assert bool(after["hardwareTypeActive"]) is active_expected
    if "promptly" in title:
        assert duration <= 60000
    record_metrics(request, case, [f"Response status: {response.status}", f"Response duration: {duration} ms", f"Response payload: {safe_json(response)}"])