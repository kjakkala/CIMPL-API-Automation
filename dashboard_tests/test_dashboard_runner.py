from types import SimpleNamespace

import dashboard_server
from playwright.sync_api import sync_playwright
from utils.testrail_client import TestRailClient as RailClient, TestRailConfig as RailConfig


def test_invoice_empty_table_skips_before_seed_lookup():
    import pytest
    from tests.invoice_intelligence.test_post_get_invoice_narrative import _load_rows, EMPTY_MESSAGE

    def unexpected_lookup(query):
        raise AssertionError("Empty table must not trigger seed lookup")

    database = SimpleNamespace(fetch_scalar=lambda query: 0, fetch_all=unexpected_lookup)
    with pytest.raises(pytest.skip.Exception, match="No data available") as skipped:
        _load_rows(database)
    assert str(skipped.value) == EMPTY_MESSAGE


def test_invoice_dashboard_scope_resolves_and_groups_under_payment_readiness(monkeypatch):
    target = dashboard_server._pytest_target("Invoice Intelligence")
    assert target == dashboard_server.ROOT_DIR / "tests" / "invoice_intelligence"
    assert dashboard_server.NAVIGATION_GROUPS["Payment Readiness"] == ("Invoice Intelligence",)
    counts = dashboard_server._navigation_counts({"summary": {"by_section": {
        "Invoice Intelligence": {"cases_passed": 8, "total_cases": 8},
    }}})
    assert counts["Payment Readiness"] == {"passed": 8, "total": 8}
    monkeypatch.setattr(dashboard_server, "_load_dashboard_data", lambda: {"sections": [], "summary": {"by_section": {}}})
    monkeypatch.setattr(dashboard_server, "_historical_failure_counts", lambda data: {})
    monkeypatch.setattr(dashboard_server, "_connection_health", lambda: {"db": {}, "testrail": {}})
    monkeypatch.setattr(dashboard_server, "_discover_sections", lambda data: ["Invoice Intelligence"])
    response = dashboard_server.app.test_client().get("/")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert 'href="/scheduler"' in page
    assert ">Scheduler</a>" in page
    assert 'id="payment-readiness-nav-group"' in page
    assert 'data-tab-target="section-invoice_intelligence-tab"' in page
    assert "Payment Readiness" in page
    assert 'id="invoice-intelligence-nav-group"' in page
    assert 'data-action-scope="action:test_post_get_invoice_narrative.py"' in page
    assert 'data-tab-target="invoice-intelligence-action-page-1"' in page
    assert 'id="invoice-intelligence-action-page-1"' in page
    assert 'value="action:test_post_get_invoice_narrative.py"' in page
    assert "Run IIT02" in page


def test_qa_robot_only_moves_while_a_run_is_active(monkeypatch):
    monkeypatch.setattr(dashboard_server, "_load_dashboard_data", lambda: {"sections": [], "summary": {"by_section": {}}})
    monkeypatch.setattr(dashboard_server, "_historical_failure_counts", lambda data: {})
    monkeypatch.setattr(dashboard_server, "_connection_health", lambda: {"db": {}, "testrail": {}})
    monkeypatch.setattr(dashboard_server, "_discover_sections", lambda data: ["Purpose"])
    monkeypatch.setattr(dashboard_server, "_run_lock", SimpleNamespace(locked=lambda: True))
    monkeypatch.setattr(dashboard_server, "_load_last_run", lambda: {
        "status": "running",
        "scope": "Purpose",
        "current_section": "Purpose",
        "environment": "qa-main",
    })

    with dashboard_server.app.test_client() as client:
        running_html = client.get("/").get_data(as_text=True)
        assert 'class="qa-runner-track"' in running_html
        assert 'id="qa-runner"' in running_html
        assert 'src="/static/qa-rover.svg"' in running_html
        assert "Checking Purpose" in running_html
        assert "qaRunner.classList.add('is-launching')" in running_html
        image = client.get("/static/qa-rover.svg")
        assert image.status_code == 200
        assert b"prefers-reduced-motion" in image.data

        monkeypatch.setattr(dashboard_server, "_load_last_run", lambda: {"status": "idle"})
        idle_html = client.get("/").get_data(as_text=True)
        assert 'class="qa-runner-track"' not in idle_html


def test_invoice_action_page_renders_own_counts_details_and_nested_navigation(monkeypatch):
    operation = "POST /v1/invoice-intelligence/GetInvoiceNarrative"
    data = {"sections": [{"name": "Invoice Intelligence", "results": [
        {"operation": operation, "testrail_case_id": "1", "status": "passed", "title": "IIT02 case", "details": "IIT02 matched with DB"},
        {"operation": "POST /v1/invoice-intelligence/OtherOperation", "testrail_case_id": "2", "status": "failed", "title": "Sibling case", "details": "Sibling details"},
    ]}], "summary": {"by_section": {}}}
    monkeypatch.setattr(dashboard_server, "_load_dashboard_data", lambda: data)
    monkeypatch.setattr(dashboard_server, "_historical_failure_counts", lambda data: {})
    monkeypatch.setattr(dashboard_server, "_connection_health", lambda: {"db": {}, "testrail": {}})
    monkeypatch.setattr(dashboard_server, "_discover_sections", lambda data: ["Invoice Intelligence"])
    response = dashboard_server.app.test_client().get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.route("http://invoice.test/", lambda route: route.fulfill(content_type="text/html", body=html))
        page.route("http://invoice.test/api/run-status", lambda route: route.fulfill(json={"status": "idle"}))
        page.goto("http://invoice.test/")
        link = page.locator('#payment-readiness-nav-group #invoice-intelligence-nav-group [data-action-scope="action:test_post_get_invoice_narrative.py"]')
        assert link.locator(".nav-count").inner_text() == "1/1"
        link.click()
        panel = page.locator("#invoice-intelligence-action-page-1")
        assert panel.is_visible()
        assert panel.locator(".summary-item").first.inner_text() == "Total\n1 cases · 1 runs"
        assert panel.locator("tbody tr").count() == 1
        assert "IIT02 matched with DB" in panel.text_content()
        assert "Sibling details" not in panel.text_content()
        assert panel.get_by_role("button", name="Show Cases").is_visible()
        for width, height in ((1280, 800), (390, 844)):
            page.set_viewport_size({"width": width, "height": height})
            layouts = []
            for target in ("section-invoice_intelligence-tab", "invoice-intelligence-action-page-1"):
                page.locator(f'[data-tab-target="{target}"]').evaluate("button => button.click()")
                layout = page.locator(f"#{target}").evaluate("""panel => {
                    const card = panel.querySelector(':scope > .card');
                    const header = card.querySelector('.section-head');
                    const summary = card.querySelector('.summary-grid');
                    const form = header.querySelector('form');
                    return {
                        padding: getComputedStyle(card).padding,
                        gap: getComputedStyle(header).gap,
                        font: getComputedStyle(header.querySelector('h2')).fontSize,
                        headerX: header.getBoundingClientRect().x,
                        summaryX: summary.getBoundingClientRect().x,
                        formX: form.getBoundingClientRect().x,
                        cardWidth: card.getBoundingClientRect().width,
                        overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth
                    };
                }""")
                assert not layout["overflow"]
                assert layout["headerX"] == layout["summaryX"] == layout["formX"]
                layouts.append(layout)
            assert layouts[0] == layouts[1]
        browser.close()


def test_invoice_action_scope_routes_runner_and_testrail_to_invoice_intelligence(monkeypatch):
    action_file = "test_post_get_invoice_narrative.py"
    scope = f"action:{action_file}"
    target = dashboard_server.ROOT_DIR / "tests" / "invoice_intelligence" / action_file
    assert dashboard_server._pytest_target(scope) == target.resolve()
    assert dashboard_server._testrail_scope(scope) == "Invoice Intelligence"
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(dashboard_server.subprocess, "run", fake_run)
    dashboard_server._run_pytest(scope)
    assert captured["command"] == [dashboard_server.sys.executable, "-m", "pytest", str(target), "-v"]


def test_invoice_action_counts_and_details_exclude_siblings_and_roll_up_cases(monkeypatch):
    operation = "POST /v1/invoice-intelligence/GetInvoiceNarrative"
    sibling = "POST /v1/invoice-intelligence/OtherOperation"
    monkeypatch.setattr(dashboard_server, "INVOICE_INTELLIGENCE_ACTIONS", {
        "IIT02": {"operation": operation, "file": "test_post_get_invoice_narrative.py"},
        "IIT03": {"operation": sibling, "file": "test_post_other_invoice.py"},
    })
    rows = [
        {"operation": operation, "testrail_case_id": "1", "status": "passed", "details": "IIT02 only"},
        {"operation": operation, "testrail_case_id": "1", "status": "failed", "details": "IIT02 failure"},
        {"operation": operation, "testrail_case_id": "2", "status": "skipped"},
        {"operation": sibling, "testrail_case_id": "3", "status": "passed", "details": "IIT03 only"},
    ]
    groups = dashboard_server._invoice_intelligence_groups({"sections": [{"results": rows}]})
    assert [group["case_count"] for group in groups] == [2, 1]
    assert (groups[0]["passed"], groups[0]["failed"], groups[0]["skipped"]) == (0, 1, 1)
    assert groups[0]["results"] == rows[:3]
    assert groups[1]["results"] == rows[3:]


def test_invoice_action_live_counts_replace_previous_runs(monkeypatch, tmp_path):
    scope = "action:test_post_get_invoice_narrative.py"
    state = {"action_counts": {scope: {"total": 99, "passed": 99}}}
    payload = {"summary": {"total": 1, "passed": 1}, "sections": [{"name": "Invoice Intelligence", "results": [
        {"operation": "POST /v1/invoice-intelligence/GetInvoiceNarrative", "testrail_case_id": "1", "status": "passed"},
    ]}]}
    monkeypatch.setattr(dashboard_server, "_load_last_run", lambda: state)

    def save_run(data):
        state.clear()
        state.update(data)

    monkeypatch.setattr(dashboard_server, "_save_last_run", save_run)
    monkeypatch.setattr(dashboard_server, "_run_pytest", lambda *args: SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(dashboard_server, "_load_dashboard_data", lambda: payload)
    monkeypatch.setattr(dashboard_server, "_generate_pdf", lambda *args: "report.pdf")
    monkeypatch.setattr(dashboard_server, "_integrate_testrail", lambda *args: {})
    monkeypatch.setattr(dashboard_server, "DASHBOARD_JSON", tmp_path / "dashboard.json")
    for execution in range(2):
        dashboard_server._execute_test_run(scope)
        assert state["status"] == "completed"
        assert state["action_counts"][scope] == {"total": 1, "passed": 1, "failed": 0, "skipped": 0}


def test_invoice_nonempty_table_without_mapping_fails_instead_of_skipping():
    import pytest
    from tests.invoice_intelligence.test_post_get_invoice_narrative import _load_rows

    database = SimpleNamespace(fetch_scalar=lambda query: 1, fetch_all=lambda query: [])
    with pytest.raises(AssertionError, match="usable header/currency mapping"):
        _load_rows(database)


def test_invoice_compatibility_preview_reports_keys_without_ai_text():
    from datetime import date
    from tests.invoice_intelligence.test_post_get_invoice_narrative import preview_seed_compatibility

    captured = {}

    def fetch_all(query, params):
        captured["params"] = params
        return [{"InvoiceNarrativeId": 1, "ProviderId": 32, "ProviderName": "Provider", "CurrencyId": 1,
                 "AccountNo": "account", "BillingAccount": "billing", "InvoiceDate": date(2024, 3, 1),
                 "AIAnalysis": "Full analysis", "AIBrief": "Full brief"}]

    database = SimpleNamespace(fetch_scalar=lambda query: 1, fetch_all=fetch_all)
    result = preview_seed_compatibility(database, provider_id=32)
    assert captured["params"] == (2, 32, 32)
    assert result[0]["invoiceDate"] == "2024-03-01"
    assert result[0]["hasAIAnalysis"] and result[0]["hasAIBrief"]
    assert "AIAnalysis" not in result[0] and "AIBrief" not in result[0]


def test_invoice_compatibility_endpoint_reports_empty_table(monkeypatch):
    database = SimpleNamespace(fetch_scalar=lambda query: 0, close=lambda: None)
    monkeypatch.setattr(dashboard_server, "DBHelper", lambda: database)
    response = dashboard_server.app.test_client().post("/api/check-compatibility", json={
        "scope": "invoice_intelligence", "file": "test_post_get_invoice_narrative.py",
    })
    assert response.status_code == 200
    assert response.get_json()["compatible"] == []
    assert "No data available in [core].[InvoiceNarrative]" in response.get_json()["message"]


def test_invoice_output_records_full_response_before_assertions_and_keeps_updates():
    from tests.invoice_intelligence.test_post_get_invoice_narrative import _record_output, _record_response

    request = SimpleNamespace(node=SimpleNamespace(user_properties=[("other", "preserved")]))
    response = SimpleNamespace(status=400, text=lambda: '{"error":"Invalid invoice date"}')
    messages = []
    _record_response(request, messages, response, {"invoiceDate": "bad-date"})
    details = dict(request.node.user_properties)["dashboard_metrics"]
    assert "HTTP 400" in details
    assert '"invoiceDate": "bad-date"' in details
    assert '{"error":"Invalid invoice date"}' in details
    messages.append("DB comparison details")
    _record_output(request, messages)
    assert len([key for key, value in request.node.user_properties if key == "dashboard_metrics"]) == 1
    assert dict(request.node.user_properties)["other"] == "preserved"
    assert "DB comparison details" in dict(request.node.user_properties)["dashboard_metrics"]


def test_invoice_contract_matches_text_ignoring_whitespace(capsys):
    from tests.invoice_intelligence.test_post_get_invoice_narrative import _assert_narrative

    response = SimpleNamespace(
        status=200, headers={"content-type": "application/json"},
        text=lambda: "", json=lambda: {"aiAnalysis": "Analysis\n text", "aiBrief": "Brief text"},
    )
    row = {"InvoiceNarrativeId": 1, "AIAnalysis": "Analysis text", "AIBrief": "Brief  text"}
    _assert_narrative(response, row)
    output = capsys.readouterr().out
    assert "AI Analysis matched with DB" in output
    assert "AI Brief matched with DB" in output


def test_invoice_contract_rejects_missing_null_blank_and_mismatched_text():
    import pytest
    from tests.invoice_intelligence.test_post_get_invoice_narrative import _assert_narrative

    row = {"InvoiceNarrativeId": 1, "AIAnalysis": "Analysis", "AIBrief": "Brief"}
    for field in ("aiAnalysis", "aiBrief"):
        for invalid in (None, "", "   ", 1, "Different text"):
            payload = {"aiAnalysis": "Analysis", "aiBrief": "Brief", field: invalid}
            response = SimpleNamespace(status=200, headers={"content-type": "application/json"}, text=lambda: "", json=lambda: payload)
            with pytest.raises(AssertionError):
                _assert_narrative(response, row)
        payload = {"aiAnalysis": "Analysis", "aiBrief": "Brief"}
        del payload[field]
        response = SimpleNamespace(status=200, headers={"content-type": "application/json"}, text=lambda: "", json=lambda: payload)
        with pytest.raises(AssertionError, match="missing"):
            _assert_narrative(response, row)


def test_case_endpoint_marks_only_cases_mapped_in_requested_scope(monkeypatch):
    node_id = "tests/purpose/test_get_purpose.py::TestGetPurpose::test_count[C17767742]"
    monkeypatch.setattr(dashboard_server, "_pytest_case_mapping", lambda scope: {"17767742": [{"nodeid": node_id}]})
    monkeypatch.setattr(
        dashboard_server,
        "_testrail_case_data",
        lambda: ({12: "Purpose"}, [
            {"id": 17767742, "title": "Purpose count", "dashboard_section": "Purpose"},
            {"id": 999, "title": "Manual case", "dashboard_section": "Inventory Management / Purpose / GET Purpose"},
            {"id": 1000, "title": "Unrelated notes", "dashboard_section": "Inventory Management / Notes"},
            {"id": 1001, "title": "Similar section name", "dashboard_section": "Purpose History"},
        ]),
    )

    response = dashboard_server.app.test_client().get("/api/dashboard-cases?scope=Purpose")

    assert response.status_code == 200
    cases = response.get_json()["cases"]
    assert [case["case_id"] for case in cases] == ["17767742", "999"]
    assert cases[0]["selectable"] is True
    assert cases[0]["node_ids"] == [node_id]
    assert cases[1]["selectable"] is False
    assert cases[1]["node_ids"] == []


def test_case_endpoint_action_excludes_sibling_actions_and_manual_cases(monkeypatch):
    node_id = "tests/workorders/test_post_workorders_disconnect.py::test_disconnect"
    monkeypatch.setattr(dashboard_server, "_pytest_case_mapping", lambda scope: {"1": [{"nodeid": node_id}]})
    monkeypatch.setattr(dashboard_server, "_testrail_case_data", lambda: ({}, [
        {"id": 1, "title": "Disconnect", "dashboard_section": "Workorders / Disconnect"},
        {"id": 2, "title": "Provision", "dashboard_section": "Workorders / Provision"},
        {"id": 3, "title": "Manual disconnect", "dashboard_section": "Workorders / Disconnect"},
    ]))
    response = dashboard_server.app.test_client().get(
        "/api/dashboard-cases?scope=action:test_post_workorders_disconnect.py"
    )
    assert response.status_code == 200
    assert [case["case_id"] for case in response.get_json()["cases"]] == ["1"]


def test_case_endpoint_invoice_section_includes_children_not_other_payment_sections(monkeypatch):
    monkeypatch.setattr(dashboard_server, "_pytest_case_mapping", lambda scope: {})
    monkeypatch.setattr(dashboard_server, "_testrail_case_data", lambda: ({}, [
        {"id": 1, "dashboard_section": "Payment Readiness / Invoice Intelligence / GET Invoice Narrative"},
        {"id": 2, "dashboard_section": "Payment Readiness / Invoice Intelligence / Other Operation"},
        {"id": 3, "dashboard_section": "Payment Readiness / Payments"},
    ]))
    response = dashboard_server.app.test_client().get("/api/dashboard-cases?scope=Invoice Intelligence")
    assert response.status_code == 200
    assert [case["case_id"] for case in response.get_json()["cases"]] == ["1", "2"]


def test_case_endpoint_vendor_map_display_alias_matches_testrail_hierarchy(monkeypatch):
    monkeypatch.setattr(dashboard_server, "_pytest_case_mapping", lambda scope: {})
    monkeypatch.setattr(dashboard_server, "_testrail_case_data", lambda: ({}, [
        {"id": 1, "dashboard_section": "Sync Management / Vendor Map / Fetch Devices"},
        {"id": 2, "dashboard_section": "Sync Management / Other API"},
    ]))
    response = dashboard_server.app.test_client().get("/api/dashboard-cases?scope=Sync Management (Vendor Map)")
    assert response.status_code == 200
    assert [case["case_id"] for case in response.get_json()["cases"]] == ["1"]


def test_run_rejects_node_ids_outside_requested_scope(monkeypatch):
    monkeypatch.setattr(dashboard_server, "_validate_seed_filters", lambda *args: None)
    monkeypatch.setattr(
        dashboard_server,
        "_pytest_case_mapping",
        lambda scope: {"17767742": [{"nodeid": "tests/purpose/test_get_purpose.py::test_allowed"}]},
    )

    response = dashboard_server.app.test_client().post(
        "/run",
        data={"section": "Purpose", "node_id": "tests/notes/test_get_notes.py::test_not_allowed"},
    )

    assert response.status_code == 400
    assert "do not belong" in response.get_json()["error"]


def test_pytest_runner_appends_selected_node_ids(monkeypatch):
    completed = SimpleNamespace(returncode=0)
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return completed

    monkeypatch.setattr(dashboard_server.subprocess, "run", fake_run)
    node_ids = ["tests/purpose/test_get_purpose.py::test_selected"]

    result = dashboard_server._run_pytest("Purpose", node_ids=node_ids)

    assert result is completed
    assert captured["command"] == [dashboard_server.sys.executable, "-m", "pytest", node_ids[0], "-v"]


def test_pytest_runner_keeps_full_section_when_no_node_ids(monkeypatch):
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(dashboard_server.subprocess, "run", fake_run)

    dashboard_server._run_pytest("Purpose", node_ids=None)

    assert str(dashboard_server.ROOT_DIR / "tests" / "purpose") in captured["command"]
    assert not any("::" in argument for argument in captured["command"])


def test_dashboard_renders_sticky_shell_navigation_search_and_lazy_case_dialog(monkeypatch):
    monkeypatch.setattr(dashboard_server, "_load_dashboard_data", lambda: {"sections": [], "summary": {"by_section": {}}})
    monkeypatch.setattr(dashboard_server, "_historical_failure_counts", lambda data: {})
    monkeypatch.setattr(dashboard_server, "_connection_health", lambda: {"db": {}, "testrail": {}})
    monkeypatch.setattr(dashboard_server, "_discover_sections", lambda data: ["Purpose"])

    response = dashboard_server.app.test_client().get("/")

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "position: sticky" in page
    assert 'id="nav-search"' in page
    assert 'id="case-dialog"' in page
    assert "Show Cases" in page


def test_testrail_case_and_section_requests_include_offsets():
    client = RailClient(RailConfig("https://testrail.invalid", "user", "key", 1, 2, 3))
    requests = []
    client._request = lambda method, path: requests.append(path) or {"cases": [], "sections": []}

    client.get_cases(section_id=12, limit=100, offset=200)
    client.get_sections(limit=100, offset=200)

    assert requests == [
        "get_cases/1&suite_id=2&limit=100&offset=200&section_id=12",
        "get_sections/1&suite_id=2&limit=100&offset=200",
    ]


def test_testrail_case_data_paginates_and_uses_five_minute_cache(monkeypatch):
    calls = {"section_offsets": [], "case_offsets": []}

    class FakeClient:
        def get_sections(self, limit, offset):
            calls["section_offsets"].append(offset)
            if offset:
                return []
            sections = [{"id": 1, "name": "API", "parent_id": None}, {"id": 2, "name": "Purpose", "parent_id": 1}]
            sections.extend({"id": section_id, "name": "Other", "parent_id": None} for section_id in range(3, 251))
            return sections

        def get_cases(self, limit, offset):
            calls["case_offsets"].append(offset)
            if offset:
                return [{"id": 999, "title": "Last case", "section_id": 2}]
            return [{"id": case_id, "title": "Case", "section_id": 2} for case_id in range(1, 251)]

    monkeypatch.setattr(RailClient, "from_env", classmethod(lambda cls: FakeClient()))
    monkeypatch.setattr(dashboard_server, "_case_data_cache", {"expires_at": 0, "sections": {}, "cases": []})

    _, cases = dashboard_server._testrail_case_data()
    dashboard_server._testrail_case_data()

    assert calls["section_offsets"] == [0, 250]
    assert calls["case_offsets"] == [0, 250]
    assert len(cases) == 251
    assert cases[0]["dashboard_section"] == "API / Purpose"
    assert cases[-1]["dashboard_section"] == "API / Purpose"


def test_browser_case_picker_search_and_scrolling(monkeypatch):
    monkeypatch.setattr(dashboard_server, "_load_dashboard_data", lambda: {"sections": [], "summary": {"by_section": {}}})
    monkeypatch.setattr(dashboard_server, "_historical_failure_counts", lambda data: {})
    monkeypatch.setattr(dashboard_server, "_connection_health", lambda: {"db": {}, "testrail": {}})
    monkeypatch.setattr(dashboard_server, "_discover_sections", lambda data: ["Purpose"])
    page_html = dashboard_server.app.test_client().get("/").get_data(as_text=True)
    node_id = "tests/purpose/test_get_purpose.py::TestGetPurpose::test_count[C17767742]"
    submitted = {}

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        page.route("http://dashboard.test/", lambda route: route.fulfill(content_type="text/html", body=page_html))
        page.route(
            "http://dashboard.test/api/run-status",
            lambda route: route.fulfill(json={"status": "idle", "generated_at": ""}),
        )
        page.route(
            "http://dashboard.test/api/dashboard-cases**",
            lambda route: route.fulfill(json={"cases": [{
                "case_id": "17767742",
                "title": "Purpose count",
                "section": "Purpose",
                "node_ids": [node_id],
                "selectable": True,
            }]}),
        )

        def capture_run(route):
            submitted["body"] = route.request.post_data or ""
            route.fulfill(status=200, body="Run accepted")

        page.route("http://dashboard.test/run", capture_run)
        page.goto("http://dashboard.test/")
        page.locator("#nav-search").fill("Purpose")
        page.locator("#nav-search").press("Enter")
        page.locator("#section-purpose-tab").wait_for(state="visible")
        page.locator("#section-purpose-tab .case-picker-trigger").click()
        page.locator("#case-dialog").wait_for(state="visible")
        page.get_by_label("Select case C17767742").check()
        page.locator("#case-dialog-done").click()

        desktop_layout = page.evaluate("""() => ({
            headerPosition: getComputedStyle(document.querySelector('.app-header')).position,
            contentOverflow: getComputedStyle(document.querySelector('.container')).overflowY,
            sidebarOverflow: getComputedStyle(document.querySelector('.sidebar')).overflowY
        })""")
        assert desktop_layout == {"headerPosition": "sticky", "contentOverflow": "auto", "sidebarOverflow": "auto"}

        page.set_viewport_size({"width": 390, "height": 844})
        mobile_layout = page.evaluate("""() => ({
            headerPosition: getComputedStyle(document.querySelector('.app-header')).position,
            sidebarPosition: getComputedStyle(document.querySelector('.sidebar')).position,
            sidebarTop: getComputedStyle(document.querySelector('.sidebar')).top,
            contentOverflow: getComputedStyle(document.querySelector('.container')).overflowY,
            headerWidth: document.querySelector('.app-header').getBoundingClientRect().width,
            documentWidth: document.documentElement.clientWidth
        })""")
        assert mobile_layout["headerPosition"] == "sticky"
        assert mobile_layout["sidebarPosition"] == "fixed"
        assert mobile_layout["sidebarTop"] == "76px"
        assert mobile_layout["contentOverflow"] == "auto"
        assert mobile_layout["headerWidth"] == mobile_layout["documentWidth"]

        page.locator("#section-purpose-tab form button[type=submit]").click()
        page.wait_for_timeout(100)
        assert "node_id=" in submitted["body"]
        assert "test_get_purpose.py" in submitted["body"]

        page.goto("http://dashboard.test/")
        page.locator("#section-purpose-tab form button[type=submit]").click()
        page.wait_for_timeout(100)
        assert "node_id=" not in submitted["body"]
        browser.close()


def test_dashboard_idle_timeout_ignores_status_polls_and_active_runs(monkeypatch):
    monkeypatch.setattr(dashboard_server, "_last_dashboard_activity", 100.0)

    response = dashboard_server.app.test_client().get("/api/run-status")

    assert response.status_code == 200
    assert dashboard_server._last_dashboard_activity == 100.0
    assert dashboard_server._dashboard_should_stop(400.0, 300, run_active=False)
    assert not dashboard_server._dashboard_should_stop(400.0, 300, run_active=True)
    assert dashboard_server._last_dashboard_activity == 400.0
    assert not dashboard_server._dashboard_should_stop(400.0, 0, run_active=False)