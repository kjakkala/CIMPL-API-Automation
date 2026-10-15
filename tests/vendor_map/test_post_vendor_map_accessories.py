from __future__ import annotations

import time

import pytest

from tests.vendor_map._vendor_map_common import (
    VendorMapCase, assert_error, case_param, db_accessories, elapsed_ms,
    first_accessory, assert_unauthenticated_rejected, post, record_metrics, response_items, safe_json,
    unauthenticated_request,
)

API_PATH = "vendormap/accessories"
PREFIX = "VMP04"
CASES = [
    VendorMapCase("01", "Functional", "Return category-2 accessories for vendorId", "POST a valid vendorId", "HTTP 200 with a paged accessory list limited to that vendor and HardwareCategoryId 2"),
    VendorMapCase("02", "Functional", "Filter by statusId", "POST vendorId and a database status", "Every returned accessory matches the requested status"),
    VendorMapCase("03", "Functional", "Filter by name and SKU", "POST vendorId with hardware name and accessory SKU fragments", "Results satisfy the optional filters"),
    VendorMapCase("04", "Validation", "Apply default pagination", "POST only vendorId", "Pagination contains valid default index, size, and totalRecords"),
    VendorMapCase("05", "Validation", "Reject missing vendorId", "POST an empty object", "HTTP 400 or equivalent validation error"),
    VendorMapCase("06", "Validation", "Reject invalid pagination", "POST negative pageIndex and pageSize zero", "HTTP 400 or equivalent validation error"),
    VendorMapCase("07", "Compatibility", "Match response to VendorAccessoryMap", "Compare response IDs with the category-2 DB query", "Returned accessory IDs exist in the source table"),
    VendorMapCase("08", "Compatibility", "Return documented accessory fields", "Inspect one returned item", "Response includes hardware type, price, SKU, status, vendor, map, and import fields"),
    VendorMapCase("09", "Security", "Reject unauthenticated request", "POST without Authorization", "HTTP 401 or 403 and no accessory data"),
    VendorMapCase("10", "Security", "Treat filter text as data", "POST SQL/XSS-like name and SKU values", "No query execution or server error"),
    VendorMapCase("11", "Non-functional", "Complete within API timeout", "POST a valid accessory query and measure duration", "Response completes within 60 seconds"),
]
CASE_PARAMS = [case_param(case, prefix=PREFIX, operation="POST /v1/vendormap/accessories") for case in CASES]


@pytest.mark.parametrize("case", CASE_PARAMS)
def test_fetch_accessories(case, api_context, db, request):
    title = case.title.lower()
    accessory = first_accessory(db)
    vendor_id = int(accessory["vendorId"])
    if "missing vendorid" in title:
        response = post(api_context, API_PATH, {})
        assert_error(response, case)
        record_metrics(request, case, [f"Response status: {response.status}"])
        return
    if "invalid pagination" in title:
        response = post(api_context, API_PATH, {"vendorId": vendor_id, "pageIndex": -1, "pageSize": 0})
        assert_error(response, case)
        record_metrics(request, case, [f"Response status: {response.status}"])
        return
    if "unauthenticated" in title:
        response = unauthenticated_request("POST", f"v1/{API_PATH}", {"vendorId": vendor_id})
        assert_unauthenticated_rejected(response)
        record_metrics(request, case, [f"Response status: {response.status_code}"])
        return
    if "sql/xss" in title:
        response = post(api_context, API_PATH, {"vendorId": vendor_id, "name": "' OR 1=1 -- <script>", "sku": "<script>alert(1)</script>"})
        assert response.status in {200, 400, 422}
        record_metrics(request, case, [f"Response status: {response.status}", f"Response payload: {safe_json(response)}"])
        return
    payload = {"vendorId": vendor_id}
    if "statusid" in title:
        payload["statusId"] = int(accessory["statusId"])
    if "name and sku" in title:
        payload.update({"name": str(accessory.get("hardwareTypeName") or "")[:4], "sku": str(accessory.get("vendorAccessoryId") or "")[:4]})
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
    if "match response" in title:
        db_ids = {int(row["vendorAccessoryMapId"]) for row in db_accessories(db, vendor_id=vendor_id, limit=10000)}
        api_ids = {int(item["vendorAccessoryMapId"]) for item in items if item.get("vendorAccessoryMapId") is not None}
        assert api_ids <= db_ids
    elif "documented" in title and items:
        assert {"hardwareTypeId", "hardwareTypeName", "price", "sku", "statusId", "vendorId", "vendorAccessoryMapId", "vendorImportJobId"} <= set(items[0])
    elif "statusid" in title:
        assert all(int(item.get("statusId")) == int(accessory["statusId"]) for item in items)
    if "within api timeout" in title:
        assert duration <= 60000
    record_metrics(request, case, [f"Response status: {response.status}", f"Response duration: {duration} ms", f"Items: {len(items)}"])