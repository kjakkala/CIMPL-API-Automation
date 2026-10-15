"""Tests for POST /locations/Search with DB-backed validation and detailed dashboard output."""

from __future__ import annotations

import json
import os

from utils.test_metadata import api_case


BASE_SELECT = """
    SELECT
        a.AddressId AS addressId,
        a.AddressLine1 AS addressLine1,
        a.AddressLine2 AS addressLine2,
        a.CountryId AS countryId,
        c.Name AS countryName,
        a.City AS city,
        a.LocationName AS locationName,
        a.LocationNumber AS locationNumber,
        a.ProvinceId AS provinceId,
        p.Code AS provinceCode,
        p.Name AS provinceName,
        a.PostalCode AS postalCode,
        a.Region AS region
    FROM cimpl.Address a
    LEFT JOIN cimpl.Province p ON a.ProvinceId = p.ProvinceId
    LEFT JOIN cimpl.Country c ON a.CountryId = c.CountryId
"""

REQUIRED_KEYS = [
    "addressId",
    "addressLine1",
    "countryId",
    "countryName",
    "city",
    "locationName",
    "locationNumber",
    "provinceId",
    "provinceCode",
    "provinceName",
    "postalCode",
    "region",
]

FILTER_COLUMN_MAP = {
    "locationNumber": "a.LocationNumber",
    "locationName": "a.LocationName",
    "addressLine1": "a.AddressLine1",
    "city": "a.City",
    "state": "p.Name",
    "postalCode": "a.PostalCode",
    "country": "c.Name",
}

FILTER_ROW_FIELD_MAP = {
    "locationNumber": "locationNumber",
    "locationName": "locationName",
    "addressLine1": "addressLine1",
    "city": "city",
    "state": "provinceName",
    "postalCode": "postalCode",
    "country": "countryName",
}


def _record_dashboard_metrics(request, lines: list[str]):
    request.node.user_properties.append(("dashboard_metrics", "\n".join(lines)))


def _search_locations(api_context, payload: dict):
    return api_context.post(
        "locations/Search",
        data=json.dumps(payload, ensure_ascii=True),
        headers={"Content-Type": "application/json", "accept": "text/plain"},
    )


def _payload(filters: dict | None = None, *, page_index: int = 0, page_size: int = 5000) -> dict:
    data = {
        "pageIndex": page_index,
        "pageSize": page_size,
        "locationNumber": None,
        "locationName": None,
        "addressLine1": None,
        "city": None,
        "state": None,
        "postalCode": None,
        "country": None,
    }
    if filters:
        data.update(filters)
    return data


def _db_rows(db, filters: dict | None = None) -> list[dict]:
    where = ["a.Active = 1"]
    params: list[str] = []

    for key, value in (filters or {}).items():
        if value is None:
            continue
        col = FILTER_COLUMN_MAP.get(key)
        if not col:
            continue
        where.append(f"{col} LIKE '%' + ? + '%'")
        params.append(str(value))

    query = BASE_SELECT
    if where:
        query += "\nWHERE " + "\n  AND ".join(where)

    return db.fetch_all(query, tuple(params))


def _api_items(payload: dict) -> list[dict]:
    items = payload.get("items", [])
    return items if isinstance(items, list) else []


def _api_by_address_id(items: list[dict]) -> dict[int, dict]:
    result: dict[int, dict] = {}
    for item in items:
        if item.get("addressId") is not None:
            result[int(item["addressId"])] = item
    return result


def _db_by_address_id(rows: list[dict]) -> dict[int, dict]:
    return {int(row["addressId"]): row for row in rows if row.get("addressId") is not None}


def _assert_status_ok(response, title: str):
    assert response.status == 200, f"{title}: expected 200, got {response.status}. Response={response.text()}"


def _assert_common_page_rules(pagination: dict, items: list[dict], *, allow_zero: bool, title: str):
    assert isinstance(pagination, dict), f"{title}: missing pagination object"
    assert int(pagination.get("index", -1)) >= 0, f"{title}: pagination.index must be >= 0"
    assert int(pagination.get("size", 0)) > 0, f"{title}: pagination.size must be > 0"

    total_records = int(pagination.get("totalRecords", 0))
    if allow_zero:
        assert total_records >= 0, f"{title}: pagination.totalRecords must be >= 0"
    else:
        assert total_records > 0, f"{title}: pagination.totalRecords must be > 0"

    if total_records > 0:
        assert items, f"{title}: items must not be empty when totalRecords > 0"


def _assert_required_not_null(item: dict, title: str):
    for key in REQUIRED_KEYS:
        assert key in item, f"{title}: key '{key}' missing in item={item}"
        assert item[key] is not None, f"{title}: key '{key}' must not be null in item={item}"


def _assert_api_matches_db(rows: list[dict], items: list[dict], title: str):
    api_map = _api_by_address_id(items)
    db_map = _db_by_address_id(rows)

    assert set(api_map.keys()) == set(db_map.keys()), (
        f"{title}: addressId set mismatch. "
        f"API-only={sorted(set(api_map.keys()) - set(db_map.keys()))[:20]}, "
        f"DB-only={sorted(set(db_map.keys()) - set(api_map.keys()))[:20]}"
    )

    fields = [
        "addressLine1",
        "countryId",
        "countryName",
        "city",
        "locationName",
        "locationNumber",
        "provinceId",
        "provinceCode",
        "provinceName",
        "postalCode",
        "region",
    ]
    mismatches = []
    for address_id, db_row in db_map.items():
        api_row = api_map[address_id]
        for field in fields:
            if api_row.get(field) != db_row.get(field):
                mismatches.append((address_id, field, db_row.get(field), api_row.get(field)))

    assert not mismatches, (
        f"{title}: field mismatches found. First mismatches={mismatches[:20]}"
    )


def _db_count(db, filters: dict) -> int:
    return len(_db_rows(db, filters))


def _find_filter_value_for_count(db, filter_key: str, *, target: str) -> str | None:
    column = FILTER_COLUMN_MAP[filter_key]
    if target == "single":
        having = "= 1"
    else:
        having = "> 1"

    candidate_query = (
        "SELECT TOP 200 "
        f"{column} AS value "
        "FROM cimpl.Address a "
        "LEFT JOIN cimpl.Province p ON a.ProvinceId = p.ProvinceId "
        "LEFT JOIN cimpl.Country c ON a.CountryId = c.CountryId "
        "WHERE a.Active = 1 "
        f"  AND {column} IS NOT NULL "
        f"  AND LTRIM(RTRIM(CAST({column} AS NVARCHAR(4000)))) <> '' "
        f"GROUP BY {column} "
        f"HAVING COUNT(*) {having}"
    )
    candidates = db.fetch_all(candidate_query)

    for row in candidates:
        value = str(row["value"])
        count = _db_count(db, {filter_key: value})
        if target == "single" and count == 1:
            return value
        if target == "multiple" and count > 1:
            return value

    fallback_query = (
        "SELECT TOP 500 "
        f"{column} AS value "
        "FROM cimpl.Address a "
        "LEFT JOIN cimpl.Province p ON a.ProvinceId = p.ProvinceId "
        "LEFT JOIN cimpl.Country c ON a.CountryId = c.CountryId "
        "WHERE a.Active = 1 "
        f"  AND {column} IS NOT NULL "
        f"  AND LTRIM(RTRIM(CAST({column} AS NVARCHAR(4000)))) <> ''"
    )
    fallbacks = db.fetch_all(fallback_query)
    for row in fallbacks:
        value = str(row["value"])
        count = _db_count(db, {filter_key: value})
        if target == "single" and count == 1:
            return value
        if target == "multiple" and count > 1:
            return value

    return None


def _run_filter_case_with_db_driven_sets(
    test_title: str,
    testrail_id: str,
    filter_key: str,
    api_context,
    db,
    request,
):
    single_value = _find_filter_value_for_count(db, filter_key, target="single")
    multiple_value = _find_filter_value_for_count(db, filter_key, target="multiple")

    assert single_value is not None, f"{test_title}: unable to find DB value returning exactly 1 record"
    assert multiple_value is not None, f"{test_title}: unable to find DB value returning multiple records"

    datasets = [
        ("zero-record", f"__NO_MATCH_{filter_key.upper()}__", 0),
        ("single-record", single_value, 1),
        ("multiple-record", multiple_value, None),
    ]

    metrics = [
        f"CaseId: {testrail_id}",
        f"Title: {test_title}",
        f"DB target: {os.getenv('DB_SERVER', '')} / {os.getenv('DB_NAME', '')}",
        f"Filter key under test: {filter_key}",
        "Data sets executed: 0-record, 1-record, multiple-record",
    ]

    for label, value, expected in datasets:
        filters = {filter_key: value}
        response = _search_locations(api_context, _payload(filters))
        _assert_status_ok(response, f"{test_title} [{label}]")

        payload = response.json()
        pagination = payload.get("pagination", {})
        items = _api_items(payload)
        rows = _db_rows(db, filters)

        _assert_common_page_rules(
            pagination,
            items,
            allow_zero=True,
            title=f"{test_title} [{label}]",
        )

        assert int(pagination.get("totalRecords", 0)) == len(rows), (
            f"{test_title} [{label}]: totalRecords mismatch API={pagination.get('totalRecords')} DB={len(rows)}"
        )
        assert len(items) == len(rows), (
            f"{test_title} [{label}]: returned items count mismatch API={len(items)} DB={len(rows)}"
        )

        if expected == 0:
            assert len(rows) == 0, f"{test_title} [{label}]: expected zero records, got {len(rows)}"
        elif expected == 1:
            assert len(rows) == 1, f"{test_title} [{label}]: expected one record, got {len(rows)}"
        else:
            assert len(rows) > 1, f"{test_title} [{label}]: expected multiple records, got {len(rows)}"

        _assert_api_matches_db(rows, items, f"{test_title} [{label}]")

        metrics.append(f"--- Data set: {label}")
        metrics.append(f"Input filter value: {value}")
        metrics.append(f"Request payload: {json.dumps(_payload(filters), ensure_ascii=True)}")
        metrics.append(f"Pagination: {json.dumps(pagination, ensure_ascii=True)}")
        metrics.append(f"DB count: {len(rows)} | API count: {len(items)}")
        metrics.append(f"Validated addressIds sample (first 25): {[row['addressId'] for row in rows[:25]]}")

    _record_dashboard_metrics(request, metrics)


def _find_multi_filter_set_for_count(db, filter_keys: list[str], *, target: str) -> dict | None:
    active_rows = _db_rows(db, {})
    seen: set[tuple] = set()

    for row in active_rows:
        values = []
        for key in filter_keys:
            field = FILTER_ROW_FIELD_MAP[key]
            raw = row.get(field)
            if raw is None:
                values = []
                break
            text = str(raw).strip()
            if not text:
                values = []
                break
            values.append(text)

        if not values:
            continue

        tup = tuple(values)
        if tup in seen:
            continue
        seen.add(tup)

        filters = {k: v for k, v in zip(filter_keys, values)}
        count = _db_count(db, filters)
        if target == "single" and count == 1:
            return filters
        if target == "multiple" and count > 1:
            return filters

    return None


def _run_filter_case(test_title: str, testrail_id: str, filters: dict, api_context, db, request):
    response = _search_locations(api_context, _payload(filters))
    _assert_status_ok(response, test_title)

    payload = response.json()
    pagination = payload.get("pagination", {})
    items = _api_items(payload)
    rows = _db_rows(db, filters)

    _assert_common_page_rules(pagination, items, allow_zero=True, title=test_title)

    assert int(pagination.get("totalRecords", 0)) == len(rows), (
        f"{test_title}: pagination.totalRecords mismatch. API={pagination.get('totalRecords')} DB={len(rows)}"
    )

    assert len(items) == len(rows), (
        f"{test_title}: returned items count mismatch. API={len(items)} DB={len(rows)}"
    )

    _assert_api_matches_db(rows, items, test_title)

    metrics = [
        f"CaseId: {testrail_id}",
        f"Title: {test_title}",
        f"DB target: {os.getenv('DB_SERVER', '')} / {os.getenv('DB_NAME', '')}",
        f"Request payload: {json.dumps(_payload(filters), ensure_ascii=True)}",
        f"Response status: {response.status}",
        f"Pagination: {json.dumps(pagination, ensure_ascii=True)}",
        f"DB records matched: {len(rows)}",
        f"API items returned: {len(items)}",
        f"Validated addressIds (sample first 25): {[row['addressId'] for row in rows[:25]]}",
        "Validation: API result set and fields match DB query output.",
    ]
    _record_dashboard_metrics(request, metrics)


class TestPostLocationsSearch:

    @api_case(
        section="Location",
        operation="POST /locations/Search",
        testrail_id="17770481",
        title="LOC01 - Search Location by Location Number",
    )
    def test_search_by_location_number(self, api_context, db, request):
        _run_filter_case_with_db_driven_sets(
            "LOC01 - Search Location by Location Number",
            "17770481",
            "locationNumber",
            api_context,
            db,
            request,
        )

    @api_case(
        section="Location",
        operation="POST /locations/Search",
        testrail_id="17770482",
        title="Search Location by Location Name",
    )
    def test_search_by_location_name(self, api_context, db, request):
        _run_filter_case_with_db_driven_sets(
            "Search Location by Location Name",
            "17770482",
            "locationName",
            api_context,
            db,
            request,
        )

    @api_case(
        section="Location",
        operation="POST /locations/Search",
        testrail_id="17770483",
        title="Search Location by Address Line 1",
    )
    def test_search_by_address_line1(self, api_context, db, request):
        _run_filter_case_with_db_driven_sets(
            "Search Location by Address Line 1",
            "17770483",
            "addressLine1",
            api_context,
            db,
            request,
        )

    @api_case(
        section="Location",
        operation="POST /locations/Search",
        testrail_id="17770484",
        title="Search Location by City",
    )
    def test_search_by_city(self, api_context, db, request):
        _run_filter_case_with_db_driven_sets(
            "Search Location by City",
            "17770484",
            "city",
            api_context,
            db,
            request,
        )

    @api_case(
        section="Location",
        operation="POST /locations/Search",
        testrail_id="17770485",
        title="Search Location by Province/State",
    )
    def test_search_by_state(self, api_context, db, request):
        _run_filter_case_with_db_driven_sets(
            "Search Location by Province/State",
            "17770485",
            "state",
            api_context,
            db,
            request,
        )

    @api_case(
        section="Location",
        operation="POST /locations/Search",
        testrail_id="17770486",
        title="Search Location by Postal Code",
    )
    def test_search_by_postal_code(self, api_context, db, request):
        _run_filter_case_with_db_driven_sets(
            "Search Location by Postal Code",
            "17770486",
            "postalCode",
            api_context,
            db,
            request,
        )

    @api_case(
        section="Location",
        operation="POST /locations/Search",
        testrail_id="17770487",
        title="Search Location by Country",
    )
    def test_search_by_country(self, api_context, db, request):
        _run_filter_case_with_db_driven_sets(
            "Search Location by Country",
            "17770487",
            "country",
            api_context,
            db,
            request,
        )

    @api_case(
        section="Location",
        operation="POST /locations/Search",
        testrail_id="17770488",
        title="Search Using Multiple Filters",
    )
    def test_search_using_multiple_filters(self, api_context, db, request):
        combinations = [
            ("Location Number + City", ["locationNumber", "city"]),
            ("Location Number + Country", ["locationNumber", "country"]),
            ("Location Name + City", ["locationName", "city"]),
            ("City + Province", ["city", "state"]),
            ("City + Country", ["city", "country"]),
            ("Province + Country", ["state", "country"]),
            ("Postal Code + Country", ["postalCode", "country"]),
            ("Location Number + City + Country", ["locationNumber", "city", "country"]),
        ]

        details = [
            "CaseId: 17770488",
            "Title: Search Using Multiple Filters",
            f"DB target: {os.getenv('DB_SERVER', '')} / {os.getenv('DB_NAME', '')}",
            f"Total combinations executed: {len(combinations)}",
            "High-level validation: each combination executed with 0-record, 1-record, and multiple-record DB-driven data sets.",
        ]

        for label, keys in combinations:
            single_filters = _find_multi_filter_set_for_count(db, keys, target="single")
            multiple_filters = _find_multi_filter_set_for_count(db, keys, target="multiple")
            assert single_filters is not None, f"{label}: unable to find single-record DB-driven filter set"
            assert multiple_filters is not None, f"{label}: unable to find multiple-record DB-driven filter set"

            zero_filters = {key: f"__NO_MATCH_{key.upper()}__" for key in keys}
            datasets = [
                ("zero-record", zero_filters, 0),
                ("single-record", single_filters, 1),
                ("multiple-record", multiple_filters, None),
            ]

            details.append(f"--- Combination: {label}")
            details.append(f"Keys: {keys}")

            for dataset_label, filters, expected in datasets:
                response = _search_locations(api_context, _payload(filters))
                _assert_status_ok(response, f"Search Using Multiple Filters - {label} [{dataset_label}]")
                payload = response.json()
                pagination = payload.get("pagination", {})
                items = _api_items(payload)
                rows = _db_rows(db, filters)

                _assert_common_page_rules(
                    pagination,
                    items,
                    allow_zero=True,
                    title=f"Search Using Multiple Filters - {label} [{dataset_label}]",
                )

                assert int(pagination.get("totalRecords", 0)) == len(rows), (
                    f"{label} [{dataset_label}]: totalRecords mismatch API={pagination.get('totalRecords')} DB={len(rows)}"
                )
                assert len(items) == len(rows), (
                    f"{label} [{dataset_label}]: items count mismatch API={len(items)} DB={len(rows)}"
                )

                if expected == 0:
                    assert len(rows) == 0, f"{label} [{dataset_label}]: expected zero records, got {len(rows)}"
                elif expected == 1:
                    assert len(rows) == 1, f"{label} [{dataset_label}]: expected one record, got {len(rows)}"
                else:
                    assert len(rows) > 1, f"{label} [{dataset_label}]: expected multiple records, got {len(rows)}"

                _assert_api_matches_db(rows, items, f"Search Using Multiple Filters - {label} [{dataset_label}]")

                details.append(f"Data set: {dataset_label}")
                details.append(f"Filters: {json.dumps(filters, ensure_ascii=True)}")
                details.append(
                    f"DB count: {len(rows)} | API count: {len(items)} | totalRecords: {pagination.get('totalRecords')}"
                )
                details.append(f"Validated IDs sample (first 20): {[row['addressId'] for row in rows[:20]]}")

        _record_dashboard_metrics(request, details)

    @api_case(
        section="Location",
        operation="POST /locations/Search",
        testrail_id="17770489",
        title="Verify No Records Found",
    )
    def test_verify_no_records_found(self, api_context, db, request):
        filters = {
            "locationNumber": "__NOT_A_REAL_LOCATION_NUMBER__",
            "city": "__NO_CITY__",
            "country": "__NO_COUNTRY__",
        }
        response = _search_locations(api_context, _payload(filters))
        _assert_status_ok(response, "Verify No Records Found")

        payload = response.json()
        pagination = payload.get("pagination", {})
        items = _api_items(payload)
        rows = _db_rows(db, filters)

        _assert_common_page_rules(pagination, items, allow_zero=True, title="Verify No Records Found")
        assert len(rows) == 0, f"Expected DB no records, got {len(rows)}"
        assert int(pagination.get("totalRecords", -1)) == 0, (
            f"Expected totalRecords=0, got {pagination.get('totalRecords')}"
        )
        assert len(items) == 0, f"Expected API items=0, got {len(items)}"

        _record_dashboard_metrics(
            request,
            [
                "CaseId: 17770489",
                "Title: Verify No Records Found",
                f"DB target: {os.getenv('DB_SERVER', '')} / {os.getenv('DB_NAME', '')}",
                f"Request payload: {json.dumps(_payload(filters), ensure_ascii=True)}",
                f"Pagination: {json.dumps(pagination, ensure_ascii=True)}",
                "Validation: API and DB both returned zero records as expected.",
            ],
        )

    @api_case(
        section="Location",
        operation="POST /locations/Search",
        testrail_id="17770490",
        title="Verify All records with Page index & Page size.",
    )
    def test_verify_pagination_index_and_size(self, api_context, db, request):
        page_size = 50
        response_page_0 = _search_locations(api_context, _payload(page_index=0, page_size=page_size))
        response_page_1 = _search_locations(api_context, _payload(page_index=1, page_size=page_size))

        _assert_status_ok(response_page_0, "Verify All records with Page index & Page size.")
        _assert_status_ok(response_page_1, "Verify All records with Page index & Page size.")

        payload_0 = response_page_0.json()
        payload_1 = response_page_1.json()
        items_0 = _api_items(payload_0)
        items_1 = _api_items(payload_1)
        page_0 = payload_0.get("pagination", {})
        page_1 = payload_1.get("pagination", {})

        _assert_common_page_rules(page_0, items_0, allow_zero=False, title="Pagination page 0")
        _assert_common_page_rules(page_1, items_1, allow_zero=False, title="Pagination page 1")

        db_total = len(_db_rows(db, {}))
        assert int(page_0.get("totalRecords", -1)) == db_total, (
            f"Page 0 totalRecords mismatch API={page_0.get('totalRecords')} DB={db_total}"
        )
        assert int(page_1.get("totalRecords", -1)) == db_total, (
            f"Page 1 totalRecords mismatch API={page_1.get('totalRecords')} DB={db_total}"
        )

        ids_0 = {item["addressId"] for item in items_0 if item.get("addressId") is not None}
        ids_1 = {item["addressId"] for item in items_1 if item.get("addressId") is not None}
        overlap = sorted(ids_0 & ids_1)
        assert not overlap, f"Page overlap detected between pageIndex 0 and 1. Overlap IDs={overlap[:20]}"

        _record_dashboard_metrics(
            request,
            [
                "CaseId: 17770490",
                "Title: Verify All records with Page index & Page size.",
                f"DB target: {os.getenv('DB_SERVER', '')} / {os.getenv('DB_NAME', '')}",
                f"Page request 0 payload: {json.dumps(_payload(page_index=0, page_size=page_size), ensure_ascii=True)}",
                f"Page request 1 payload: {json.dumps(_payload(page_index=1, page_size=page_size), ensure_ascii=True)}",
                f"Page 0 pagination: {json.dumps(page_0, ensure_ascii=True)}",
                f"Page 1 pagination: {json.dumps(page_1, ensure_ascii=True)}",
                f"DB total records: {db_total}",
                f"Page 0 records returned: {len(items_0)}",
                f"Page 1 records returned: {len(items_1)}",
                f"Overlap count across page 0 and 1: {len(overlap)}",
                "Validation: pagination index/size/totalRecords and cross-page uniqueness validated.",
            ],
        )

    @api_case(
        section="Location",
        operation="POST /locations/Search",
        testrail_id="17770491",
        title="Sorting Validation",
    )
    def test_sorting_validation(self, api_context, db, request):
        strict_row = db.fetch_one(
            """
            SELECT TOP 1
                a.LocationName AS locationName,
                a.LocationNumber AS locationNumber
            FROM cimpl.Address a
            WHERE a.LocationName IS NOT NULL
              AND a.LocationNumber IS NOT NULL
              AND a.AddressLine1 IS NOT NULL
              AND a.City IS NOT NULL
              AND a.PostalCode IS NOT NULL
              AND a.Region IS NOT NULL
              AND a.ProvinceId IS NOT NULL
              AND a.CountryId IS NOT NULL
            ORDER BY a.AddressId
            """
        )
        assert strict_row, "No strict row with required fields was found for sorting validation"

        filters = {"locationNumber": strict_row["locationNumber"]}
        response = _search_locations(api_context, _payload(filters))
        _assert_status_ok(response, "Sorting Validation")

        payload = response.json()
        pagination = payload.get("pagination", {})
        items = _api_items(payload)

        _assert_common_page_rules(pagination, items, allow_zero=False, title="Sorting Validation")

        # Rule checks requested for this case.
        location_order = [(item.get("locationName") or "", item.get("addressLine1") or "") for item in items]
        expected = sorted(location_order, key=lambda pair: (pair[0].lower(), pair[1].lower()))
        assert location_order == expected, (
            "Sorting mismatch: expected ascending by locationName, then addressLine1 when names are equal"
        )

        for item in items:
            _assert_required_not_null(item, "Sorting Validation")

        _record_dashboard_metrics(
            request,
            [
                "CaseId: 17770491",
                "Title: Sorting Validation",
                f"DB target: {os.getenv('DB_SERVER', '')} / {os.getenv('DB_NAME', '')}",
                f"Request payload: {json.dumps(_payload(filters), ensure_ascii=True)}",
                f"Pagination: {json.dumps(pagination, ensure_ascii=True)}",
                f"Returned records: {len(items)}",
                "Validation rules executed:",
                "1) Status code = 200",
                "2) pagination.index >= 0",
                "3) pagination.size > 0",
                "4) pagination.totalRecords > 0",
                "5) items not empty",
                "6) locationName ascending",
                "7) addressLine1 ascending when locationName equal",
                "8) required fields not null for each item",
            ],
        )


def _db_active_total(db) -> int:
    return int(db.fetch_scalar("SELECT COUNT(*) FROM cimpl.Address WHERE Active = 1") or 0)


def _safe_json(response) -> dict:
    try:
        payload = response.json()
        return payload if isinstance(payload, dict) else {"raw": payload}
    except Exception:  # noqa: BLE001
        return {}


def _run_negative_case(
    *,
    case_id: str,
    title: str,
    payload: dict,
    allowed_statuses: set[int],
    api_context,
    db,
    request,
    expected_error_text: str | None = None,
    validate_db_count: bool = True,
    validate_filter_count: bool = False,
    filter_payload: dict | None = None,
):
    before_active_count = _db_active_total(db)
    response = _search_locations(api_context, payload)
    body = _safe_json(response)
    after_active_count = _db_active_total(db)

    assert response.status in allowed_statuses, (
        f"{title}: expected status in {sorted(allowed_statuses)}, got {response.status}. "
        f"Response={response.text()}"
    )

    if expected_error_text:
        message_source = json.dumps(body, ensure_ascii=True) if body else response.text()
        assert expected_error_text.lower() in message_source.lower(), (
            f"{title}: expected error text '{expected_error_text}' not found. "
            f"Body={message_source}"
        )

    if validate_db_count:
        assert before_active_count == after_active_count, (
            f"{title}: DB active row count changed unexpectedly. "
            f"Before={before_active_count}, After={after_active_count}"
        )

    details = [
        f"CaseId: {case_id}",
        f"Title: {title}",
        f"DB target: {os.getenv('DB_SERVER', '')} / {os.getenv('DB_NAME', '')}",
        f"Request payload: {json.dumps(payload, ensure_ascii=True)}",
        f"Response status: {response.status}",
        f"Response body: {json.dumps(body, ensure_ascii=True) if body else response.text()}",
        f"DB active count before call: {before_active_count}",
        f"DB active count after call: {after_active_count}",
    ]

    if validate_filter_count and response.status == 200:
        api_payload = body
        pagination = api_payload.get("pagination", {}) if isinstance(api_payload, dict) else {}
        api_items = _api_items(api_payload) if isinstance(api_payload, dict) else []
        db_rows = _db_rows(db, filter_payload or {})
        details.append(f"Pagination: {json.dumps(pagination, ensure_ascii=True)}")
        details.append(f"API items count: {len(api_items)}")
        details.append(f"DB matched rows count: {len(db_rows)}")
        assert int(pagination.get("totalRecords", -1)) == len(db_rows), (
            f"{title}: totalRecords mismatch API={pagination.get('totalRecords')} DB={len(db_rows)}"
        )

    _record_dashboard_metrics(request, details)


class TestPostLocationsSearchNegative:

    @api_case(
        section="Location",
        operation="POST /locations/Search",
        testrail_id="17770492",
        title="Invalid pageIndex",
    )
    def test_invalid_page_index(self, api_context, db, request):
        _run_negative_case(
            case_id="17770492",
            title="Invalid pageIndex",
            payload=_payload(page_index=-1, page_size=100),
            allowed_statuses={400},
            api_context=api_context,
            db=db,
            request=request,
            expected_error_text="validation",
        )

    @api_case(
        section="Location",
        operation="POST /locations/Search",
        testrail_id="17770493",
        title="Invalid pageSize",
    )
    def test_invalid_page_size(self, api_context, db, request):
        _run_negative_case(
            case_id="17770493",
            title="Invalid pageSize",
            payload=_payload(page_index=0, page_size=5001),
            allowed_statuses={400},
            api_context=api_context,
            db=db,
            request=request,
            expected_error_text="pagesize",
        )

    @api_case(
        section="Location",
        operation="POST /locations/Search",
        testrail_id="17770494",
        title="Invalid datatype for pagination",
    )
    def test_invalid_datatype_for_pagination(self, api_context, db, request):
        payload = _payload()
        payload["pageIndex"] = "one"
        payload["pageSize"] = "hundred"
        _run_negative_case(
            case_id="17770494",
            title="Invalid datatype for pagination",
            payload=payload,
            allowed_statuses={400},
            api_context=api_context,
            db=db,
            request=request,
            expected_error_text="could not be converted",
        )

    @api_case(
        section="Location",
        operation="POST /locations/Search",
        testrail_id="17770495",
        title="Missing required pagination fields",
    )
    def test_missing_required_pagination_fields(self, api_context, db, request):
        payload = _payload()
        payload.pop("pageIndex", None)
        payload.pop("pageSize", None)
        _run_negative_case(
            case_id="17770495",
            title="Missing required pagination fields",
            payload=payload,
            allowed_statuses={200},
            api_context=api_context,
            db=db,
            request=request,
            validate_filter_count=True,
            filter_payload={},
        )

    @api_case(
        section="Location",
        operation="POST /locations/Search",
        testrail_id="17770496",
        title="Null pagination fields",
    )
    def test_null_pagination_fields(self, api_context, db, request):
        payload = _payload()
        payload["pageIndex"] = None
        payload["pageSize"] = None
        _run_negative_case(
            case_id="17770496",
            title="Null pagination fields",
            payload=payload,
            allowed_statuses={400},
            api_context=api_context,
            db=db,
            request=request,
            expected_error_text="could not be converted",
        )

    @api_case(
        section="Location",
        operation="POST /locations/Search",
        testrail_id="17770497",
        title="Unknown request property",
    )
    def test_unknown_request_property(self, api_context, db, request):
        payload = _payload()
        payload["randomField"] = "x"
        _run_negative_case(
            case_id="17770497",
            title="Unknown request property",
            payload=payload,
            allowed_statuses={200},
            api_context=api_context,
            db=db,
            request=request,
            validate_filter_count=True,
            filter_payload={},
        )

    @api_case(
        section="Location",
        operation="POST /locations/Search",
        testrail_id="17770498",
        title="Invalid datatype for filter fields",
    )
    def test_invalid_datatype_for_filter_fields(self, api_context, db, request):
        payload = _payload()
        payload["city"] = True
        _run_negative_case(
            case_id="17770498",
            title="Invalid datatype for filter fields",
            payload=payload,
            allowed_statuses={400},
            api_context=api_context,
            db=db,
            request=request,
            expected_error_text="could not be converted",
        )

    @api_case(
        section="Location",
        operation="POST /locations/Search",
        testrail_id="17770499",
        title="Whitespace-only filters",
    )
    def test_whitespace_only_filters(self, api_context, db, request):
        filters = {"city": "   ", "country": "   "}
        _run_negative_case(
            case_id="17770499",
            title="Whitespace-only filters",
            payload=_payload(filters),
            allowed_statuses={200},
            api_context=api_context,
            db=db,
            request=request,
            validate_filter_count=True,
            filter_payload={},
        )

    @api_case(
        section="Location",
        operation="POST /locations/Search",
        testrail_id="17770500",
        title="Very long filter values",
    )
    def test_very_long_filter_values(self, api_context, db, request):
        filters = {"locationName": "A" * 5000}
        _run_negative_case(
            case_id="17770500",
            title="Very long filter values",
            payload=_payload(filters),
            allowed_statuses={400, 500},
            api_context=api_context,
            db=db,
            request=request,
            expected_error_text="truncated",
        )

    @api_case(
        section="Location",
        operation="POST /locations/Search",
        testrail_id="17770501",
        title="Special characters and injection payloads",
    )
    def test_special_characters_and_injection_payloads(self, api_context, db, request):
        filters = {"locationName": "' OR 1=1 --", "city": "<script>alert(1)</script>"}
        _run_negative_case(
            case_id="17770501",
            title="Special characters and injection payloads",
            payload=_payload(filters),
            allowed_statuses={200, 400},
            api_context=api_context,
            db=db,
            request=request,
            validate_filter_count=True,
            filter_payload=filters,
        )

    @api_case(
        section="Location",
        operation="POST /locations/Search",
        testrail_id="17770502",
        title="All filters null with large pageSize",
    )
    def test_all_filters_null_with_large_page_size(self, api_context, db, request):
        _run_negative_case(
            case_id="17770502",
            title="All filters null with large pageSize",
            payload=_payload(page_size=5000),
            allowed_statuses={200},
            api_context=api_context,
            db=db,
            request=request,
            validate_filter_count=True,
            filter_payload={},
        )

    @api_case(
        section="Location",
        operation="POST /locations/Search",
        testrail_id="17770503",
        title="Conflicting filters that should return zero",
    )
    def test_conflicting_filters_should_return_zero(self, api_context, db, request):
        filters = {
            "locationNumber": "LOC01",
            "city": "__NO_CITY__",
            "country": "__NO_COUNTRY__",
        }
        _run_negative_case(
            case_id="17770503",
            title="Conflicting filters that should return zero",
            payload=_payload(filters),
            allowed_statuses={200},
            api_context=api_context,
            db=db,
            request=request,
            validate_filter_count=True,
            filter_payload=filters,
        )

    @api_case(
        section="Location",
        operation="POST /locations/Search",
        testrail_id="17770504",
        title="Case-sensitivity check",
    )
    def test_case_sensitivity_check(self, api_context, db, request):
        lower_filters = {"country": "canada"}
        proper_filters = {"country": "Canada"}

        lower_response = _search_locations(api_context, _payload(lower_filters))
        proper_response = _search_locations(api_context, _payload(proper_filters))
        _assert_status_ok(lower_response, "Case-sensitivity check - lowercase")
        _assert_status_ok(proper_response, "Case-sensitivity check - proper case")

        lower_body = _safe_json(lower_response)
        proper_body = _safe_json(proper_response)

        lower_total = int(lower_body.get("pagination", {}).get("totalRecords", -1))
        proper_total = int(proper_body.get("pagination", {}).get("totalRecords", -1))

        lower_db_count = len(_db_rows(db, lower_filters))
        proper_db_count = len(_db_rows(db, proper_filters))

        assert lower_total == lower_db_count, (
            f"Case-sensitivity check (lowercase) mismatch API={lower_total} DB={lower_db_count}"
        )
        assert proper_total == proper_db_count, (
            f"Case-sensitivity check (proper case) mismatch API={proper_total} DB={proper_db_count}"
        )

        _record_dashboard_metrics(
            request,
            [
                "CaseId: 17770504",
                "Title: Case-sensitivity check",
                f"DB target: {os.getenv('DB_SERVER', '')} / {os.getenv('DB_NAME', '')}",
                f"Lowercase payload: {json.dumps(_payload(lower_filters), ensure_ascii=True)}",
                f"Proper-case payload: {json.dumps(_payload(proper_filters), ensure_ascii=True)}",
                f"Lowercase API totalRecords: {lower_total} | DB count: {lower_db_count}",
                f"Proper-case API totalRecords: {proper_total} | DB count: {proper_db_count}",
                "Validation: case-variant responses match DB behavior.",
            ],
        )

    @api_case(
        section="Location",
        operation="POST /locations/Search",
        testrail_id="17770505",
        title="Leading/trailing spaces in filters",
    )
    def test_leading_trailing_spaces_in_filters(self, api_context, db, request):
        spaced_filters = {"country": " Canada "}
        _run_negative_case(
            case_id="17770505",
            title="Leading/trailing spaces in filters",
            payload=_payload(spaced_filters),
            allowed_statuses={200},
            api_context=api_context,
            db=db,
            request=request,
            validate_filter_count=True,
            filter_payload=spaced_filters,
        )

    @api_case(
        section="Location",
        operation="POST /locations/Search",
        testrail_id="17770506",
        title="Pagination boundary behavior",
    )
    def test_pagination_boundary_behavior(self, api_context, db, request):
        payload = _payload(page_index=9999, page_size=50)
        response = _search_locations(api_context, payload)
        _assert_status_ok(response, "Pagination boundary behavior")

        body = _safe_json(response)
        pagination = body.get("pagination", {})
        items = _api_items(body)
        db_total = _db_active_total(db)

        assert int(pagination.get("index", -1)) == 9999, (
            f"Pagination boundary behavior: expected index=9999, got {pagination.get('index')}"
        )
        assert int(pagination.get("totalRecords", -1)) == db_total, (
            f"Pagination boundary behavior: totalRecords mismatch API={pagination.get('totalRecords')} DB={db_total}"
        )
        assert len(items) == 0, "Pagination boundary behavior: expected empty items for very high page index"

        _record_dashboard_metrics(
            request,
            [
                "CaseId: 17770506",
                "Title: Pagination boundary behavior",
                f"DB target: {os.getenv('DB_SERVER', '')} / {os.getenv('DB_NAME', '')}",
                f"Request payload: {json.dumps(payload, ensure_ascii=True)}",
                f"Pagination: {json.dumps(pagination, ensure_ascii=True)}",
                f"Returned items: {len(items)}",
                f"DB active total: {db_total}",
                "Validation: high page index returns empty page while preserving totalRecords.",
            ],
        )

    @api_case(
        section="Location",
        operation="POST /locations/Search",
        testrail_id="17770507",
        title="Sort stability with nulls",
    )
    def test_sort_stability_with_nulls(self, api_context, db, request):
        payload = _payload(page_index=0, page_size=5000)
        response = _search_locations(api_context, payload)
        _assert_status_ok(response, "Sort stability with nulls")

        body = _safe_json(response)
        pagination = body.get("pagination", {})
        items = _api_items(body)

        assert int(pagination.get("totalRecords", 0)) > 0, "Sort stability with nulls: expected totalRecords > 0"
        assert items, "Sort stability with nulls: expected items list"

        null_name_count = sum(1 for item in items if item.get("locationName") is None)
        null_number_count = sum(1 for item in items if item.get("locationNumber") is None)

        # Ensure sort comparison can be performed safely even when values are null.
        normalized_pairs = [
            ((item.get("locationName") or "").lower(), (item.get("addressLine1") or "").lower())
            for item in items
        ]
        _ = sorted(normalized_pairs)

        _record_dashboard_metrics(
            request,
            [
                "CaseId: 17770507",
                "Title: Sort stability with nulls",
                f"DB target: {os.getenv('DB_SERVER', '')} / {os.getenv('DB_NAME', '')}",
                f"Request payload: {json.dumps(payload, ensure_ascii=True)}",
                f"Pagination: {json.dumps(pagination, ensure_ascii=True)}",
                f"Returned items: {len(items)}",
                f"Items with null locationName: {null_name_count}",
                f"Items with null locationNumber: {null_number_count}",
                "Validation: response remains stable and sortable when null values are present.",
            ],
        )
