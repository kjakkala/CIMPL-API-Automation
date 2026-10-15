from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass

import requests


VENDOR_MAP_SECTION = "Sync Management (Vendor Map)"

TESTRAIL_CASE_IDS = {
    "VMP01": {
        "01": "17864684", "02": "17864685", "03": "17864686", "04": "17864687", "05": "17864688",
        "06": "17864689", "07": "17864690", "08": "17864691", "09": "17864692", "10": "17864693", "11": "17864694",
    },
    "VMP02": {
        "01": "17864695", "02": "17864696", "03": "17864697", "04": "17864698", "05": "17864699",
        "06": "17864700", "07": "17864701", "08": "17864702",
    },
    "VMP03": {
        "01": "17864703", "02": "17864704", "03": "17864705", "04": "17864706", "05": "17864707",
        "06": "17864708", "07": "17864709", "08": "17864710",
    },
    "VMP04": {
        "01": "17864721", "02": "17864722", "03": "17864723", "04": "17864724", "05": "17864725",
        "06": "17864726", "07": "17864727", "08": "17864728", "09": "17864729", "10": "17864730", "11": "17864731",
    },
    "VMP05": {
        "01": "17864732", "02": "17864733", "03": "17864734", "04": "17864735", "05": "17864736",
        "06": "17864737", "07": "17864738", "08": "17864739",
    },
    "VMP18": {
        "01": "17864740", "02": "17864741", "03": "17864742", "04": "17864743", "05": "17864744",
        "06": "17864745", "07": "17864746", "08": "17864747",
    },
}


@dataclass(frozen=True)
class VendorMapCase:
    key: str
    category: str
    title: str
    steps: str
    expected: str


def case_id(prefix: str, key: str) -> str:
    return os.getenv(f"{prefix}_{key}_TESTRAIL_CASE", TESTRAIL_CASE_IDS.get(prefix, {}).get(key, ""))


def case_param(case: VendorMapCase, *, prefix: str, operation: str):
    import pytest

    return pytest.param(
        case,
        id=case.key,
        marks=[
            pytest.mark.swagger_section(VENDOR_MAP_SECTION),
            pytest.mark.swagger_operation(operation),
            pytest.mark.testrail_case(case_id(prefix, case.key)),
            pytest.mark.dashboard_title(f"{case.category} - {case.title}"),
        ],
    )


def record_metrics(request, case: VendorMapCase, lines: list[str]):
    request.node.user_properties.append(
        (
            "dashboard_metrics",
            "\n".join(
                [
                    f"Category: {case.category}",
                    f"Steps: {case.steps}",
                    f"Expected: {case.expected}",
                    *lines,
                ]
            ),
        )
    )


def safe_json(response) -> dict:
    try:
        value = response.json()
        return value if isinstance(value, dict) else {"raw": value}
    except Exception:  # noqa: BLE001
        return {}


def post(api_context, path: str, payload: dict):
    return api_context.post(
        path,
        data=json.dumps(payload, ensure_ascii=True),
        headers={"Content-Type": "application/json", "accept": "text/plain"},
    )


def unauthenticated_request(method: str, path: str, payload: dict | None = None):
    base_url = os.environ["BASE_URL"].rstrip("/")
    return requests.request(
        method,
        f"{base_url}/{path.lstrip('/')}",
        json=payload,
        headers={"Content-Type": "application/json", "accept": "text/plain"},
        timeout=60,
    )


def db_devices(db, *, vendor_id: int | None = None, status_id: int | None = None, name: str | None = None, sku: str | None = None, limit: int = 100):
    clauses = ["1 = 1"]
    params: list[object] = []
    if vendor_id is not None:
        clauses.append("vem.VendorId = ?")
        params.append(vendor_id)
    if status_id is not None:
        clauses.append("vem.Status = ?")
        params.append(status_id)
    if name:
        clauses.append("ht.Name LIKE '%' + ? + '%' ")
        params.append(name)
    if sku:
        clauses.append("vem.VendorEquipmentId LIKE '%' + ? + '%' ")
        params.append(sku)
    query = f"""
        SELECT TOP {int(limit)}
            vem.VendorEquipmentMapId AS vendorEquipmentMapId,
            vem.VendorId AS vendorId,
            vem.Status AS statusId,
            vem.HardwareTypeId AS hardwareTypeId,
            vem.VendorEquipmentId AS sku,
            ht.Name AS hardwareTypeName,
            ht.Active AS hardwareTypeActive
        FROM cimpl.VendorEquipmentMap vem
        LEFT JOIN cimpl.HardwareType ht ON ht.HardwareTypeId = vem.HardwareTypeId
        WHERE {' AND '.join(clauses)}
        ORDER BY vem.VendorEquipmentMapId
    """
    return db.fetch_all(query, tuple(params))


def db_accessories(db, *, vendor_id: int | None = None, status_id: int | None = None, name: str | None = None, sku: str | None = None, limit: int = 100):
    clauses = ["ht.HardwareCategoryId = 2"]
    params: list[object] = []
    if vendor_id is not None:
        clauses.append("vam.VendorId = ?")
        params.append(vendor_id)
    if status_id is not None:
        clauses.append("vam.Status = ?")
        params.append(status_id)
    if name:
        clauses.append("ht.Name LIKE '%' + ? + '%' ")
        params.append(name)
    if sku:
        clauses.append("vam.VendorAccessoryId LIKE '%' + ? + '%' ")
        params.append(sku)
    query = f"""
        SELECT TOP {int(limit)}
            vam.VendorAccessoryMapId AS vendorAccessoryMapId,
            vam.VendorAccessoryId AS vendorAccessoryId,
            vam.VendorId AS vendorId,
            vam.Status AS statusId,
            vam.HardwareTypeId AS hardwareTypeId,
            vam.VendorImportJobId AS vendorImportJobId,
            ht.Name AS hardwareTypeName,
            ht.Active AS hardwareTypeActive
        FROM cimpl.VendorAccessoryMap vam
        INNER JOIN cimpl.HardwareType ht ON ht.HardwareTypeId = vam.HardwareTypeId
        WHERE {' AND '.join(clauses)}
        ORDER BY vam.VendorAccessoryMapId
    """
    return db.fetch_all(query, tuple(params))


def first_accessory(db) -> dict:
    rows = db_accessories(db, limit=1)
    assert rows, "No category-2 cimpl.VendorAccessoryMap row is available for VMP automation"
    return rows[0]


def payload_for_accessory(accessory: dict, *, map_key: str = "vendorAccessoriesMapId", map_ids: list[int] | None = None) -> dict:
    return {
        "vendorId": int(accessory["vendorId"]),
        map_key: map_ids if map_ids is not None else [int(accessory["vendorAccessoryMapId"])],
    }


def first_device(db) -> dict:
    rows = db_devices(db, limit=1)
    assert rows, "cimpl.VendorEquipmentMap has no device available for VMP automation"
    return rows[0]


def prepare_device_candidate(db):
    row = db.fetch_one(
        "SELECT TOP 1 VendorEquipmentMapId AS vendorEquipmentMapId FROM cimpl.VendorEquipmentMap WHERE Status = 1 ORDER BY VendorEquipmentMapId"
    )
    if row:
        db.execute(
            "UPDATE cimpl.VendorEquipmentMap SET Status = 2 WHERE VendorEquipmentMapId = ?",
            (int(row["vendorEquipmentMapId"]),),
        )


def payload_for_device(device: dict, **overrides) -> dict:
    payload = {
        "vendorId": int(device["vendorId"]),
        "vendorEquipmentMapIds": [int(device["vendorEquipmentMapId"])],
    }
    payload.update(overrides)
    return payload


def response_items(payload: dict) -> list[dict]:
    items = payload.get("items", [])
    return items if isinstance(items, list) else []


def assert_generic_success(response, case: VendorMapCase):
    assert response.status in {200, 201}, f"{case.title}: status={response.status}; body={response.text()}"


def assert_error(response, case: VendorMapCase):
    assert response.status in {400, 401, 403, 404, 422}, (
        f"{case.title}: expected client/authorization error, got {response.status}; body={response.text()}"
    )


def assert_unauthenticated_rejected(response):
    assert response.status_code in {401, 403, 500}, (
        f"Expected authorization rejection, got {response.status_code}; body={response.text}"
    )
    if response.status_code == 500:
        body = safe_json(response)
        assert body.get("ErrorCode") == "IDENTITY_REQUEST_DOESNT_HAVE_CLAIMS", (
            f"Unexpected unauthenticated 500 body: {response.text}"
        )


def elapsed_ms(start: float) -> int:
    return round((time.perf_counter() - start) * 1000)
