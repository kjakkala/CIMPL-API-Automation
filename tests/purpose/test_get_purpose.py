"""
Tests for GET /purpose endpoint.
Validates API response against cimpl.Purpose and cimpl.ServiceInventory in SQL Server.
"""

from utils.test_metadata import api_case


def _extract_items(payload) -> list:
    """Return list payload from direct list or common envelope shapes."""
    if isinstance(payload, dict):
        return payload.get("items", payload.get("data", payload.get("value", [])))
    return payload


def _record_dashboard_metrics(request, db_count: int, api_count: int, extra_lines: list[str] | None = None):
    metrics = [
        f"DB records: {db_count}",
        f"API response records: {api_count}",
    ]
    if extra_lines:
        metrics.extend(extra_lines)

    request.node.user_properties.append(
        (
            "dashboard_metrics",
            "\n".join(metrics),
        )
    )


def _db_purpose_rows(db) -> list[dict]:
    query = """
        SELECT
            p.PurposeId AS purposeId,
            p.name AS name,
            p.Active AS active,
            COUNT(si.ServiceInventoryId) AS numberOfServices
        FROM cimpl.Purpose p
        LEFT JOIN cimpl.ServiceInventory si ON p.PurposeId = si.PurposeId
        GROUP BY p.PurposeId, p.name, p.Active
    """
    return db.fetch_all(query)


def _to_api_map(items: list[dict]) -> dict[int, dict]:
    return {int(item["purposeId"]): item for item in items if "purposeId" in item}


def _to_db_map(rows: list[dict]) -> dict[int, dict]:
    return {int(row["purposeId"]): row for row in rows}


class TestGetPurpose:

    @api_case(
        section="Purpose",
        operation="GET /purpose",
        testrail_id="17767742",
        title="Validate purpose id count matches DB",
    )
    def test_purpose_count_matches_db(self, api_context, db, request):
        response = api_context.get("purpose")
        assert response.ok, f"GET /purpose returned {response.status}: {response.text()}"

        items = _extract_items(response.json())
        db_rows = _db_purpose_rows(db)
        _record_dashboard_metrics(request, len(db_rows), len(items))

        assert len(items) == len(db_rows), (
            f"Purpose count mismatch: API={len(items)}, DB={len(db_rows)}"
        )

    @api_case(
        section="Purpose",
        operation="GET /purpose",
        testrail_id="17767743",
        title="Validate services count for each purpose id",
    )
    def test_services_count_per_purpose_matches_db(self, api_context, db, request):
        response = api_context.get("purpose")
        assert response.ok, f"GET /purpose returned {response.status}: {response.text()}"

        items = _extract_items(response.json())
        api_by_id = _to_api_map(items)
        db_by_id = _to_db_map(_db_purpose_rows(db))
        missing_ids = [purpose_id for purpose_id in db_by_id if purpose_id not in api_by_id]
        service_mismatches = []
        non_zero_validated = []

        for purpose_id, db_row in db_by_id.items():
            if purpose_id not in api_by_id:
                continue
            api_services = int(api_by_id[purpose_id]["numberOfServices"])
            db_services = int(db_row["numberOfServices"])
            if db_services > 0:
                non_zero_validated.append((purpose_id, db_services, api_services))
            if api_services != db_services:
                service_mismatches.append((purpose_id, db_services, api_services))

        sample = ", ".join(
            f"{purpose_id}:{api_services}"
            for purpose_id, _db_services, api_services in non_zero_validated[:25]
        )
        _record_dashboard_metrics(
            request,
            len(db_by_id),
            len(api_by_id),
            extra_lines=[
                f"PurposeIds with services > 0 validated: {len(non_zero_validated)}",
                f"Sample purposeId:numberOfServices (first 25): {sample if sample else 'None'}",
                f"Missing purposeIds in API: {len(missing_ids)}",
                f"Service count mismatches: {len(service_mismatches)}",
            ],
        )

        assert not missing_ids, (
            f"Purpose IDs missing in API response: {missing_ids[:20]}"
            f"{' ...' if len(missing_ids) > 20 else ''}"
        )
        assert not service_mismatches, (
            "Service count mismatches detected. "
            f"First mismatches: {service_mismatches[:20]}"
            f"{' ...' if len(service_mismatches) > 20 else ''}"
        )

    @api_case(
        section="Purpose",
        operation="GET /purpose",
        testrail_id="17767744",
        title="Validate active status for each purpose",
    )
    def test_active_status_per_purpose_matches_db(self, api_context, db, request):
        response = api_context.get("purpose")
        assert response.ok, f"GET /purpose returned {response.status}: {response.text()}"

        items = _extract_items(response.json())
        api_by_id = _to_api_map(items)
        db_by_id = _to_db_map(_db_purpose_rows(db))
        db_active_count = 0
        db_inactive_count = 0
        api_active_count = 0
        api_inactive_count = 0
        missing_ids = []
        status_mismatches = []

        for purpose_id, db_row in db_by_id.items():
            db_active = bool(db_row["active"])
            if db_active:
                db_active_count += 1
            else:
                db_inactive_count += 1

            if purpose_id not in api_by_id:
                missing_ids.append(purpose_id)
                continue

            api_active = bool(api_by_id[purpose_id]["active"])
            if api_active:
                api_active_count += 1
            else:
                api_inactive_count += 1

            if api_active != db_active:
                status_mismatches.append((purpose_id, db_active, api_active))

        _record_dashboard_metrics(
            request,
            len(db_by_id),
            len(api_by_id),
            extra_lines=[
                f"DB active count: {db_active_count}",
                f"DB inactive count: {db_inactive_count}",
                f"API active count: {api_active_count}",
                f"API inactive count: {api_inactive_count}",
                f"Missing purposeIds in API: {len(missing_ids)}",
                f"Active-status mismatches: {len(status_mismatches)}",
            ],
        )

        assert not missing_ids, (
            f"Purpose IDs missing in API response: {missing_ids[:20]}"
            f"{' ...' if len(missing_ids) > 20 else ''}"
        )
        assert not status_mismatches, (
            "Active status mismatches detected. "
            f"First mismatches: {status_mismatches[:20]}"
            f"{' ...' if len(status_mismatches) > 20 else ''}"
        )

        assert api_active_count == db_active_count, (
            f"Active total mismatch: API={api_active_count}, DB={db_active_count}"
        )
        assert api_inactive_count == db_inactive_count, (
            f"Inactive total mismatch: API={api_inactive_count}, DB={db_inactive_count}"
        )

    @api_case(
        section="Purpose",
        operation="GET /purpose",
        testrail_id="17767745",
        title="Validate expected response output shape",
    )
    def test_expected_response_output_shape(self, api_context, db, request):
        response = api_context.get("purpose")
        assert response.ok, f"GET /purpose returned {response.status}: {response.text()}"

        items = _extract_items(response.json())
        db_rows = _db_purpose_rows(db)
        _record_dashboard_metrics(request, len(db_rows), len(items))
        assert items, "Expected at least one purpose item"

        required_keys = {"purposeId", "name", "numberOfServices", "active"}
        for item in items:
            missing = required_keys - set(item.keys())
            assert not missing, f"Purpose item missing expected keys: {missing}. Item={item}"
            assert isinstance(item["purposeId"], int), f"purposeId must be int. Item={item}"
            assert isinstance(item["name"], str), f"name must be string. Item={item}"
            assert isinstance(item["numberOfServices"], int), (
                f"numberOfServices must be int. Item={item}"
            )
            assert item["numberOfServices"] >= 0, (
                f"numberOfServices must be non-negative. Item={item}"
            )
            assert isinstance(item["active"], bool), f"active must be bool. Item={item}"