"""Tests for GET /translations/{cultureCode} with DB-backed validation."""

from __future__ import annotations

import json
import os

import pytest

from utils.test_metadata import api_case


def _record_dashboard_metrics(request, lines: list[str]):
    request.node.user_properties.append(("dashboard_metrics", "\n".join(lines)))


def _safe_json(response):
    try:
        payload = response.json()
        return payload if isinstance(payload, dict) else {"raw": payload}
    except Exception:  # noqa: BLE001
        return {}


def _normalize_culture_for_db(culture_code: str) -> str:
    culture = (culture_code or "").strip().lower()
    if not culture:
        return culture
    if "-" in culture:
        return culture.split("-", 1)[0]
    return culture


def _resolve_culture_for_table(db, table_name: str, culture_code: str) -> str:
    exact = db.fetch_scalar(
        f"SELECT COUNT(1) FROM cimpl.{table_name} WHERE LOWER(Culture) = ?",
        (culture_code.lower(),),
    )
    if int(exact or 0) > 0:
        return culture_code.lower()
    return _normalize_culture_for_db(culture_code)


def _db_translation_rows_for_table(db, table_name: str, culture_code: str) -> tuple[str, list[dict]]:
    assert table_name in {"Translations", "Translations_Client"}, f"Unexpected table name: {table_name}"
    resolved_culture = _resolve_culture_for_table(db, table_name, culture_code)
    rows = db.fetch_all(
        f"""
        SELECT
            Culture AS culture,
            TranslationKey AS translationKey,
            TranslationValue AS translationValue,
            SpecificityLevel AS specificityLevel
        FROM cimpl.{table_name}
        WHERE LOWER(Culture) = ?
        ORDER BY TranslationKey
        """,
        (resolved_culture,),
    )
    return resolved_culture, rows


def _db_translation_map(db, culture_code: str) -> tuple[str, dict[str, str]]:
    base_culture, base_rows = _db_translation_rows_for_table(db, "Translations", culture_code)
    client_culture, client_rows = _db_translation_rows_for_table(db, "Translations_Client", culture_code)

    mapping: dict[str, str] = {}
    # Base translation dictionary.
    for row in base_rows:
        key = str(row["translationKey"]).strip()
        value = str(row["translationValue"]) if row["translationValue"] is not None else ""
        mapping[key] = value

    # Client translations override base values for duplicate keys and add new keys.
    for row in client_rows:
        key = str(row["translationKey"]).strip()
        value = str(row["translationValue"]) if row["translationValue"] is not None else ""
        mapping[key] = value

    resolved = base_culture
    if not resolved and client_culture:
        resolved = client_culture
    return resolved, mapping


def _normalized_api_translation_map(api_payload: dict) -> tuple[dict[str, str], list[str]]:
    normalized: dict[str, str] = {}
    collisions: list[str] = []
    for key, value in api_payload.items():
        norm_key = str(key).strip()
        norm_value = str(value) if value is not None else ""
        if norm_key in normalized and normalized[norm_key] != norm_value:
            collisions.append(norm_key)
        normalized[norm_key] = norm_value
    return normalized, collisions


class TestGetTranslations:

    @api_case(
        section="Translations",
        operation="GET /translations/{cultureCode}",
        testrail_id="17775558",
        title="Validate Invalid culture code is passed",
    )
    def test_invalid_culture_code(self, api_context, request):
        culture_code = "invalid-culture"
        response = api_context.get(f"translations/{culture_code}", headers={"accept": "text/plain"})
        body = _safe_json(response)

        assert response.status == 400, (
            f"Invalid culture should return 400. Got {response.status}. Response={response.text()}"
        )

        message = json.dumps(body, ensure_ascii=True) if body else response.text()
        assert "not valid" in message.lower(), (
            f"Expected validation message for invalid culture code. Response={message}"
        )

        _record_dashboard_metrics(
            request,
            [
                "CaseId: 17775558",
                "Title: Validate Invalid culture code is passed",
                f"Request path: /v1/translations/{culture_code}",
                f"Response status: {response.status}",
                f"Response body: {message}",
                "Validation: invalid culture code rejected with validation error.",
            ],
        )

    @api_case(
        section="Translations",
        operation="GET /translations/{cultureCode}",
        testrail_id="17775559",
        title="Validate response with valid currency code is passed",
    )
    def test_valid_culture_code_db_match(self, api_context, db, request):
        # API accepts regional code and resolves to culture rows in DB (e.g., en-US -> en).
        culture_code = "en-US"
        response = api_context.get(f"translations/{culture_code}", headers={"accept": "text/plain"})
        assert response.status == 200, (
            f"Valid culture should return 200. Got {response.status}. Response={response.text()}"
        )

        api_payload = response.json()
        assert isinstance(api_payload, dict), (
            f"Expected translations response to be object/dictionary. Got type={type(api_payload)}"
        )

        resolved_culture, db_map = _db_translation_map(db, culture_code)
        assert db_map, f"DB returned no translation rows for culture '{culture_code}' (resolved='{resolved_culture}')"

        api_map, api_collisions = _normalized_api_translation_map(api_payload)
        assert not api_collisions, (
            f"API contains duplicate keys after trim-normalization with different values: {api_collisions[:20]}"
        )

        api_keys = set(api_map.keys())
        db_keys = set(db_map.keys())
        missing_db_keys = sorted(db_keys - api_keys)
        extra_api_keys = sorted(api_keys - db_keys)

        mismatches = []
        for key, db_value in db_map.items():
            api_value = api_map.get(key)
            if str(api_value) != str(db_value):
                mismatches.append((key, db_value, api_value))

        _record_dashboard_metrics(
            request,
            [
                "CaseId: 17775559",
                "Title: Validate response with valid currency code is passed",
                f"DB target: {os.getenv('DB_SERVER', '')} / {os.getenv('DB_NAME', '')}",
                f"Request path: /v1/translations/{culture_code}",
                f"Resolved DB culture: {resolved_culture}",
                f"API status: {response.status}",
                f"DB translation row count: {len(db_map)}",
                f"API translation item count: {len(api_payload)}",
                f"API normalized unique key count: {len(api_map)}",
                f"Missing DB keys in API: {len(missing_db_keys)}",
                f"API extra keys not in DB row set: {len(extra_api_keys)}",
                f"Value mismatches: {len(mismatches)}",
                f"Missing key sample (first 25): {missing_db_keys[:25]}",
                f"API extra key sample (first 25): {extra_api_keys[:25]}",
                f"Value mismatch sample (first 25): {mismatches[:25]}",
                f"Sample DB keys (first 25): {list(sorted(db_keys))[:25]}",
            ],
        )

        assert not missing_db_keys, (
            "API response is missing translation keys present in DB. "
            f"Missing sample={missing_db_keys[:20]}"
        )
        assert len(api_payload) >= len(db_map), (
            "API translation count should be greater than or equal to DB translation count for resolved culture. "
            f"API={len(api_payload)} DB={len(db_map)}"
        )

        assert not mismatches, (
            f"Translation value mismatches found. First mismatches={mismatches[:20]}"
        )

    @api_case(
        section="Translations",
        operation="GET /translations/{cultureCode}",
        testrail_id="17775560",
        title="Validate when no currency code is passed",
    )
    def test_no_culture_code_passed(self, api_context, request):
        response = api_context.get("translations/", headers={"accept": "text/plain"})
        body = _safe_json(response)

        assert response.status in {400, 404}, (
            f"Missing culture code should return 400/404. Got {response.status}. Response={response.text()}"
        )

        _record_dashboard_metrics(
            request,
            [
                "CaseId: 17775560",
                "Title: Validate when no currency code is passed",
                "Request path: /v1/translations/",
                f"Response status: {response.status}",
                f"Response body: {json.dumps(body, ensure_ascii=True) if body else response.text()}",
                "Validation: request without culture code is rejected.",
            ],
        )
