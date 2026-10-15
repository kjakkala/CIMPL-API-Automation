import os
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from html import escape
from pathlib import Path

import pytest
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from utils.db_helper import DBHelper

load_dotenv()


def _api_timeout_ms() -> int:
    raw = os.getenv("API_REQUEST_TIMEOUT_MS", "120000").strip()
    try:
        value = int(raw)
        return value if value > 0 else 120000
    except ValueError:
        return 120000


def _marker_value(item, name: str, default: str = "") -> str:
    marker = item.get_closest_marker(name)
    if not marker:
        return default
    if marker.args:
        return str(marker.args[0])
    if "value" in marker.kwargs:
        return str(marker.kwargs["value"])
    return default


def _build_testrail_link(case_id: str) -> str:
    template = os.getenv(
        "TESTRAIL_CASE_URL_TEMPLATE",
        "https://upland.testrail.com/index.php?/cases/view/{case_id}",
    ).strip()
    if not template or not case_id:
        return ""
    return template.format(case_id=case_id)


def _rollup_case_stats(results: list[dict]) -> dict:
    """Roll up execution-level results into per-TestRail-case and per-execution counters."""
    counts = Counter(result["status"] for result in results)
    case_statuses: dict[str, str] = {}
    case_execution_counts: Counter = Counter()
    unmapped_executions = 0
    for result in results:
        case_id = (result.get("testrail_case_id") or "").strip()
        if not case_id:
            unmapped_executions += 1
            continue
        case_execution_counts[case_id] += 1
        current = case_statuses.get(case_id)
        if result["status"] == "failed":
            case_statuses[case_id] = "failed"
        elif result["status"] == "skipped" and current != "failed":
            case_statuses[case_id] = "skipped"
        elif current is None:
            case_statuses[case_id] = "passed"
    case_status_counts = Counter(case_statuses.values())
    cases_with_multiple_executions = sum(1 for n in case_execution_counts.values() if n > 1)

    return {
        "total_executions": len(results),
        "passed_executions": counts.get("passed", 0),
        "failed_executions": counts.get("failed", 0),
        "skipped_executions": counts.get("skipped", 0),
        "unmapped_executions": unmapped_executions,
        "total_cases": len(case_statuses),
        "cases_passed": case_status_counts.get("passed", 0),
        "cases_failed": case_status_counts.get("failed", 0),
        "cases_skipped": case_status_counts.get("skipped", 0),
        "cases_with_multiple_executions": cases_with_multiple_executions,
    }


def _write_dashboard(config) -> tuple[Path, Path]:
    output_dir = Path(config.getoption("--dashboard-dir")).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    results = list(getattr(config, "_dashboard_results", []))
    started_at = getattr(config, "_dashboard_started_at", datetime.now(timezone.utc))
    finished_at = datetime.now(timezone.utc)
    counts = Counter(result["status"] for result in results)
    grouped_results: dict[str, list[dict]] = defaultdict(list)
    for result in results:
        grouped_results[result["section"]].append(result)

    # A single TestRail case can be executed multiple times (parametrized scenarios,
    # positive/negative variants), so "executions" (pytest runs) and "cases" (unique
    # TestRail IDs) are tracked separately to avoid conflating the two counts.
    overall_rollup = _rollup_case_stats(results)
    by_section = {
        section: _rollup_case_stats(section_results)
        for section, section_results in sorted(grouped_results.items())
    }

    payload = {
        "generated_at": finished_at.isoformat(),
        "started_at": started_at.isoformat(),
        "summary": {
            # Legacy execution-based counters (kept for backward compatibility).
            "total": len(results),
            "passed": counts.get("passed", 0),
            "failed": counts.get("failed", 0),
            "skipped": counts.get("skipped", 0),
            **overall_rollup,
            "by_section": by_section,
        },
        "sections": [
            {
                "name": section,
                "results": section_results,
            }
            for section, section_results in sorted(grouped_results.items())
        ],
    }


    json_path = output_dir / "test_dashboard.json"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    section_breakdown_rows: list[str] = []
    for section, stats in sorted(by_section.items()):
        section_breakdown_rows.append(
            "<tr>"
            f"<td>{escape(section)}</td>"
            f"<td>{stats['total_cases']}</td>"
            f"<td>{stats['total_executions']}</td>"
            f"<td>{stats['cases_passed']}</td>"
            f"<td>{stats['passed_executions']}</td>"
            f"<td>{stats['cases_failed']}</td>"
            f"<td>{stats['failed_executions']}</td>"
            f"<td>{stats['cases_skipped']}</td>"
            f"<td>{stats['skipped_executions']}</td>"
            "</tr>"
        )

    section_html: list[str] = []
    for section, section_results in sorted(grouped_results.items()):
        rows: list[str] = []
        for result in section_results:
            case_cell = escape(result["testrail_case_id"] or "Not mapped")
            if result["testrail_case_url"]:
                case_cell = (
                    f'<a href="{escape(result["testrail_case_url"])}" '
                    'target="_blank" rel="noreferrer">'
                    f"{escape(result['testrail_case_id'])}</a>"
                )
            details = escape(result["details"] or "No details captured.")
            rows.append(
                "<tr>"
                f'<td><span class="status {escape(result["status"])}">{escape(result["status"].upper())}</span></td>'
                f"<td>{escape(result['title'])}<div class=\"nodeid\">{escape(result['nodeid'])}</div></td>"
                f"<td>{escape(result['operation'])}</td>"
                f'<td>{case_cell}</td>'
                f'<td><details><summary>View output</summary><pre>{details}</pre></details></td>'
                "</tr>"
            )
        section_html.append(
            "<section class=\"section\">"
            f"<h2>{escape(section)}</h2>"
            "<table>"
            "<thead><tr><th>Status</th><th>Test Case</th><th>Swagger Operation</th><th>TestRail</th><th>Output</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody>"
            "</table>"
            "</section>"
        )

    html_content = f"""<!DOCTYPE html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
    <title>Smart API &amp; Data Verification Dashboard - CIMPL</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f5f1e8;
      --surface: #fffdf8;
      --ink: #1d2733;
      --muted: #5e6b78;
      --border: #d8cfc1;
      --passed: #2b7a4b;
      --failed: #b33a3a;
      --skipped: #9c6b1f;
      --accent: #0f5c78;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: Georgia, \"Segoe UI\", serif; background: linear-gradient(180deg, #f4efe5 0%, #fbfaf6 100%); color: var(--ink); }}
    header {{ padding: 32px 40px 20px; border-bottom: 1px solid var(--border); background: rgba(255, 253, 248, 0.96); position: sticky; top: 0; backdrop-filter: blur(8px); }}
    h1 {{ margin: 0 0 8px; font-size: 2rem; }}
    p {{ margin: 0; color: var(--muted); }}
    main {{ padding: 24px 40px 48px; }}
    .summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 16px; margin-bottom: 24px; }}
    .card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 14px; padding: 16px 18px; box-shadow: 0 10px 30px rgba(29, 39, 51, 0.06); }}
    .card .label {{ display: block; font-size: 0.8rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; }}
    .card .value {{ display: block; margin-top: 8px; font-size: 2rem; font-weight: 700; }}
    .section {{ margin-top: 24px; background: var(--surface); border: 1px solid var(--border); border-radius: 18px; overflow: hidden; box-shadow: 0 16px 40px rgba(29, 39, 51, 0.06); }}
    .section h2 {{ margin: 0; padding: 18px 20px; background: linear-gradient(90deg, rgba(15, 92, 120, 0.12), rgba(15, 92, 120, 0.02)); border-bottom: 1px solid var(--border); }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ padding: 14px 16px; border-bottom: 1px solid var(--border); vertical-align: top; text-align: left; }}
    th {{ font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); }}
    tr:last-child td {{ border-bottom: 0; }}
    .status {{ display: inline-block; min-width: 74px; padding: 6px 10px; border-radius: 999px; font-size: 0.78rem; font-weight: 700; text-align: center; }}
    .status.passed {{ background: rgba(43, 122, 75, 0.12); color: var(--passed); }}
    .status.failed {{ background: rgba(179, 58, 58, 0.12); color: var(--failed); }}
    .status.skipped {{ background: rgba(156, 107, 31, 0.12); color: var(--skipped); }}
    .nodeid {{ margin-top: 6px; color: var(--muted); font-size: 0.82rem; word-break: break-word; }}
    details summary {{ cursor: pointer; color: var(--accent); }}
    pre {{ margin: 10px 0 0; white-space: pre-wrap; word-break: break-word; background: #f7f4ee; border: 1px solid var(--border); border-radius: 10px; padding: 12px; }}
    a {{ color: var(--accent); }}
    @media (max-width: 900px) {{
      header, main {{ padding-left: 18px; padding-right: 18px; }}
      table, thead, tbody, th, td, tr {{ display: block; }}
      thead {{ display: none; }}
      td {{ padding-top: 10px; padding-bottom: 10px; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>Smart API &amp; Data Verification Dashboard - CIMPL</h1>
    <p>Grouped by Swagger section with TestRail mapping and captured pytest output.</p>
  </header>
  <main>
    <section class=\"summary\">
      <div class=\"card\"><span class=\"label\">Generated</span><span class=\"value\">{escape(finished_at.strftime('%Y-%m-%d %H:%M:%S UTC'))}</span></div>
      <div class=\"card\"><span class=\"label\">Total Cases</span><span class=\"value\">{payload['summary']['total_cases']}</span></div>
      <div class=\"card\"><span class=\"label\">Cases Passed</span><span class=\"value\">{payload['summary']['cases_passed']}</span></div>
      <div class=\"card\"><span class=\"label\">Cases Failed</span><span class=\"value\">{payload['summary']['cases_failed']}</span></div>
      <div class=\"card\"><span class=\"label\">Cases Skipped</span><span class=\"value\">{payload['summary']['cases_skipped']}</span></div>
    </section>
    <section class=\"summary\">
      <div class=\"card\"><span class=\"label\">Total Executions</span><span class=\"value\">{len(results)}</span></div>
      <div class=\"card\"><span class=\"label\">Passed</span><span class=\"value\">{counts.get('passed', 0)}</span></div>
      <div class=\"card\"><span class=\"label\">Failed</span><span class=\"value\">{counts.get('failed', 0)}</span></div>
      <div class=\"card\"><span class=\"label\">Skipped</span><span class=\"value\">{counts.get('skipped', 0)}</span></div>
      <div class=\"card\"><span class=\"label\">Cases w/ Multiple Runs</span><span class=\"value\">{payload['summary']['cases_with_multiple_executions']}</span></div>
    </section>
    <p style=\"margin: -8px 0 24px; font-size: 0.85rem; color: var(--muted);\">
      "Cases" are unique TestRail case IDs; "Executions" are pytest runs (a single case can run under multiple scenarios, e.g. valid/invalid variants).
    </p>
    <section class=\"section\">
      <h2>Summary by Section</h2>
      <table>
        <thead><tr><th>Section</th><th>Cases</th><th>Executions</th><th>Cases Passed</th><th>Exec Passed</th><th>Cases Failed</th><th>Exec Failed</th><th>Cases Skipped</th><th>Exec Skipped</th></tr></thead>
        <tbody>{''.join(section_breakdown_rows) if section_breakdown_rows else '<tr><td colspan="9">No sections yet.</td></tr>'}</tbody>
      </table>
    </section>
    {''.join(section_html) if section_html else '<p>No test results were collected.</p>'}
  </main>
</body>
</html>
"""

    html_path = output_dir / "test_dashboard.html"
    html_path.write_text(html_content, encoding="utf-8")
    return html_path, json_path


def pytest_addoption(parser):
    parser.addoption(
        "--dashboard-dir",
        action="store",
        default="reports",
        help="Directory where the JSON and HTML test dashboard artifacts are written.",
    )


def pytest_configure(config):
    config._dashboard_results = []
    config._dashboard_started_at = datetime.now(timezone.utc)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when not in {"call", "setup"}:
        return
    if report.when == "setup" and report.passed:
        return

    status = "passed"
    if report.failed:
        status = "failed"
    elif report.skipped:
        status = "skipped"

    metrics_text = ""
    for key, value in getattr(report, "user_properties", []):
        if key == "dashboard_metrics" and value:
            metrics_text = str(value)
            break

    failure_text = getattr(report, "longreprtext", "") if status != "passed" else ""
    if metrics_text and failure_text:
        details_text = f"{metrics_text}\n\n{failure_text}"
    elif metrics_text:
        details_text = metrics_text
    elif failure_text:
        details_text = failure_text
    else:
        details_text = "Assertion checks passed."

    result = {
        "nodeid": item.nodeid,
        "title": _marker_value(item, "dashboard_title", item.name),
        "section": _marker_value(item, "swagger_section", item.path.parent.name.title()),
        "operation": _marker_value(item, "swagger_operation", item.name),
        "testrail_case_id": _marker_value(item, "testrail_case"),
        "status": status,
        "details": details_text,
    }
    result["testrail_case_url"] = _build_testrail_link(result["testrail_case_id"])
    item.config._dashboard_results.append(result)


def pytest_sessionfinish(session, exitstatus):
    html_path, json_path = _write_dashboard(session.config)
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter:
        reporter.write_line(f"dashboard html: {html_path}")
        reporter.write_line(f"dashboard json: {json_path}")


@pytest.fixture(scope="session")
def api_token() -> str:
    """Fetch an OAuth2 bearer token once per test session."""
    token_url = os.environ["TOKEN_URL"]
    grant_type = os.getenv("GRANT_TYPE", "password")
    timeout_ms = _api_timeout_ms()

    payload = {
        "grant_type": grant_type,
        "client_id": os.environ["CLIENT_ID"],
        "client_secret": os.environ["CLIENT_SECRET"],
    }
    if grant_type == "password":
        payload["username"] = os.environ["API_USERNAME"]
        payload["password"] = os.environ["API_PASSWORD"]

    with sync_playwright() as p:
        request_context = p.request.new_context()
        response = request_context.post(token_url, form=payload, timeout=timeout_ms)
        assert response.ok, f"Token request failed: {response.status} {response.text()}"
        token = response.json()["access_token"]
        request_context.dispose()

    return token


@pytest.fixture(scope="session")
def api_context(api_token):
    """Playwright APIRequestContext with auth header pre-set for the whole session."""
    base_url = os.environ["BASE_URL"].rstrip("/") + "/"
    timeout_ms = _api_timeout_ms()
    with sync_playwright() as p:
        context = p.request.new_context(
            base_url=base_url,
            extra_http_headers={"Authorization": f"Bearer {api_token}"},
            timeout=timeout_ms,
        )
        yield context
        context.dispose()


@pytest.fixture(scope="session")
def db():
    """SQL Server connection available for the whole session."""
    helper = DBHelper()
    yield helper
    helper.close()
