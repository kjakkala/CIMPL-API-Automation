"""Publish pilot Workorder Provision test results to TestRail as a dedicated plan."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from utils.testrail_client import TestRailClient, TestRailError


PILOT_CASE_IDS = set(range(17777182, 17777284)) | {17783584}

DASHBOARD_JSON = Path(__file__).resolve().parents[1] / "reports" / "test_dashboard.json"


def _load_pilot_results() -> list[dict]:
    payload = json.loads(DASHBOARD_JSON.read_text(encoding="utf-8"))
    selected: list[dict] = []

    for section in payload.get("sections", []):
        for result in section.get("results", []):
            case_raw = str(result.get("testrail_case_id", "")).strip()
            if case_raw.isdigit() and int(case_raw) in PILOT_CASE_IDS:
                selected.append(result)

    return selected


def main():
    results = _load_pilot_results()
    found_case_ids = {
        int(str(r.get("testrail_case_id", "")).strip())
        for r in results
        if str(r.get("testrail_case_id", "")).strip().isdigit()
    }
    missing = sorted(PILOT_CASE_IDS - found_case_ids)
    if missing:
        raise RuntimeError(
            f"Missing pilot results in dashboard JSON for case IDs: {missing}. "
            "Run pilot test module first."
        )

    client = TestRailClient.from_env()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    plan_name = f"Workorder Provision Pilot - {ts}"

    section_name = "Workorders Provision Pilot"
    case_ids_map = {section_name: sorted(PILOT_CASE_IDS)}
    section_results_map = {section_name: results}

    plan = client.create_plan(plan_name, case_ids_map)
    publish = client.publish_results(plan, section_results_map)

    plan_id = plan.get("id")
    plan_url = ""
    if plan_id:
        plan_url = f"{client.config.url.rstrip('/')}/index.php?/plans/view/{plan_id}"

    print(f"Plan ID: {plan_id}")
    print(f"Plan URL: {plan_url}")
    print(f"Published results: {publish.get('published_results', 0)}")


if __name__ == "__main__":
    try:
        load_dotenv()
        main()
    except TestRailError as exc:
        raise SystemExit(f"TestRail error: {exc}") from exc
