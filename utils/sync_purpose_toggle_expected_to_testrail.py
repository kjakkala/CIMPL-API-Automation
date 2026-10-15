"""Sync Purpose toggle expected results to TestRail from latest dashboard output."""

from __future__ import annotations

import json
from pathlib import Path

from dotenv import load_dotenv

from utils.testrail_client import TestRailClient, TestRailError


TOGGLE_CASE_IDS = {
    17777284,
    17777285,
    17777286,
    17777287,
    17777288,
    17777289,
    17777290,
    17777291,
    17777292,
    17777293,
    17777294,
    17777295,
    17777296,
    17777297,
    17777298,
    17777299,
    17777300,
    17777301,
}

NEGATIVE_CASE_IDS = {
    17777287,
    17777288,
    17777289,
    17777290,
    17777291,
    17777292,
    17777293,
    17777297,
    17777298,
    17777301,
}

SUCCESS_CASE_IDS = {
    17777284,
    17777285,
    17777286,
    17777294,
    17777295,
    17777296,
    17777299,
    17777300,
}

DASHBOARD_JSON = Path(__file__).resolve().parents[1] / "reports" / "test_dashboard.json"


def _load_dashboard_results() -> dict[int, dict]:
    if not DASHBOARD_JSON.exists():
        raise RuntimeError(f"Dashboard file not found: {DASHBOARD_JSON}")

    payload = json.loads(DASHBOARD_JSON.read_text(encoding="utf-8"))
    by_case: dict[int, dict] = {}

    for section in payload.get("sections", []):
        for result in section.get("results", []):
            case_raw = str(result.get("testrail_case_id", "")).strip()
            if not case_raw.isdigit():
                continue
            case_id = int(case_raw)
            if case_id not in TOGGLE_CASE_IDS:
                continue
            by_case[case_id] = result

    return by_case


def _extract_validation_message(details: str) -> str:
    for line in details.splitlines():
        if line.lower().startswith("validation message:"):
            return line.split(":", 1)[1].strip()
    return "n/a"


def _extract_response_status(details: str) -> str:
    for line in details.splitlines():
        if line.lower().startswith("response status:"):
            return line.split(":", 1)[1].strip()
    return "n/a"


def _build_expected(case_id: int, title: str, details: str) -> str:
    status = _extract_response_status(details)
    validation_msg = _extract_validation_message(details)

    if case_id in NEGATIVE_CASE_IDS:
        return (
            f"Response code should be validation/security style (400/401/403/404/405 as implemented; latest observed: {status}). "
            f"Expected validation/security message: {validation_msg}. "
            "Expected DB outcome: cimpl.Purpose.Active is not unintentionally changed."
        )

    if case_id == 17777284:
        return (
            "Response code should be 200/201. "
            "Expected DB value: cimpl.Purpose.Active becomes false for the targeted purposeId."
        )

    if case_id == 17777285:
        return (
            "Response code should be 200/201. "
            "Expected DB value: cimpl.Purpose.Active becomes true for the targeted purposeId."
        )

    if case_id in {17777286, 17777300}:
        return (
            "Response code should be 200/201 for each toggle call. "
            "Expected DB value: Active flips on each call and final Active equals original value after two calls."
        )

    if case_id == 17777294:
        return (
            "Response code should be 200/201. "
            "Expected response contains generic envelope fields (for example request/message/error keys as implemented)."
        )

    if case_id == 17777295:
        return (
            "Response code should be 200/201. "
            "Expected DB outcome: cimpl.Purpose row count remains unchanged and Active is toggled for target purposeId."
        )

    if case_id == 17777296:
        return (
            "Response code should be 200/201 for concurrent calls. "
            "Expected DB value: final Active state is parity-based and no data inconsistency occurs."
        )

    if case_id == 17777299:
        return (
            "Response code should be 200/201. "
            "Expected performance: response within SLA threshold used by automation. "
            "Expected DB value: Active toggles correctly."
        )

    if case_id in SUCCESS_CASE_IDS:
        return (
            "Response code should be 200/201. "
            "Expected DB value: cimpl.Purpose.Active is toggled as required by the case."
        )

    return (
        "PATCH /v1/purpose/{purposeId}/toggle follows expected API behavior and response contract; "
        "verify status and DB state according to case intent."
    )


def _target_expected_field(case_payload: dict) -> str:
    for key in ("custom_expected", "custom_expectedresult", "custom_expected_result"):
        if key in case_payload:
            return key
    raise RuntimeError(
        "TestRail case does not expose a known expected-result field. "
        "Checked: custom_expected, custom_expectedresult, custom_expected_result"
    )


def _target_steps_field(case_payload: dict) -> str:
    for key in ("custom_steps", "custom_steps_separated"):
        if key in case_payload:
            return key
    raise RuntimeError(
        "TestRail case does not expose a known steps field. "
        "Checked: custom_steps, custom_steps_separated"
    )


def _build_steps(case_id: int, title: str, details: str):
    if case_id in NEGATIVE_CASE_IDS:
        if case_id == 17777297:
            return "Call PATCH /v1/purpose/{purposeId}/toggle without Authorization token."
        if case_id == 17777298:
            return "Call PATCH /v1/purpose/{purposeId}/toggle with invalid Authorization token."
        if case_id == 17777292:
            return "Call PATCH /v1/purpose//toggle (missing purposeId in path)."
        if case_id == 17777289:
            return "Call PATCH /v1/purpose/abc/toggle (non-numeric purposeId)."
        if case_id == 17777290:
            return "Call PATCH /v1/purpose/10.5/toggle (decimal purposeId)."
        if case_id == 17777293:
            return "Call PATCH /v1/purpose/@#/toggle (special characters in purposeId)."
        if case_id == 17777287:
            return "Call PATCH /v1/purpose/0/toggle."
        if case_id == 17777288:
            return "Call PATCH /v1/purpose/-1/toggle."
        if case_id == 17777291:
            return "Call PATCH /v1/purpose/{nonExistingPurposeId}/toggle."
        if case_id == 17777301:
            return "Call PATCH /v1/purpose/2147483647/toggle (large integer purposeId)."
        steps_text = (
            "Call PATCH /v1/purpose/{purposeId}/toggle using the negative/validation input defined for this case."
        )
    elif case_id == 17777284:
        steps_text = "Call PATCH /v1/purpose/{purposeId}/toggle where input purpose has Active=true."
    elif case_id == 17777285:
        steps_text = "Call PATCH /v1/purpose/{purposeId}/toggle where input purpose has Active=false."
    elif case_id == 17777286 or case_id == 17777300:
        steps_text = (
            "Call PATCH /v1/purpose/{purposeId}/toggle twice for the same valid purposeId."
        )
    elif case_id == 17777296:
        steps_text = (
            "Call PATCH /v1/purpose/{purposeId}/toggle concurrently using the same valid purposeId."
        )
    elif case_id == 17777299:
        steps_text = "Call PATCH /v1/purpose/{purposeId}/toggle with a valid purposeId and capture response time."
    else:
        steps_text = "Call PATCH /v1/purpose/{purposeId}/toggle with a valid purposeId."

    return steps_text


def main():
    case_results = _load_dashboard_results()
    missing = sorted(TOGGLE_CASE_IDS - set(case_results.keys()))
    if missing:
        raise RuntimeError(
            f"Missing toggle case outputs in dashboard JSON for case IDs: {missing}. "
            "Run toggle pytest suite first."
        )

    client = TestRailClient.from_env()

    updated = 0
    expected_updated = 0
    steps_updated = 0
    for case_id in sorted(TOGGLE_CASE_IDS):
        result = case_results[case_id]
        title = str(result.get("title", "")).strip()
        details = str(result.get("details", "")).strip()

        current_case = client.get_case(case_id)
        expected_field = _target_expected_field(current_case)
        steps_field = _target_steps_field(current_case)
        new_expected = _build_expected(case_id, title, details)
        new_steps = _build_steps(case_id, title, details)

        current_value = str(current_case.get(expected_field, "") or "").strip()
        current_steps = current_case.get(steps_field, "")
        if isinstance(current_steps, list):
            current_steps_text = json.dumps(current_steps, ensure_ascii=True)
            new_steps_payload = [{"content": "Execution Steps", "expected": new_steps}]
            new_steps_text = json.dumps(new_steps_payload, ensure_ascii=True)
        else:
            current_steps_text = str(current_steps or "").strip()
            new_steps_payload = new_steps
            new_steps_text = str(new_steps).strip()

        payload = {}
        if current_value != new_expected:
            payload[expected_field] = new_expected
        if current_steps_text != new_steps_text:
            payload[steps_field] = new_steps_payload

        if not payload:
            print(f"C{case_id}: unchanged")
            continue

        client.update_case(case_id, payload)
        updated += 1
        if expected_field in payload:
            expected_updated += 1
        if steps_field in payload:
            steps_updated += 1
        updated_fields = ", ".join(payload.keys())
        print(f"C{case_id}: updated {updated_fields}")

    print(
        "Done. Updated "
        f"{updated} TestRail cases "
        f"(expected fields: {expected_updated}, steps fields: {steps_updated})."
    )


if __name__ == "__main__":
    try:
        load_dotenv()
        main()
    except TestRailError as exc:
        raise SystemExit(f"TestRail error: {exc}") from exc
