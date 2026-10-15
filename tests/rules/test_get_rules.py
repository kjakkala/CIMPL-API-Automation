"""
Tests for GET /Rules endpoint.
Validates API response against cimpl.[Rule] table in SQL Server (Windows Auth).

Endpoints:
  GET /Rules?activeOnly=true   → active rules   (DB: active = 1)
  GET /Rules?activeOnly=false  → inactive rules  (DB: active = 0)
  GET /Rules                   → all rules
"""

from utils.test_metadata import api_case


def _extract_items(payload) -> list:
    """Return the list of rule objects from either a plain list or paginated envelope."""
    if isinstance(payload, dict):
        return payload.get("items", payload.get("data", payload.get("value", [])))
    return payload


def _assert_rule_has_details(rule: dict):
    """Validate that a rule includes core detail fields expected by TestRail cases."""
    required_fields = [
        "ruleId",
        "tenantId",
        "ruleName",
        "ruleTypeId",
        "ruleTypeName",
        "active",
        "deleted",
        "modifiedDate",
    ]
    missing = [field for field in required_fields if field not in rule]
    assert not missing, f"Rule is missing expected detail fields: {missing}. Rule={rule}"


def _record_dashboard_metrics(request, db_count: int, api_count: int):
    request.node.user_properties.append(
        (
            "dashboard_metrics",
            f"DB records: {db_count}\nAPI response records: {api_count}",
        )
    )


class TestGetRulesBehavior:

    @api_case(
        section="Rules",
        operation="GET /Rules",
        testrail_id="17766632",
        title="No parameter returns active rule details",
    )
    def test_no_parameter_returns_all_details(self, api_context, db, request):
        response = api_context.get("Rules")
        assert response.ok, f"GET /Rules returned {response.status}: {response.text()}"

        items = _extract_items(response.json())
        assert items, "Expected rules list when no parameter is passed"
        db_count = db.fetch_scalar("SELECT COUNT(*) FROM cimpl.[Rule] WHERE active = 1 AND deleted = 0")
        api_count = len(items)
        _record_dashboard_metrics(request, db_count, api_count)
        assert api_count == db_count, (
            f"No-parameter count mismatch: API={api_count}, DB={db_count}"
        )

        for rule in items:
            _assert_rule_has_details(rule)
            assert rule.get("active") is True, (
                f"Rule {rule.get('ruleId')} has active={rule.get('active')}, expected True"
            )

    @api_case(
        section="Rules",
        operation="GET /Rules?activeOnly=false",
        testrail_id="17766633",
        title="activeOnly=false returns all rule details",
    )
    def test_active_false_returns_all_details(self, api_context, db, request):
        response = api_context.get("Rules", params={"activeOnly": "false"})
        assert response.ok, f"GET /Rules?activeOnly=false returned {response.status}: {response.text()}"

        items = _extract_items(response.json())
        assert items, "Expected rules list for activeOnly=false"
        db_count = db.fetch_scalar("SELECT COUNT(*) FROM cimpl.[Rule] WHERE deleted = 0")
        api_count = len(items)
        _record_dashboard_metrics(request, db_count, api_count)
        assert api_count == db_count, (
            f"activeOnly=false count mismatch: API={api_count}, DB={db_count}"
        )

        for rule in items:
            _assert_rule_has_details(rule)

    @api_case(
        section="Rules",
        operation="GET /Rules?activeOnly=true",
        testrail_id="17766634",
        title="activeOnly=true returns active rule details",
    )
    def test_active_true_returns_only_active_details(self, api_context, db, request):
        response = api_context.get("Rules", params={"activeOnly": "true"})
        assert response.ok, f"GET /Rules?activeOnly=true returned {response.status}: {response.text()}"

        items = _extract_items(response.json())
        assert items, "Expected active rules list for activeOnly=true"
        db_count = db.fetch_scalar("SELECT COUNT(*) FROM cimpl.[Rule] WHERE active = 1 AND deleted = 0")
        api_count = len(items)
        _record_dashboard_metrics(request, db_count, api_count)
        assert api_count == db_count, (
            f"activeOnly=true count mismatch: API={api_count}, DB={db_count}"
        )

        for rule in items:
            _assert_rule_has_details(rule)
            assert rule.get("active") is True, (
                f"Rule {rule.get('ruleId')} has active={rule.get('active')}, expected True"
            )
