# Prompt Template: New Web UI Automation Project

Use this as the starting prompt when asking Copilot/an AI agent to scaffold a new **web automation** project,
based on the structure and conventions used in this API automation project (Cimpl_API_Automation).
Fill in the bracketed `[...]` sections before submitting, then paste the whole thing as your first message.

---

## Project Request

Create a new Python-based **web UI automation** project named `[PROJECT_NAME]` for testing `[APPLICATION_NAME / URL]`.
Model the project structure, conventions, and tooling after an existing internal project
(Cimpl_API_Automation) that does API automation, adapted for browser/UI testing. Follow the
conventions below exactly unless I say otherwise.

### Tech stack
- Python + `pytest` as the test runner
- `playwright` (sync API) + `pytest-playwright` for browser automation (Chromium/Edge/Firefox)
- `python-dotenv` for environment/config management (`.env` + `.env.example`)
- `pyodbc` for SQL Server DB validation (pre/post-action DB assertions, not just UI assertions)
- `Flask` for a lightweight local results dashboard (HTML/JSON)
- `reportlab` for optional PDF report export
- Optional TestRail integration for case creation + result publishing

### Repo layout to scaffold
```
[project_root]/
  conftest.py                 # pytest hooks: dashboard result collection, env loading, timeouts
  pytest.ini                  # testpaths, custom markers
  dashboard_server.py         # Flask app to run suites / view results / trigger cleanup tools
  requirements.txt
  .env.example
  README.md
  reports/                    # generated dashboards/PDFs (gitignored)
  tests/
    __init__.py
    [feature_area_1]/         # one folder per page/flow/module under test, e.g. login/, checkout/
      test_[flow_name].py
    [feature_area_2]/
      _[feature]_common.py    # shared page-object helpers / fixtures for that area
  utils/
    __init__.py
    db_helper.py               # thin pyodbc wrapper: fetch_one/fetch_all/count_rows/execute
    post_ui_db_scripts.py       # helper for pre/post DB assertions around UI actions (create/update/delete flows)
    page_objects/               # Playwright Page Object classes, one per page/screen
    testrail_client.py          # optional: TestRail REST wrapper (add_case, add_result, get_sections, get_labels)
    test_metadata.py            # shared marker/metadata helpers used by dashboard + testrail publishing
```

### Test file conventions (apply to every new test module)
- Each test file covers ONE flow/page/action (mirrors one API endpoint file in the old project,
  here it should mirror one user flow, e.g. `test_login.py`, `test_checkout_flow.py`).
- Define test cases as a list of small `@dataclass(frozen=True)` case objects (`case_id`, `title`, `label`)
  where `label` is one of `Positive` / `Validation` / `Authorization` (or add more as needed, e.g. `Negative`, `UI`).
- Build a `CASE_PARAMS` list of `pytest.param(case, id=f"C{case.case_id}", marks=[...])` where marks include:
  - `swagger_section` -> rename to `page_section("...")` (page/module name shown in dashboard)
  - `swagger_operation` -> rename to `ui_flow("...")` (human label of the flow/action under test)
  - `testrail_case(case.case_id)`
  - `dashboard_title(case.title)`
- Use Playwright fixtures (`page`) for browser interaction; wrap common waits/selectors in Page Object
  helper classes under `utils/page_objects/`, not inline in test files.
- For flows that create/update/delete DB records via the UI, add pre-action `count_rows()`/`fetch_one()`
  checks and post-action assertions using `utils/post_ui_db_scripts.py`, mirroring the API project's
  `post_api_db_scripts.py` pattern (assert row count deltas, assert key field values after save).
- Prefer real, deterministic seed data selection via DB queries (`SELECT TOP 1 ... ORDER BY ... DESC`)
  over hardcoded test data, and exclude records already in a pending/mutated state from earlier test runs.
- Assert final pass/fail by the case `label` category, not just HTTP/status code — for UI this means
  asserting the expected success/error message or resulting page state per label.

### conftest.py / dashboard requirements
- Load `.env` via `python-dotenv` at conftest import time.
- Add custom markers in `pytest.ini`: `page_section`, `ui_flow`, `testrail_case`, `dashboard_title`.
- Collect per-test results (status, section, testrail_case_id, title, duration, screenshot path on
  failure) into a dashboard JSON file under `reports/`.
- Roll up results two ways: by execution (every pytest run) and by unique TestRail case id (a single
  case can be exercised by multiple parametrized executions) — keep both counts, don't conflate them.
- On failure, capture a Playwright screenshot (and optionally trace/video) and store the path in the
  dashboard payload so `dashboard_server.py` can surface it.
- `dashboard_server.py` should expose: trigger a full/partial pytest run, show pass/fail counts by
  section, link out to TestRail cases, and (optionally) expose manual cleanup tools for test data
  created during runs — same "opt-in, explicit input only" pattern as the API project (never a bulk
  auto-cleanup button).

### Config / environment variables (.env.example)
Include placeholders for at least:
```
BASE_URL=
LOGIN_USERNAME=
LOGIN_PASSWORD=
BROWSER=chromium
HEADLESS=true
UI_ACTION_TIMEOUT_MS=30000
DB_SERVER=
DB_NAME=
DB_USER=
DB_PASSWORD=
DB_TRUSTED_CONNECTION=
TESTRAIL_ENABLE_ON_RUN=
TESTRAIL_URL=
TESTRAIL_EMAIL=
TESTRAIL_API_KEY=
TESTRAIL_PROJECT_ID=
TESTRAIL_MILESTONE_ID=
TESTRAIL_SUITE_ID=
```

### Workflow expectations when we add a new flow together
1. Before writing code, confirm with me: flow name, TestRail section to file cases under, any manual
   test case list/requirement doc, relevant DB tables, and a known-good real example to validate against.
2. Verify actual UI selectors/behavior by inspecting the real app first (like verifying swagger paths
   in the API project) — don't trust a requirements doc's field/button names blindly.
3. Create TestRail cases first with real case IDs, then write the automation referencing those IDs.
4. Write the test file following the dataclass/CASE_PARAMS pattern above.
5. Run the suite and let real UI behavior correct wrong Positive/Validation assumptions.
6. Wire the new flow into `dashboard_server.py`'s flow registry (mirroring `WORKORDER_ACTIONS`/
   `SELF_SERVICE_ACTIONS` dicts) with `implemented: True`.
7. Add any needed data cleanup as a manual, explicit-input-only dashboard tool — never automatic.

### What NOT to commit
`.env`, `.venv/`, `reports/`, browser trace/video artifacts, local cache files. Only commit
`.env.example` as a sanitized template.

---

## Notes for the assistant reading this prompt
- Ask me for the target application URL/base URL, credentials source, and DB connection details if
  DB validation is in scope before scaffolding.
- Default to Playwright + pytest unless I specify Selenium/Cypress/other.
- Keep the same "dashboard + TestRail + DB validation" three-legged reporting approach unless told
  to drop one of the legs.
