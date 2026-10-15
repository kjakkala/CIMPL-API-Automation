"""Publish POST /workorders/support results to a dedicated TestRail plan."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.workorders.test_post_workorders_support import CASES
from utils.testrail_client import TestRailClient, TestRailError


CASE_IDS = {int(case.case_id) for case in CASES}
DASHBOARD_JSON = ROOT / "reports" / "test_dashboard.json"
SECTION_NAME = "Workorders Support"


def _load_results() -> list[dict]:
    payload = json.loads(DASHBOARD_JSON.read_text(encoding="utf-8"))
    return [
        result
        for section in payload.get("sections", [])
        for result in section.get("results", [])
        if str(result.get("testrail_case_id", "")).strip().isdigit()
        and int(str(result["testrail_case_id"]).strip()) in CASE_IDS
    ]


def main():
    results = _load_results()
    result_ids = {int(str(result["testrail_case_id"]).strip()) for result in results}
    missing = sorted(CASE_IDS - result_ids)
    if missing:
        raise RuntimeError(
            f"Missing Support dashboard results for TestRail cases: {missing}. "
            "Run tests/workorders/test_post_workorders_support.py first."
        )

    client = TestRailClient.from_env()
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    plan = client.create_plan(
        f"Workorder Support API - {timestamp}",
        {SECTION_NAME: sorted(CASE_IDS)},
    )
    published = client.publish_results(plan, {SECTION_NAME: results})
    plan_id = plan.get("id")
    plan_url = f"{client.config.url.rstrip('/')}/index.php?/plans/view/{plan_id}" if plan_id else ""
    print(f"Plan ID: {plan_id}")
    print(f"Plan URL: {plan_url}")
    print(f"Published results: {published.get('published_results', 0)}")


if __name__ == "__main__":
    try:
        load_dotenv()
        main()
    except TestRailError as exc:
        raise SystemExit(f"TestRail error: {exc}") from exc