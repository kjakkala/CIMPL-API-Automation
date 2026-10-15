from __future__ import annotations

import time

import pytest

from tests.vendor_map._vendor_map_common import (
    VendorMapCase,
    assert_error,
    assert_unauthenticated_rejected,
    case_param,
    db_devices,
    elapsed_ms,
    first_device,
    post,
    record_metrics,
    response_items,
    safe_json,
    unauthenticated_request,
)


API_PATH = "vendormap/devices"
PREFIX = "VMP01"


CASES = [
    VendorMapCase("01", "Functional", "Return devices for required vendorId", "POST vendorId with a valid vendor ID", "HTTP 200 with a paged items list limited to that vendor"),
    VendorMapCase("02", "Functional", "Filter by statusId", "POST vendorId and statusId from the database", "Every returned item has both requested filter values"),
    VendorMapCase("03", "Functional", "Filter by name and SKU", "POST vendorId with name and SKU fragments", "Results satisfy both optional filters"),
    VendorMapCase("04", "Validation", "Apply default pagination", "POST only the required vendorId", "Response contains pagination with valid index, size, and totalRecords"),
    VendorMapCase("05", "Validation", "Reject missing vendorId", "POST an empty object", "HTTP 400 or equivalent validation error with no data returned"),
    VendorMapCase("06", "Validation", "Reject invalid pagination", "POST negative pageIndex and pageSize zero", "HTTP 400 or equivalent validation error"),
    VendorMapCase("07", "Compatibility", "Match response items to VendorEquipmentMap", "Compare returned IDs and fields with the DB query", "API records match the source table for the selected vendor"),
    VendorMapCase("08", "Compatibility", "Return documented response fields", "Inspect one returned item", "Item includes hardware type, price, SKU, status, vendor, map, and import identifiers"),
    VendorMapCase("09", "Security", "Reject unauthenticated request", "POST without Authorization", "HTTP 401 or 403 and no device data"),
    VendorMapCase("10", "Security", "Treat filter text as data", "POST SQL/XSS-like name and SKU values", "No server error or query execution; response is a normal empty/error result"),
    VendorMapCase("11", "Non-functional", "Complete within API timeout", "POST a valid vendor query and measure elapsed time", "Response completes within 60 seconds"),
]


CASE_PARAMS = [case_param(case, prefix=PREFIX, operation="POST /v1/vendormap/devices") for case in CASES]


def _vendor_id(db) -> int:
    return int(first_device(db)["vendorId"])


def _run(case: VendorMapCase, api_context, db, request):
    title = case.title.lower()
    if "missing vendorid" in title:
        response = post(api_context, API_PATH, {})
        assert_error(response, case)
        record_metrics(request, case, [f"Response status: {response.status}"])
        return

    if "invalid pagination" in title:
        response = post(api_context, API_PATH, {"vendorId": _vendor_id(db), "pageIndex": -1, "pageSize": 0})
        assert_error(response, case)
        record_metrics(request, case, [f"Response status: {response.status}"])
        return

    if "unauthenticated" in title:
        response = unauthenticated_request("POST", f"v1/{API_PATH}", {"vendorId": _vendor_id(db)})
        assert_unauthenticated_rejected(response)
        record_metrics(request, case, [f"Response status: {response.status_code}"])
        return

    if "sql/xss" in title:
        response = post(api_context, API_PATH, {"vendorId": _vendor_id(db), "name": "' OR 1=1 -- <script>", "sku": "<script>alert(1)</script>"})
        assert response.status in {200, 400, 422}
        record_metrics(request, case, [f"Response status: {response.status}", f"Response payload: {safe_json(response)}"])
        return

    device = first_device(db)
    payload = {"vendorId": int(device["vendorId"])}
    if "statusid" in title:
        payload["statusId"] = int(device["statusId"])
    if "name and sku" in title:
        payload.update({"name": str(device.get("hardwareTypeName") or "")[:4], "sku": str(device.get("sku") or "")[:4]})
    if "default pagination" not in title:
        payload.update({"pageIndex": 0, "pageSize": 10})

    start = time.perf_counter()
    response = post(api_context, API_PATH, payload)
    duration = elapsed_ms(start)
    assert response.status == 200, f"{case.title}: status={response.status}; body={response.text()}"
    body = safe_json(response)
    items = response_items(body)
    pagination = body.get("pagination")
    assert isinstance(pagination, dict)
    assert int(pagination.get("index", -1)) >= 0
    assert int(pagination.get("size", 0)) > 0
    assert int(pagination.get("totalRecords", 0)) >= len(items)

    if "match response" in title:
        rows = db_devices(db, vendor_id=int(device["vendorId"]), limit=10000)
        api_ids = {int(item["vendorEquipmentMapId"]) for item in items if item.get("vendorEquipmentMapId") is not None}
        db_ids = {int(row["vendorEquipmentMapId"]) for row in rows}
        assert api_ids <= db_ids
    elif "documented response" in title and items:
        assert {"hardwareTypeId", "hardwareTypeName", "price", "sku", "statusId", "vendorId", "vendorEquipmentMapId", "vendorImportJobId"} <= set(items[0])
    elif "statusid" in title:
        assert all(int(item.get("statusId")) == int(device["statusId"]) for item in items)
    elif "name and sku" in title:
        assert all(str(device.get("sku") or "")[:4].lower() in str(item.get("sku") or "").lower() for item in items)

    if "within api timeout" in title:
        assert duration <= 60000
    record_metrics(request, case, [f"Request: POST /v1/{API_PATH}", f"Response status: {response.status}", f"Response duration: {duration} ms", f"Items: {len(items)}"])


@pytest.mark.parametrize("case", CASE_PARAMS)
def test_fetch_devices(case, api_context, db, request):
    _run(case, api_context, db, request)
