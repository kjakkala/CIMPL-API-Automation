import json
import importlib
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, abort, jsonify, redirect, render_template_string, request, send_file, url_for
from werkzeug.serving import make_server

from utils.db_helper import DBHelper
from utils.dashboard_scheduler import ScheduleStore, ScheduleWorker, _stamp, next_occurrence
from utils.pdf_report import build_dashboard_pdf, pdf_name_for_run
from utils.self_service_cleanup import cancel_self_service_requests, parse_request_numbers
from utils.testrail_client import TestRailClient, TestRailError
from utils.workorder_cleanup import cancel_workorders, parse_workorder_numbers

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parent
REPORTS_DIR = ROOT_DIR / "reports"
PDF_DIR = REPORTS_DIR / "pdf"
DASHBOARD_JSON = REPORTS_DIR / "test_dashboard.json"
LAST_RUN_JSON = REPORTS_DIR / "last_run.json"
SCHEDULE_DB = REPORTS_DIR / "dashboard_scheduler.sqlite3"
SCHEDULE_LOG_DIR = REPORTS_DIR / "scheduled_runs"

app = Flask(__name__)
_run_lock = threading.Lock()
_case_cache_lock = threading.Lock()
_pytest_case_lock = threading.Lock()
_dashboard_activity_lock = threading.Lock()
_last_dashboard_activity = time.monotonic()
_scheduler_start_lock = threading.Lock()
_schedule_store: ScheduleStore | None = None
_schedule_worker: ScheduleWorker | None = None
DASHBOARD_IDLE_TIMEOUT_SECONDS = int(os.getenv("DASHBOARD_IDLE_TIMEOUT_SECONDS", "1800"))
_case_data_cache: dict = {"expires_at": 0, "sections": {}, "cases": []}
_pytest_case_cache: dict[str, dict] = {}
_CASE_CACHE_TTL_SECONDS = 300


def _mark_dashboard_activity() -> None:
    global _last_dashboard_activity
    with _dashboard_activity_lock:
        _last_dashboard_activity = time.monotonic()


def _dashboard_should_stop(now: float, idle_timeout: int, run_active: bool) -> bool:
    global _last_dashboard_activity
    if idle_timeout <= 0:
        return False
    with _dashboard_activity_lock:
        if run_active:
            _last_dashboard_activity = now
            return False
        return now - _last_dashboard_activity >= idle_timeout


@app.before_request
def _track_dashboard_activity():
    if request.endpoint != "run_status":
        _mark_dashboard_activity()


def _serve_local_dashboard() -> None:
    start_schedule_worker()
    server = make_server("127.0.0.1", 5050, app, threaded=True)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    if DASHBOARD_IDLE_TIMEOUT_SECONDS > 0:
        def stop_when_idle():
            poll_interval = min(30, max(1, DASHBOARD_IDLE_TIMEOUT_SECONDS // 10))
            while True:
                time.sleep(poll_interval)
                if _dashboard_should_stop(
                    time.monotonic(),
                    DASHBOARD_IDLE_TIMEOUT_SECONDS,
                    _run_lock.locked() or _get_schedule_store().has_enabled_schedules(),
                ):
                    print("Dashboard stopped after reaching its idle timeout.")
                    server.shutdown()
                    return

        threading.Thread(target=stop_when_idle, daemon=True).start()

    print(f"Dashboard running at http://127.0.0.1:5050 (idle timeout: {DASHBOARD_IDLE_TIMEOUT_SECONDS}s)")
    server.serve_forever()


class _PrefixMiddleware:
    """Makes url_for() emit APP_BASE_PATH (e.g. /ApiAutomation) when the app is
    reverse-proxied under a base path by IIS."""

    def __init__(self, wsgi_app, base_path: str):
        self.wsgi_app = wsgi_app
        self.base_path = base_path.rstrip("/")

    def __call__(self, environ, start_response):
        if self.base_path:
            environ["SCRIPT_NAME"] = self.base_path
            path_info = environ.get("PATH_INFO", "")
            if path_info.startswith(self.base_path):
                environ["PATH_INFO"] = path_info[len(self.base_path):]
        return self.wsgi_app(environ, start_response)


_app_base_path = os.environ.get("APP_BASE_PATH", "").strip()
if _app_base_path:
    app.wsgi_app = _PrefixMiddleware(app.wsgi_app, _app_base_path)  # type: ignore[assignment]


@app.after_request
def _disable_caching(response):
    """Force browsers to always fetch a fresh copy; this app has no static assets to version."""
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

def _environment_variable_prefix(environment_key: str) -> str:
    return "TEST_ENV_" + "".join(character if character.isalnum() else "_" for character in environment_key.upper())


def _load_test_environments() -> dict[str, dict]:
    configured = [item.strip().lower() for item in os.getenv("TEST_ENVIRONMENTS", "qa-main,qa-rc").split(",") if item.strip()]
    profiles = {}
    for environment_key in configured:
        prefix = _environment_variable_prefix(environment_key)
        label = os.getenv(f"{prefix}_LABEL", environment_key.replace("-", " ").title())
        profiles[environment_key] = {
            "label": label,
            "token_url": os.getenv(f"{prefix}_TOKEN_URL", os.getenv("TOKEN_URL", "")).rstrip("/"),
            "db_name": os.getenv(f"{prefix}_DB_NAME", ""),
            "base_url": os.getenv(f"{prefix}_BASE_URL", "").rstrip("/"),
            "self_service_base_url": os.getenv(f"{prefix}_SELF_SERVICE_BASE_URL", "").rstrip("/"),
        }
    return profiles


TEST_ENVIRONMENTS = _load_test_environments()


def _default_environment_key() -> str:
    configured_default = os.getenv("DEFAULT_TEST_ENVIRONMENT", "").strip().lower()
    if configured_default in TEST_ENVIRONMENTS:
        return configured_default
    return next(iter(TEST_ENVIRONMENTS))


def _environment_profile(environment_key: str) -> dict:
    default_key = _default_environment_key()
    return TEST_ENVIRONMENTS.get(environment_key, TEST_ENVIRONMENTS[default_key])


def _run_environment(environment_key: str, provider_id: int | None = None, service_type_id: int | None = None) -> dict:
    profile = _environment_profile(environment_key)
    environment = os.environ.copy()
    environment.update({
        "TOKEN_URL": profile["token_url"],
        "BASE_URL": profile["base_url"],
        "DB_NAME": profile["db_name"],
        "SELF_SERVICE_BASE_URL": profile["self_service_base_url"],
        "TEST_ENVIRONMENT": environment_key,
        "TEST_PROVIDER_ID": "" if provider_id is None else str(provider_id),
        "TEST_SERVICE_TYPE_ID": "" if service_type_id is None else str(service_type_id),
    })
    return environment


def _parse_seed_filters(provider_value: str, service_type_value: str) -> tuple[int | None, int | None, str | None]:
    provider_value = "" if provider_value.strip().lower() in {"none", "null"} else provider_value
    service_type_value = "" if service_type_value.strip().lower() in {"none", "null"} else service_type_value
    try:
        provider_id = int(provider_value) if provider_value.strip() else None
        service_type_id = int(service_type_value) if service_type_value.strip() else None
    except ValueError:
        return None, None, "Provider ID and Service Type ID must be whole numbers."
    if provider_id is not None and provider_id <= 0 or service_type_id is not None and service_type_id <= 0:
        return None, None, "Provider ID and Service Type ID must be positive numbers."
    return provider_id, service_type_id, None


def _validate_seed_filters(environment_key: str, provider_id: int | None, service_type_id: int | None) -> str | None:
    if provider_id is None and service_type_id is None:
        return None
    profile = _environment_profile(environment_key)
    previous_db_name = os.environ.get("DB_NAME")
    os.environ["DB_NAME"] = profile["db_name"]
    db = None
    try:
        db = DBHelper()
        where = ["p.Active=1", "st.Active=1", "c.Active=1"]
        params: list[int] = []
        if provider_id is not None:
            where.append("p.ProviderId=?")
            params.append(provider_id)
        if service_type_id is not None:
            where.append("st.ServiceTypeId=?")
            params.append(service_type_id)
        row = db.fetch_one(
            "SELECT TOP 1 p.ProviderId, st.ServiceTypeId FROM cimpl.Provider p CROSS JOIN cimpl.ServiceType st INNER JOIN cimpl.Contract c ON c.ProviderId=p.ProviderId AND c.ServiceTypeId=st.ServiceTypeId WHERE " + " AND ".join(where),
            tuple(params),
        )
        if not row:
            return "No active compatible provider/service type/contract combination exists for the selected filters."
        return None
    except Exception as exc:  # noqa: BLE001
        return f"Unable to validate provider/service type filters before the run: {exc}"
    finally:
        if db:
            db.close()
        if previous_db_name is None:
            os.environ.pop("DB_NAME", None)
        else:
            os.environ["DB_NAME"] = previous_db_name

SECTION_DISPLAY_NAMES = {
    "self_service_requests": "Self Service Requests",
    "vendor_map": "Sync Management (Vendor Map)",
    "invoice_intelligence": "Invoice Intelligence",
}

SECTION_HIERARCHY = {}

NAVIGATION_GROUPS = {
    "Inventory Management": ("Purpose", "Notes"),
    "Payment Readiness": ("Invoice Intelligence",),
}

SECTION_DESCRIPTIONS = {
    "location": {
        "mandatory": "Search criteria required by the selected location operation, such as location number, name, address, or city.",
        "optional": "Additional location filters supported by the endpoint.",
        "compatibility": "Validates search criteria combinations, empty values, invalid values, and response matching for the selected location record.",
        "coverage": "Positive searches, missing or blank criteria, invalid values, and response contract checks.",
    },
    "notes": {
        "mandatory": "The note identifier or service/workorder context required by the selected Notes operation.",
        "optional": "Note text, culture, and optional metadata where supported.",
        "compatibility": "Validates that note operations target the correct service, workorder, employee, or note record.",
        "coverage": "Create, retrieve, and delete behavior, invalid identifiers, empty input, and persistence validation.",
    },
    "purpose": {
        "mandatory": "PUR05 requires no request body. IncludeInActive is optional and defaults to false; the dropdown returns lightweight id/name objects with HTTP 200.",
        "optional": "IncludeInActive=true includes active and inactive purposes; false or omitted returns active purposes only.",
        "compatibility": "Validates active/inactive filtering, purpose IDs and names against cimpl.Purpose, response shape, duplicate query parameters, and authorization behavior.",
        "coverage": "PUR05 dropdown functional, validation, database comparison, response-schema, query-parameter edge, security, and performance checks.",
    },
    "rules": {
        "mandatory": "The rule identifier and required rule context defined by the Rules API.",
        "optional": "Optional filters and rule metadata supported by the endpoint.",
        "compatibility": "Validates rule identifiers, returned rule relationships, and response data consistency.",
        "coverage": "Successful retrieval, invalid identifiers, empty results, and response contract validation.",
    },
    "translations": {
        "mandatory": "The translation key, culture, or resource context required by the selected operation.",
        "optional": "Additional culture or filtering parameters supported by the endpoint.",
        "compatibility": "Validates culture/key combinations and returned translation consistency.",
        "coverage": "Valid and invalid keys, culture variants, missing values, and response validation.",
    },
    "users": {
        "mandatory": "Required employee or user identifiers for the selected Users operation.",
        "optional": "Optional employee number, email, department, or profile fields.",
        "compatibility": "Validates active employee identity relationships across ID, number, and email.",
        "coverage": "Valid users, missing and invalid identifiers, inactive users, mismatched identity fields, and persistence checks.",
    },
    "workorders": {
        "mandatory": "Each workorder action defines its own required requester, service, operation-specific fields, and database relationships.",
        "optional": "Due date, comments, culture, subject/reference, warehouse, cost center, provider contact, options, accessories, and other action-specific fields.",
        "compatibility": "Validates requester identity, service/provider/account/number relationships, catalog/provider/service type/contract compatibility, option and accessory compatibility, warehouse behavior, cost-center state, and operation-specific rules.",
        "coverage": "Positive and negative cases include missing, null, blank, invalid, inactive, mismatched, incompatible, boundary, security-input, authorization, response, and no-persistence checks.",
    },
    "self service requests": {
        "mandatory": "REQ01 Provision requires a valid employee or department target, provider, provisioning-allowed service type, compatible self-service contract, equipment catalog item, requester, and cost center when the active configuration requires it.",
        "optional": "Options, accessories, shipping address, due date, comments, and cost center when not mandatory by configuration. Employee and department are alternative receiving targets.",
        "compatibility": "Uses operation type 19 and filters self-service-enabled providers, service types, contracts, options, catalog items, and compatible accessories. Catalog items must match provider, service type, contract, and operation compatibility; options must not already belong to the contract.",
        "coverage": "61 REQ01 cases cover positive requests, employee/requester identity variants, provider and service-type rules, contract and catalog compatibility, options, accessories, shipping address, cost center, due dates, comments, unauthorized access, detailed API responses, and SelfServiceActionLog persistence.",
    },
    "sync management (vendor map)": {
        "mandatory": "VMP01 requires vendorId; VMP02 and VMP03 require vendorId plus one or more vendorEquipmentMapId values.",
        "optional": "VMP01 supports statusId, name, SKU, pageIndex, and pageSize filters; response translations remain a client concern.",
        "compatibility": "VMP01 is compared with cimpl.VendorEquipmentMap and HardwareType. ApproveDevices sets the equipment to Approved and HardwareType to Active; DeclineDevices sets the equipment to Declined and HardwareType to Not Active.",
        "coverage": "Functional, validation, database compatibility, response contract, security, cross-vendor isolation, and response-time cases for Fetch, Approve, and Decline Devices and Accessories.",
    },
}

SELF_SERVICE_ACTION_GUIDES = {
    "test_post_self_service_provision.py": {
        "mandatory": "Employee or department target, provider, provisioning-allowed service type, compatible self-service contract, equipment catalog item, and requester. Cost center is mandatory only when the active configuration requires it.",
        "optional": "Options, accessories, shipping address, due date, comments, and cost center when not required by configuration.",
        "compatibility": "OperationTypeId 19. Filters self-service-enabled providers, service types, contracts, options, and catalog items; catalog items must match provider, service type, contract, and operation compatibility; options must not already belong to the contract.",
        "coverage": "61 cases: positive requests, employee/requester identity variants, provider and service-type rules, contract/catalog compatibility, options, accessories, shipping address, cost center, due dates, comments, unauthorized access, and SelfServiceActionLog persistence.",
    },
    "test_post_self_service_upgrade.py": {
        "mandatory": "Service, requester, newCatalogItemId, newContractId compatible with the provider/service type/self-provisioning-enabled contract.",
        "optional": "Current asset, warehouse, new options, accessories, shipping address, cost center, culture, due date, and comments.",
        "compatibility": "OperationTypeId 20. Seed requires Contract.SelfProvisioningEnabled=1. Validates catalog-provider/service-type/contract compatibility, option/accessory compatibility, warehouse CanAdd state, and cost-center state; excludes services with a pending Self Service approval.",
        "coverage": "80 passed / 1 skipped (no non-primary asset seed available): positive upgrades, identity variants, catalog/contract/option/accessory compatibility, warehouse, cost center, due dates, comments, and persistence checks.",
    },
    "test_post_self_service_transfer_owner.py": {
        "mandatory": "Service, requester, and exactly one of newDepartmentId or newEmployee (mutually exclusive). Cost center is resolved automatically.",
        "optional": "Due date and comments.",
        "compatibility": "OperationTypeId 3 (department) or 4 (employee); an alternate operation type 7 was observed for certain optional-field combinations. Department eligibility validated empirically across ~13 OrganizationTypeId/OrganizationLevelId combinations.",
        "coverage": "39 cases: department type/level positive variants, missing/null/invalid/inactive department or employee, both-or-neither target validation, service/requester validation, due date/comments, and unauthorized access.",
    },
    "test_post_self_service_liability_out.py": {
        "mandatory": "Service, requester, and warehouseId compatible with the service type.",
        "optional": "Due date and comments.",
        "compatibility": "OperationTypeId 115. Seed requires Contract.SelfProvisioningEnabled=1 and an active, CanAdd-enabled warehouse compatible with the service type.",
        "coverage": "30 cases: positive requests, identity variants, warehouse validation (missing/null/invalid/inactive/CanAdd-disabled), due dates, comments (including SQL injection/XSS), and unauthorized access.",
    },
    "test_post_self_service_travel_package.py": {
        "mandatory": "destinationCountries, departureDate, returnDate, travelPackageIds, service, and requester.",
        "optional": "dueDate, comments, and ignoreWarning.",
        "compatibility": "OperationTypeId 118 via /requestApi/SelfServiceRequests/TravelPack. travelPackageIds must be self-provisioning-enabled for the service's provider/service type (cimpl.OptionTypeProviderServiceTypeCompatibility), AND destinationCountries must match an active roaming zone/country mapping for that travel package (OptionTypeRoamingZoneRelation -> RoamingZone -> RoamingZoneCountryRelation -> Country).",
        "coverage": "54 passed / 1 skipped (no self-provisioning-disabled travel package seed available): destination country, date, travel package, service, requester, due date, and comments validation, plus unauthorized access.",
    },
    "test_post_self_service_suspend.py": {
        "mandatory": "Service and requester. dueDate is mandatory despite the confluence doc listing it as optional.",
        "optional": "Comments.",
        "compatibility": "OperationTypeId 30. Service must be Active or Spare status (ServiceInventoryStatusId 1 or 4) with no pending transaction, no open workorder, and no pending Self Service approval. Note: live testing showed Spare-status services are actually rejected ('Service should be active'), contrary to the initial requirement note.",
        "coverage": "34 cases: positive requests, identity variants, service state validation (inactive/open workorder/pending approval), requester validation, due date (mandatory, past, invalid format), comments (including SQL injection/XSS), and unauthorized access.",
    },
    "test_post_self_service_activate.py": {
        "mandatory": "Suspended service and requester. dueDate is required and must be a valid future date.",
        "optional": "Comments. Empty, null, or omitted comments are allowed for valid requests.",
        "compatibility": "OperationTypeId 14. Service must be suspended, not already active, and must not have an open workorder, open transaction, or pending Self Service approval. Requester validation follows employee identity checks and active-status rules.",
        "coverage": "33 cases: positive Activate requests, service identity and status validation, requester identity variants, dueDate validation (missing/past/invalid), comments handling (including SQL injection/XSS), and unauthorized access.",
    },
}


def _self_service_action_guide(file_name: str) -> dict | None:
    return SELF_SERVICE_ACTION_GUIDES.get(str(file_name or "").strip())


def _display_section_name(name: str) -> str:
    normalized = str(name or "").strip()
    return SECTION_DISPLAY_NAMES.get(normalized.lower(), normalized.replace("_", " ").title())


def _section_description(section: str) -> dict:
    key = str(section or "").strip().lower()
    return SECTION_DESCRIPTIONS.get(key, {
        "mandatory": "Required fields defined by the selected API operation.",
        "optional": "Optional fields defined by the selected API operation.",
        "compatibility": "Validates related identifiers and database relationships used by the operation.",
        "coverage": "Positive, negative, boundary, authorization, response, and persistence checks where applicable.",
    })


def _guide_points(value: str) -> list[str]:
    return [point.strip() for point in str(value or "").split(". ") if point.strip()]


def _check_db_connection() -> dict:
    try:
        db = DBHelper()
        value = db.fetch_scalar("SELECT 1")
        db.close()
        if value == 1:
            return {"status": "passed", "message": "DB connection successful."}
        message = "DB health query returned unexpected result."
        print(f"DB connection check failed: {message}", file=sys.stdout, flush=True)
        print(f"DB connection check failed: {message}", file=sys.stderr, flush=True)
        return {"status": "failed", "message": message}
    except Exception as exc:  # noqa: BLE001
        message = f"DB check failed: {exc}"
        print(message, file=sys.stdout, flush=True)
        print(message, file=sys.stderr, flush=True)
        return {"status": "failed", "message": message}


def _check_testrail_connection() -> dict:
    enabled = os.getenv("TESTRAIL_ENABLE_ON_RUN", "1").strip() == "1"
    if not enabled:
        return {"status": "skipped", "message": "TestRail integration is disabled."}

    try:
        client = TestRailClient.from_env()
        projects = client._request("GET", "get_projects")
        return {
            "status": "passed",
            "message": f"TestRail authentication successful. Accessible projects: {len(projects)}",
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "message": f"TestRail check failed: {exc}"}


def _connection_health() -> dict:
    return {
        "db": _check_db_connection(),
        "testrail": _check_testrail_connection(),
    }


def _default_dashboard() -> dict:
    return {
        "generated_at": "",
        "summary": {"total": 0, "passed": 0, "failed": 0, "skipped": 0},
        "sections": [],
    }


def _load_dashboard_data() -> dict:
    if not DASHBOARD_JSON.exists():
        return _default_dashboard()
    try:
        return json.loads(DASHBOARD_JSON.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return _default_dashboard()


def _load_last_run() -> dict:
    if not LAST_RUN_JSON.exists():
        return {}
    try:
        return json.loads(LAST_RUN_JSON.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save_last_run(payload: dict):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    LAST_RUN_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _discover_sections(dashboard_data: dict) -> list[str]:
    sections = [_display_section_name(section.get("name", "")) for section in dashboard_data.get("sections", [])]
    sections = [section for section in sections if section]

    tests_dir = ROOT_DIR / "tests"
    if not tests_dir.exists():
        return sorted(set(sections))

    folder_sections = []
    for child in tests_dir.iterdir():
        if child.is_dir() and not child.name.startswith("__"):
            folder_sections.append(_display_section_name(child.name))
    discovered = set(sections + folder_sections)
    for parent, children in SECTION_HIERARCHY.items():
        if any(child in discovered for child in children):
            discovered.add(parent)
    return sorted(discovered)


def _navigation_counts(dashboard_data: dict) -> dict[str, dict[str, int]]:
    by_section = dashboard_data.get("summary", {}).get("by_section", {})
    counts = {
        section: {
            "passed": int(stats.get("cases_passed", 0) or 0),
            "total": int(stats.get("total_cases", 0) or 0),
        }
        for section, stats in by_section.items()
    }
    for group, children in NAVIGATION_GROUPS.items():
        counts[group] = {
            "passed": sum(counts.get(child, {}).get("passed", 0) for child in children),
            "total": sum(counts.get(child, {}).get("total", 0) for child in children),
        }
    return counts


def _results_for_scope(dashboard_data: dict, section: str | None) -> dict[str, list[dict]]:
    scoped: dict[str, list[dict]] = {}
    for sec in dashboard_data.get("sections", []):
        sec_name = sec.get("name", "")
        if not sec_name:
            continue
        if section and section.lower() != "all" and sec_name.lower() != section.lower():
            continue
        scoped[sec_name] = sec.get("results", [])
    return scoped


def _extract_section_case_ids(section_results: dict[str, list[dict]]):
    missing = []
    case_ids: dict[str, list[int]] = {}

    for section_name, results in section_results.items():
        ids: list[int] = []
        for result in results:
            case_id_raw = str(result.get("testrail_case_id", "")).strip()
            if not case_id_raw or not case_id_raw.isdigit():
                missing.append(
                    {
                        "section": section_name,
                        "test": result.get("title", result.get("nodeid", "Unknown test")),
                    }
                )
                continue
            ids.append(int(case_id_raw))

        if ids:
            case_ids[section_name] = sorted(set(ids))

    return case_ids, missing


def _plan_name(section: str | None) -> str:
    stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    if section and section.lower() != "all":
        return f"API Automation - {section} - {stamp}"
    return f"API Automation - {stamp}"


def _testrail_scope(scope_name: str) -> str:
    if not scope_name.startswith("action:"):
        return scope_name
    action_file = scope_name.removeprefix("action:").strip()
    if action_file.startswith("test_post_vendor_map_"):
        return "Sync Management (Vendor Map)"
    if action_file.startswith("test_post_self_service_"):
        return "Self Service Requests"
    if action_file in {metadata["file"] for metadata in INVOICE_INTELLIGENCE_ACTIONS.values()}:
        return "Invoice Intelligence"
    return "Workorders"


def _run_pytest(section: str | None, environment_key: str | None = None, provider_id: int | None = None, service_type_id: int | None = None, node_ids: list[str] | None = None):
    environment_key = environment_key or _default_environment_key()
    if section and section.startswith("action:"):
        action_file = section.removeprefix("action:").strip()
        if action_file.startswith("test_post_self_service_"):
            action_folder = "self_service_requests"
        elif action_file.startswith("test_post_vendor_map_"):
            action_folder = "vendor_map"
        elif action_file in {metadata["file"] for metadata in INVOICE_INTELLIGENCE_ACTIONS.values()}:
            action_folder = "invoice_intelligence"
        else:
            action_folder = "workorders"
        target = ROOT_DIR / "tests" / action_folder / action_file
        if not target.exists() or target.suffix != ".py":
            raise RuntimeError(f"No workorder action found for '{action_file}'")
        cmd = [sys.executable, "-m", "pytest", str(target), "-v"]
    elif section and section.lower() != "all":
        folder_name = next((folder for folder, display in SECTION_DISPLAY_NAMES.items() if display.lower() == section.lower()), section.lower().replace(" ", "_"))
        target = ROOT_DIR / "tests" / folder_name
        if not target.exists():
            raise RuntimeError(f"No test folder found for section '{section}'")
        cmd = [sys.executable, "-m", "pytest", str(target), "-v"]
    else:
        cmd = [sys.executable, "-m", "pytest", str(ROOT_DIR / "tests"), "-v"]

    if node_ids:
        cmd = [sys.executable, "-m", "pytest", *node_ids, "-v"]

    completed = subprocess.run(
        cmd,
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        check=False,
        env=_run_environment(environment_key, provider_id, service_type_id),
    )
    return completed


def _test_sections() -> list[str]:
    tests_dir = ROOT_DIR / "tests"
    return sorted(
        _display_section_name(child.name)
        for child in tests_dir.iterdir()
        if child.is_dir() and not child.name.startswith("__")
    )


WORKORDER_ACTIONS = {
    "POST /v1/workorders/attachable": {"file": "test_post_workorders_attachable.py", "description": "Finds attachable MACD workorders with optional partial WorkorderNumber and CreatedBy filters; validates Pending summaries, response schema, security, compatibility, and performance."},
    "GET /workorders": {"file": "test_get_workorders.py", "description": "Lists workorders with pagination and status, requester, source, and workorder-number filters; validates response shape, database counts, security, and performance."},
    "GET /workorders/{number}": {"file": "test_get_workorder_by_number.py", "description": "Retrieves one workorder by number and validates provider summaries, status, dates, requester data, response shape, security, and database consistency."},
    "POST /v1/workorders/change-options": {"file": "test_post_workorders_change_options.py", "description": "Creates change-options workorders with service, option, requester, transaction, workorder, and compatibility validation."},
    "POST /v1/workorders/disconnect": {"file": "test_post_workorders_disconnect.py", "description": "Creates disconnect workorders with latest service-state, asset, warehouse, requester, due-date, and security validation."},
    "POST /v1/workorders/provision": {"file": "test_post_workorders_provision.py", "description": "Creates provision workorders using compatible provider, service type, contract, catalog, account, requester, cost center, accessory, and provider-contact data."},
    "POST /v1/workorders/support": {"file": "test_post_workorders_support.py", "description": "Creates support workorders and validates eligible service state, requester, due date, comments, and persisted workorder data."},
    "POST /v1/workorders/transfer-cost-center": {"file": "test_post_workorders_transfer_cost_center.py", "description": "Creates cost-center transfer workorders with service ownership, latest transaction/workorder, organization, requester, and compatibility checks."},
    "POST /v1/workorders/transfer-employee": {"file": "test_post_workorders_transfer_employee.py", "description": "Creates employee transfer workorders with service ownership, employee, cost-center, requester, and latest-record eligibility checks."},
    "POST /v1/workorders/upgrade": {"file": "test_post_workorders_upgrade.py", "description": "Creates upgrade workorders using compatible service, asset, catalog, contract, warehouse, requester, cost center, and latest-record validation."},
}

INVOICE_INTELLIGENCE_ACTIONS = {
    "IIT02 - Get Invoice Narrative": {
        "operation": "POST /v1/invoice-intelligence/GetInvoiceNarrative",
        "file": "test_post_get_invoice_narrative.py",
    },
}

VENDOR_MAP_ACTIONS = {
    "VMP01 · Fetch Devices": {
        "operation": "POST /v1/vendormap/devices",
        "file": "test_post_vendor_map_devices.py",
        "description": "Fetches paged VendorEquipmentMap devices with vendor, status, name, SKU, pagination, response, security, and database compatibility checks.",
    },
    "VMP02 · Approve Devices": {
        "operation": "POST /v1/vendormap/ApproveDevices",
        "file": "test_post_vendor_map_approve_devices.py",
        "description": "Approves selected VendorEquipmentMap devices and validates the related HardwareType Active state, bulk behavior, validation, authorization, and cross-vendor isolation.",
    },
    "VMP03 · Decline Devices": {
        "operation": "POST /v1/vendormap/DeclineDevices",
        "file": "test_post_vendor_map_decline_devices.py",
        "description": "Declines selected VendorEquipmentMap devices and validates the related HardwareType Not Active state, bulk behavior, validation, authorization, and cross-vendor isolation.",
    },
    "VMP04 · Fetch Accessories": {
        "operation": "POST /v1/vendormap/accessories",
        "file": "test_post_vendor_map_accessories.py",
        "description": "Fetches category-2 VendorAccessoryMap records with vendor, status, name, SKU, pagination, response, security, and database compatibility checks.",
    },
    "VMP05 · Approve Accessories": {
        "operation": "POST /v1/vendormap/approveAccessories",
        "file": "test_post_vendor_map_approve_accessories.py",
        "description": "Approves selected category-2 VendorAccessoryMap records and validates HardwareType Active persistence, bulk behavior, validation, authorization, and cross-vendor isolation.",
    },
    "VMP18 · Decline Accessories": {
        "operation": "POST /v1/vendormap/declineAccessories",
        "file": "test_post_vendor_map_decline_accessories.py",
        "description": "Declines selected category-2 VendorAccessoryMap records and validates HardwareType Not Active persistence, bulk behavior, validation, authorization, and cross-vendor isolation.",
    },
}

SELF_SERVICE_ACTIONS = {
    "REQ01 · Provision": {
        "operation": "POST /requestApi/SelfServiceRequests/Provision",
        "file": "test_post_self_service_provision.py",
        "implemented": True,
    },
    "REQ02 · Upgrade": {"operation": "POST /requestApi/SelfServiceRequests/Upgrade", "file": "test_post_self_service_upgrade.py", "implemented": True},
    "REQ03 · Transfer Owner": {"operation": "POST /requestApi/SelfServiceRequests/TransferOwner", "file": "test_post_self_service_transfer_owner.py", "implemented": True},
    "REQ04 · Transfer Liability Out": {"operation": "POST /requestApi/SelfServiceRequests/TransferOfLiabilityOut", "file": "test_post_self_service_liability_out.py", "implemented": True},
    "REQ05 · Travel Package": {"operation": "POST /requestApi/SelfServiceRequests/TravelPack", "file": "test_post_self_service_travel_package.py", "implemented": True},
    "REQ06 · Suspend": {"operation": "POST /requestApi/SelfServiceRequests/Suspend", "file": "test_post_self_service_suspend.py", "implemented": True},
    "REQ07 · Activate": {"operation": "POST /requestApi/SelfServiceRequests/Activate", "file": "test_post_self_service_activate.py", "implemented": True},
    "REQ08 · Transfer Liability In": {
        "operation": "POST /requestApi/SelfServiceRequests/TransferOfLiabilityIn",
        "file": "test_post_self_service_transfer_liability_in.py",
        "description": "Transfers a service to an employee or department and validates service, catalog/device, contract, option, accessory, requester, cost center, due-date, and comment rules.",
        "implemented": True,
    },
    "REQ09 · Disconnect": {
        "operation": "POST /requestApi/SelfServiceRequests/Disconnect",
        "file": "test_post_self_service_disconnect.py",
        "description": "Disconnects a service and validates asset return, warehouse, service identity, requester, due-date, comment, transaction, workorder, and approval rules.",
        "implemented": True,
    },
    "REQ10 · Additional Accessories": {
        "operation": "POST /requestApi/SelfServiceRequests/AdditionalAccessories",
        "file": "test_post_self_service_additional_accessories.py",
        "description": "Adds compatible accessories to an active service and validates service state, accessory compatibility and enablement, count limits, shipping address, cost center, requester, due-date, and comments.",
        "implemented": True,
    },
}


def _workorder_groups(dashboard_data: dict) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for section in dashboard_data.get("sections", []):
        for result in section.get("results", []):
            operation = str(result.get("operation", "")).strip()
            if operation in WORKORDER_ACTIONS:
                groups[operation].append(result)

    return [
        {
            "operation": operation,
            "file": WORKORDER_ACTIONS[operation]["file"],
            "description": WORKORDER_ACTIONS[operation]["description"],
            "results": groups.get(operation, []),
            "case_count": len({str(row.get("testrail_case_id", "")).strip() for row in groups.get(operation, [])}),
            "passed": sum(row.get("status") == "passed" for row in groups.get(operation, [])),
            "failed": sum(row.get("status") == "failed" for row in groups.get(operation, [])),
            "skipped": sum(row.get("status") == "skipped" for row in groups.get(operation, [])),
        }
        for operation in sorted(WORKORDER_ACTIONS)
    ]


def _vendor_map_groups(dashboard_data: dict) -> list[dict]:
    results_by_operation: dict[str, list[dict]] = defaultdict(list)
    for section in dashboard_data.get("sections", []):
        for result in section.get("results", []):
            operation = str(result.get("operation", "")).strip()
            if operation in {metadata["operation"] for metadata in VENDOR_MAP_ACTIONS.values()}:
                results_by_operation[operation].append(result)

    groups = []
    for label, metadata in VENDOR_MAP_ACTIONS.items():
        results = results_by_operation.get(metadata["operation"], [])
        groups.append({
            "label": label,
            "operation": metadata["operation"],
            "file": metadata["file"],
            "description": metadata["description"],
            "results": results,
            "case_count": len({str(row.get("testrail_case_id", "")).strip() for row in results if str(row.get("testrail_case_id", "")).strip()}),
            "passed": sum(row.get("status") == "passed" for row in results),
            "failed": sum(row.get("status") == "failed" for row in results),
            "skipped": sum(row.get("status") == "skipped" for row in results),
        })
    return groups


def _invoice_intelligence_groups(dashboard_data: dict) -> list[dict]:
    groups = []
    for label, metadata in INVOICE_INTELLIGENCE_ACTIONS.items():
        results = [
            result
            for section in dashboard_data.get("sections", [])
            for result in section.get("results", [])
            if result.get("operation") == metadata["operation"]
        ]
        statuses = {}
        for result in results:
            case_id = str(result.get("testrail_case_id") or result.get("nodeid", ""))
            status = result.get("status", "skipped")
            if TestRailClient._status_weight(status) > TestRailClient._status_weight(statuses.get(case_id, "")):
                statuses[case_id] = status
        groups.append({
            "label": label,
            **metadata,
            "results": results,
            "case_count": len(statuses),
            "passed": sum(status == "passed" for status in statuses.values()),
            "failed": sum(status == "failed" for status in statuses.values()),
            "skipped": sum(status == "skipped" for status in statuses.values()),
        })
    return groups


def _self_service_groups(dashboard_data: dict) -> list[dict]:
    results_by_operation: dict[str, list[dict]] = defaultdict(list)
    for section in dashboard_data.get("sections", []):
        for result in section.get("results", []):
            operation = str(result.get("operation", "")).strip()
            if operation.startswith("POST /requestApi/SelfServiceRequests/"):
                results_by_operation[operation].append(result)

    groups = []
    for label, metadata in SELF_SERVICE_ACTIONS.items():
        results = results_by_operation.get(metadata["operation"], [])
        test_file = ROOT_DIR / "tests" / "self_service_requests" / metadata["file"]
        implemented = metadata["implemented"] or test_file.exists() or bool(results)
        groups.append({
            "label": label,
            "operation": metadata["operation"],
            "file": metadata["file"],
            "implemented": implemented,
            "results": results,
            "case_count": len({str(row.get("testrail_case_id", "")).strip() for row in results}),
            "passed": sum(row.get("status") == "passed" for row in results),
            "failed": sum(row.get("status") == "failed" for row in results),
            "skipped": sum(row.get("status") == "skipped" for row in results),
        })
    return groups


def _pytest_target(scope_name: str) -> Path | None:
    tests_root = (ROOT_DIR / "tests").resolve()
    if scope_name.startswith("action:"):
        action_file = scope_name.removeprefix("action:").strip()
        if Path(action_file).name != action_file or not action_file.endswith(".py"):
            return None
        if action_file.startswith("test_post_self_service_"):
            folder = "self_service_requests"
        elif action_file.startswith("test_post_vendor_map_"):
            folder = "vendor_map"
        elif action_file in {metadata["file"] for metadata in INVOICE_INTELLIGENCE_ACTIONS.values()}:
            folder = "invoice_intelligence"
        else:
            folder = "workorders"
        target = tests_root / folder / action_file
        allowed_files = {
            metadata.get("file")
            for metadata in [*WORKORDER_ACTIONS.values(), *VENDOR_MAP_ACTIONS.values(), *SELF_SERVICE_ACTIONS.values(), *INVOICE_INTELLIGENCE_ACTIONS.values()]
        }
        return target if action_file in allowed_files and target.is_file() else None

    folder_name = next(
        (folder for folder, display in SECTION_DISPLAY_NAMES.items() if display.lower() == scope_name.lower()),
        scope_name.lower().replace(" ", "_"),
    )
    known_folders = {child.name.lower() for child in tests_root.iterdir() if child.is_dir() and not child.name.startswith("__")}
    if scope_name.lower() == "all" or folder_name.lower() not in known_folders:
        return None
    target = (tests_root / folder_name).resolve()
    try:
        target.relative_to(tests_root)
    except ValueError:
        return None
    return target if target.is_dir() and scope_name.lower() != "all" else None


def _pytest_case_mapping(scope_name: str) -> dict[str, list[dict]]:
    with _pytest_case_lock:
        cached = _pytest_case_cache.get(scope_name)
        now = time.monotonic()
        if cached and cached["expires_at"] > now:
            return cached["cases"]

        target = _pytest_target(scope_name)
        if target is None:
            raise ValueError(f"Unknown test section or action: {scope_name}")

        with tempfile.NamedTemporaryFile(prefix="dashboard-cases-", suffix=".json", delete=False) as output_file:
            output_path = Path(output_file.name)
        environment = os.environ.copy()
        environment["DASHBOARD_CASE_MAP_OUTPUT"] = str(output_path)
        command = [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "--confcutdir",
            str(ROOT_DIR / "tests"),
            "-p",
            "utils.pytest_case_map",
            str(target),
        ]
        try:
            completed = subprocess.run(command, cwd=ROOT_DIR, capture_output=True, text=True, check=False, env=environment, timeout=180)
            if completed.returncode:
                raise RuntimeError(completed.stderr[-2000:] or completed.stdout[-2000:] or "Pytest collection failed.")
            if not output_path.exists():
                raise RuntimeError("Pytest collection did not return a case map.")
            case_items = json.loads(output_path.read_text(encoding="utf-8"))
        finally:
            output_path.unlink(missing_ok=True)

        mapping: dict[str, list[dict]] = defaultdict(list)
        for item in case_items:
            mapping[item["case_id"]].append(item)
        result = dict(mapping)
        _pytest_case_cache[scope_name] = {"expires_at": time.monotonic() + _CASE_CACHE_TTL_SECONDS, "cases": result}
        return result


def _testrail_case_data() -> tuple[dict[int, str], list[dict]]:
    now = time.monotonic()
    with _case_cache_lock:
        if _case_data_cache["expires_at"] > now:
            return _case_data_cache["sections"], _case_data_cache["cases"]

        client = TestRailClient.from_env()
        limit = 250
        sections = []
        offset = 0
        while True:
            page = client.get_sections(limit=limit, offset=offset)
            sections.extend(page)
            if len(page) < limit:
                break
            offset += len(page)

        cases = []
        offset = 0
        while True:
            page = client.get_cases(limit=limit, offset=offset)
            cases.extend(page)
            if len(page) < limit:
                break
            offset += len(page)

        section_names = {int(item["id"]): str(item.get("name", "")) for item in sections if item.get("id") is not None}
        section_parents = {int(item["id"]): item.get("parent_id") for item in sections if item.get("id") is not None}

        def full_section_name(section_id: object) -> str:
            try:
                current_id = int(section_id)
            except (TypeError, ValueError):
                return "Unsectioned"
            names = []
            visited = set()
            while current_id in section_names and current_id not in visited:
                visited.add(current_id)
                names.append(section_names[current_id])
                parent_id = section_parents.get(current_id)
                if parent_id is None:
                    break
                current_id = int(parent_id)
            return " / ".join(reversed(names)) or "Unsectioned"

        for case in cases:
            case["dashboard_section"] = full_section_name(case.get("section_id"))
        _case_data_cache.update({"expires_at": now + _CASE_CACHE_TTL_SECONDS, "sections": section_names, "cases": cases})
        return section_names, cases


def _combined_dashboard(section_payloads: list[dict]) -> dict:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for payload in section_payloads:
        for section in payload.get("sections", []):
            name = str(section.get("name", "")).strip()
            if name:
                grouped[name].extend(section.get("results", []))

    all_results = [result for results in grouped.values() for result in results]
    counts = Counter(result.get("status") for result in all_results)
    by_section = {}
    for name, results in grouped.items():
        result_counts = Counter(result.get("status") for result in results)
        case_ids = {str(result.get("testrail_case_id", "")).strip() for result in results if str(result.get("testrail_case_id", "")).strip()}
        by_section[name] = {
            "total_cases": len(case_ids), "total_executions": len(results),
            "cases_passed": result_counts.get("passed", 0), "passed_executions": result_counts.get("passed", 0),
            "cases_failed": result_counts.get("failed", 0), "failed_executions": result_counts.get("failed", 0),
            "cases_skipped": result_counts.get("skipped", 0), "skipped_executions": result_counts.get("skipped", 0),
        }
    case_ids = {str(result.get("testrail_case_id", "")).strip() for result in all_results if str(result.get("testrail_case_id", "")).strip()}
    return {
        "generated_at": datetime.utcnow().isoformat(),
        "summary": {
            "total": len(all_results), "passed": counts.get("passed", 0), "failed": counts.get("failed", 0), "skipped": counts.get("skipped", 0),
            "total_cases": len(case_ids), "total_executions": len(all_results),
            "cases_passed": counts.get("passed", 0), "passed_executions": counts.get("passed", 0),
            "cases_failed": counts.get("failed", 0), "failed_executions": counts.get("failed", 0),
            "cases_skipped": counts.get("skipped", 0), "skipped_executions": counts.get("skipped", 0), "by_section": by_section,
        },
        "sections": [{"name": name, "results": results} for name, results in sorted(grouped.items())],
    }


def _execute_test_run(scope_name: str, environment_key: str | None = None, provider_id: int | None = None, service_type_id: int | None = None, node_ids: list[str] | None = None, section_names: list[str] | None = None):
    environment_key = environment_key or _default_environment_key()
    try:
        run_sections = section_names or (_test_sections() if scope_name.lower() == "all" else [scope_name])
        last_run = _load_last_run()
        section_status = last_run.get("sections", {})
        section_counts = last_run.get("section_counts", {})
        action_counts = last_run.get("action_counts", {})
        dashboard_payloads = []
        outputs = []
        exit_code = 0
        for section in run_sections:
            section_status[section] = "running"
            _save_last_run({**last_run, "sections": section_status, "current_section": section})
            completed = _run_pytest(section, environment_key, provider_id, service_type_id, node_ids if section == scope_name else None)
            exit_code = max(exit_code, completed.returncode)
            outputs.append(f"[{section}]\n{completed.stdout}\n{completed.stderr}".strip())
            dashboard_payloads.append(_load_dashboard_data())
            summary = dashboard_payloads[-1].get("summary", {})
            section_counts[section] = {
                "total": summary.get("total", 0),
                "passed": summary.get("passed", 0),
                "failed": summary.get("failed", 0),
                "skipped": summary.get("skipped", 0),
            }
            for result_section in dashboard_payloads[-1].get("sections", []):
                for result in result_section.get("results", []):
                    operation = str(result.get("operation", ""))
                    action_file = None
                    for operation_name, metadata in WORKORDER_ACTIONS.items():
                        if operation_name == operation:
                            action_file = metadata["file"]
                    for action in SELF_SERVICE_ACTIONS.values():
                        if action.get("operation") == operation:
                            action_file = action.get("file")
                    for action in VENDOR_MAP_ACTIONS.values():
                        if action.get("operation") == operation:
                            action_file = action.get("file")
                    for action in INVOICE_INTELLIGENCE_ACTIONS.values():
                        if action.get("operation") == operation:
                            action_file = action.get("file")
                    if action_file:
                        key = f"action:{action_file}"
                        counts = action_counts.setdefault(key, {"total": 0, "passed": 0, "failed": 0, "skipped": 0})
                        counts["total"] += 1
                        counts[result.get("status", "skipped")] = counts.get(result.get("status", "skipped"), 0) + 1
            for action in _invoice_intelligence_groups(dashboard_payloads[-1]):
                if action["results"]:
                    action_counts[f"action:{action['file']}"] = {
                        "total": action["case_count"],
                        "passed": action["passed"],
                        "failed": action["failed"],
                        "skipped": action["skipped"],
                    }
            section_status[section] = "completed" if completed.returncode == 0 else "failed"
            _save_last_run({**last_run, "sections": section_status, "section_counts": section_counts, "action_counts": action_counts, "current_section": ""})
        dashboard_data = _combined_dashboard(dashboard_payloads) if len(dashboard_payloads) > 1 or scope_name.lower() == "all" else dashboard_payloads[0]
        DASHBOARD_JSON.write_text(json.dumps(dashboard_data, indent=2), encoding="utf-8")
        pdf_path = _generate_pdf(dashboard_data, scope_name)
        publish_scope = _testrail_scope(scope_name)
        testrail_result = _integrate_testrail(publish_scope, dashboard_data)
        result_payload = {
            "scope": scope_name,
            "environment": environment_key,
            "environment_label": _environment_profile(environment_key)["label"],
            "status": "completed" if exit_code == 0 else "failed",
            "pytest_exit_code": exit_code,
            "pytest_output": "\n\n".join(outputs),
            "pdf_path": pdf_path,
            "pdf_file_name": Path(pdf_path).name,
            "testrail": testrail_result,
            "sections": section_status,
            "section_counts": section_counts,
            "action_counts": action_counts,
            "current_section": "",
            "generated_at": datetime.utcnow().isoformat(),
        }
    except Exception as exc:  # noqa: BLE001
        result_payload = {
            "scope": scope_name,
            "environment": environment_key,
            "environment_label": _environment_profile(environment_key)["label"],
            "status": "failed",
            "pytest_exit_code": 1,
            "pytest_output": "",
            "testrail": {"status": "failed", "message": str(exc)},
            "sections": _load_last_run().get("sections", {}),
            "section_counts": _load_last_run().get("section_counts", {}),
            "action_counts": _load_last_run().get("action_counts", {}),
            "current_section": "",
            "generated_at": datetime.utcnow().isoformat(),
        }
    _save_last_run(result_payload)
    return result_payload


def _get_schedule_store() -> ScheduleStore:
    global _schedule_store
    if _schedule_store is None:
        _schedule_store = ScheduleStore(SCHEDULE_DB)
    return _schedule_store


def _execute_scheduled_schedule(schedule: dict) -> dict:
    started_at = datetime.now().astimezone()
    selected_sections = schedule["sections"]
    all_sections = _test_sections()
    run_sections = all_sections if "all" in selected_sections else selected_sections
    scope_name = run_sections[0] if len(run_sections) == 1 else "all"
    result = _execute_test_run(
        scope_name,
        schedule["environment"],
        section_names=run_sections,
    )
    testrail = result.get("testrail", {})
    status = result.get("status", "failed")
    if testrail.get("status") == "failed":
        status = "failed"
    sections_text = ", ".join(run_sections)
    summary = (
        f"{status.title()} in {schedule['environment']} for {sections_text}. "
        f"TestRail: {testrail.get('status', 'unknown')} - {testrail.get('message', '')}"
    )
    SCHEDULE_LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_name = f"schedule-{schedule['id']}-run-{schedule['run_id']}-{stamp}.log"
    log_path = SCHEDULE_LOG_DIR / log_name
    log_path.write_text(
        "\n".join((
            f"Schedule: {schedule['name']}",
            f"Environment: {schedule['environment']}",
            f"Sections: {sections_text}",
            f"Scheduled for: {schedule['scheduled_for']}",
            f"Started: {started_at.isoformat(timespec='seconds')}",
            f"Finished: {datetime.now().astimezone().isoformat(timespec='seconds')}",
            f"Status: {status}",
            f"TestRail: {testrail.get('status', 'unknown')} - {testrail.get('message', '')}",
            "",
            result.get("pytest_output", ""),
        )),
        encoding="utf-8",
    )
    return {"status": status, "log_file": log_name, "summary": summary}


def start_schedule_worker() -> None:
    global _schedule_worker
    with _scheduler_start_lock:
        if _schedule_worker is not None and _schedule_worker.thread and _schedule_worker.thread.is_alive():
            return
        _schedule_worker = ScheduleWorker(
            _get_schedule_store(),
            _run_lock,
            _execute_scheduled_schedule,
        )
        _schedule_worker.start()


def _generate_pdf(dashboard_data: dict, section: str | None) -> str:
    pdf_file = PDF_DIR / pdf_name_for_run(section)
    build_dashboard_pdf(dashboard_data, pdf_file)
    return str(pdf_file)


def _integrate_testrail(section: str | None, dashboard_data: dict):
    enabled = os.getenv("TESTRAIL_ENABLE_ON_RUN", "1").strip() == "1"
    if not enabled:
        return {"status": "skipped", "message": "TestRail integration disabled."}

    section_results = _results_for_scope(dashboard_data, section)
    if not section_results:
        return {"status": "skipped", "message": "No results found to publish to TestRail."}

    case_ids, missing = _extract_section_case_ids(section_results)
    if missing:
        details = "; ".join(
            f"{item['section']} -> {item['test']}" for item in missing
        )
        return {
            "status": "failed",
            "message": (
                "TestRail case ID relation is missing for one or more tests. "
                f"Missing mappings: {details}"
            ),
        }

    try:
        client = TestRailClient.from_env()
        plan = client.create_plan(_plan_name(section), case_ids)
        publish = client.publish_results(plan, section_results)
        plan_id = plan.get("id")
        plan_url = ""
        if plan_id:
            plan_url = f"{client.config.url.rstrip('/')}/index.php?/plans/view/{plan_id}"

        return {
            "status": "ok",
            "message": f"Created TestRail plan {plan_id} and published {publish.get('published_results', 0)} results.",
            "plan_id": plan_id,
            "plan_url": plan_url,
        }
    except TestRailError as exc:
        return {"status": "failed", "message": str(exc)}


def _pretty_json_text(value: str) -> str:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return value
    return json.dumps(parsed, indent=2)


def _format_result_details(details: str) -> str:
    if not details:
        return "No details captured."

    formatted_lines: list[str] = []
    normalized = str(details).replace("\r\n", "\n")
    for raw_line in normalized.split("\n"):
        line = raw_line.strip()
        if not line:
            formatted_lines.append("")
            continue

        if ":" not in line:
            formatted_lines.append(line)
            continue

        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()

        if key in {"Request body", "Response payload"}:
            formatted_lines.append(f"{key}:")
            formatted_lines.append(_pretty_json_text(value) if value else "{}")
            continue

        formatted_lines.append(f"{key}: {value}" if value else f"{key}:")

    return "\n".join(formatted_lines).strip()


def _decorate_dashboard_output(dashboard_data: dict) -> dict:
    for section in dashboard_data.get("sections", []):
        for row in section.get("results", []):
            row["formatted_details"] = _format_result_details(row.get("details", ""))
    return dashboard_data


def _historical_failure_counts(dashboard_data: dict) -> dict[str, int]:
    if os.getenv("TESTRAIL_ENABLE_ON_RUN", "1").strip() != "1":
        return {}

    case_ids = {
        str(result.get("testrail_case_id", "")).strip()
        for section in dashboard_data.get("sections", [])
        for result in section.get("results", [])
        if str(result.get("testrail_case_id", "")).strip().isdigit()
    }
    if not case_ids:
        return {}

    try:
        client = TestRailClient.from_env()
    except Exception:  # noqa: BLE001
        return {}

    run_ids = {int(run.get("id")) for run in client.get_runs() if run.get("id") is not None}
    last_run = _load_last_run()
    last_run.setdefault("environment", _default_environment_key())
    plan_id = last_run.get("testrail", {}).get("plan_id")
    if plan_id:
        try:
            plan = client.get_plan(int(plan_id))
            run_ids.update(
                int(run.get("id"))
                for entry in plan.get("entries", [])
                for run in entry.get("runs", [])
                if run.get("id") is not None
            )
        except Exception:  # noqa: BLE001
            pass

    counts = {case_id: 0 for case_id in case_ids}
    for run_id in sorted(run_ids):
        try:
            results = client.get_results_for_run(run_id)
        except Exception:  # noqa: BLE001
            continue
        for result in results:
            case_id = str(result.get("case_id", "")).strip()
            if case_id in counts and (
                str(result.get("status", "")).lower() == "failed"
                or int(result.get("status_id", 0) or 0) == 5
            ):
                counts[case_id] += 1
    return counts


def _add_history_counts(dashboard_data: dict, counts: dict[str, int]) -> dict:
    for section in dashboard_data.get("sections", []):
        for result in section.get("results", []):
            case_id = str(result.get("testrail_case_id", "")).strip()
            if case_id in counts:
                result["historical_failed_count"] = counts[case_id]
    return dashboard_data


@app.get("/")
def index():
    dashboard_data = _decorate_dashboard_output(_load_dashboard_data())
    dashboard_data = _add_history_counts(dashboard_data, _historical_failure_counts(dashboard_data))
    sections = _discover_sections(dashboard_data)
    last_run = _load_last_run()
    last_run["provider_id"] = last_run.get("provider_id") or ""
    last_run["service_type_id"] = last_run.get("service_type_id") or ""
    if last_run.get("status") == "running" and not _run_lock.locked():
        last_run["status"] = "interrupted"
        last_run["current_section"] = ""
        last_run["testrail"] = {
            "status": "failed",
            "message": "The dashboard server restarted before the background run completed.",
        }
        _save_last_run(last_run)
    health = _connection_health()
    is_running = last_run.get("status") == "running"
    status_filter = request.args.get("status", "all").strip().lower()
    if status_filter not in {"all", "failed"}:
        status_filter = "all"

    template = """
<!DOCTYPE html>
<html lang=\"en\">
<head>
    <meta charset=\"utf-8\" />
    <div class=\"app-shell\">
    <aside class=\"sidebar\" id=\"sidebar\">
        <div class=\"brand\"><span class=\"brand-mark\">A</span><span>API Automation</span></div>
        <div class=\"nav-label\">Workspace</div>
        <button class=\"nav-btn active\" type=\"button\" data-tab-target=\"dashboard-tab\"><span class=\"nav-icon\">▦</span><span>Dashboard</span></button>
        <button class=\"nav-btn\" type=\"button\" data-tab-target=\"section-{{ section|lower }}-tab\"><span class=\"nav-icon\">{% if section|lower == 'workorders' %}↗{% elif section|lower == 'purpose' %}◎{% elif section|lower == 'rules' %}☷{% elif section|lower == 'notes' %}▤{% elif section|lower == 'location' %}⌖{% else %}•{% endif %}</span><span>{{ section }}</span></button>
    </aside>
    <main class=\"main\">
    <div class=\"container\">
    <style>
            <div class=\"page-heading\"><button class=\"btn secondary icon-btn mobile-menu\" id=\"menu-toggle\" type=\"button\" title=\"Toggle navigation\" aria-label=\"Toggle navigation\">☰</button><h1>API Dashboard Runner</h1></div>
        * { box-sizing: border-box; }
        body { font-family: 'Segoe UI', Tahoma, sans-serif; margin: 0; background: var(--canvas); color: var(--ink); }
        .app-shell { min-height: 100vh; display: flex; }
        .sidebar { width: 248px; flex: 0 0 248px; background: var(--surface); border-right: 1px solid var(--line); padding: 22px 14px; transition: width .2s ease, flex-basis .2s ease, padding .2s ease; }
        .sidebar.collapsed { width: 76px; flex-basis: 76px; padding-left: 10px; padding-right: 10px; }
        .brand { display: flex; align-items: center; gap: 10px; padding: 2px 10px 24px; color: var(--blue-dark); font-size: 19px; font-weight: 800; white-space: nowrap; overflow: hidden; }
        .brand-mark { width: 34px; height: 34px; border-radius: 9px; display: grid; place-items: center; color: #fff; background: var(--blue); font-weight: 800; }
        .nav-label { padding: 12px 10px 7px; color: var(--muted); font-size: 11px; font-weight: 800; text-transform: uppercase; letter-spacing: .08em; }
        .nav-btn { width: 100%; border: 0; background: transparent; color: var(--ink); border-radius: 8px; padding: 11px 10px; text-align: left; cursor: pointer; font: inherit; font-weight: 650; display: flex; align-items: center; gap: 10px; white-space: nowrap; overflow: hidden; }
        .nav-btn:hover, .nav-btn.active { color: var(--blue-dark); background: var(--blue-soft); }
        .nav-icon { width: 22px; text-align: center; font-size: 17px; flex: 0 0 22px; }
        .sidebar.collapsed .brand span, .sidebar.collapsed .nav-label, .sidebar.collapsed .nav-btn span:not(.nav-icon) { display: none; }
        .sidebar.collapsed .nav-btn { justify-content: center; }
        .main { min-width: 0; flex: 1; }
        .container { max-width: 1440px; margin: 0 auto; padding: 24px 28px 44px; }
        .topbar { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }
        .btn { border: 0; background: var(--blue); color: #fff; padding: 10px 14px; border-radius: 7px; cursor: pointer; font-weight: 700; text-decoration: none; display: inline-flex; align-items: center; justify-content: center; }
        .btn:hover { background: var(--blue-dark); }
        .btn.secondary { background: #e8f1ff; color: var(--blue-dark); }
        .card { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 16px; margin-top: 16px; box-shadow: 0 3px 12px rgba(31, 78, 121, .04); }
        .summary { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; }
        .summary .item { border: 1px solid var(--line); border-radius: 8px; padding: 12px; }
        .status { display: inline-block; border-radius: 999px; padding: 4px 10px; font-size: 12px; font-weight: 700; }
        .passed { background: #d1fae5; color: #065f46; }
        .failed { background: #fee2e2; color: #991b1b; }
        .skipped { background: #fef3c7; color: #92400e; }
        .health-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 12px; }
        .health-item { border: 1px solid var(--line); border-radius: 8px; padding: 12px; }
        .health-title { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
        .health-message { color: #4b5563; font-size: 13px; }
        table { width: 100%; border-collapse: collapse; margin-top: 8px; }
        th, td { text-align: left; border-bottom: 1px solid #e5e7eb; padding: 10px 8px; vertical-align: top; }
        th { font-size: 12px; color: #6b7280; text-transform: uppercase; }
        .actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
        .icon-btn { width: 38px; height: 38px; padding: 0; font-size: 20px; line-height: 1; }
        .actions form { display: inline-flex; }
        .filter-bar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 12px; }
        .filter-link { text-decoration: none; border: 1px solid #d1d5db; border-radius: 8px; padding: 7px 10px; color: #374151; }
        .filter-link.active { background: var(--blue); color: #fff; border-color: var(--blue); }
        pre { background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 10px; padding: 10px; white-space: pre-wrap; margin-top: 8px; }
        .output-cell { min-width: 320px; }
        .output-toggle summary { cursor: pointer; color: #0f5c78; font-weight: 600; }
        .output-toggle[open] summary { margin-bottom: 8px; }
        .output-toggle pre { margin-top: 0; max-height: 320px; overflow: auto; line-height: 1.35; }
        .run-banner { border-left: 4px solid var(--blue); background: var(--blue-soft); color: var(--blue-dark); }
        .running { background: #dbeafe; color: #1d4ed8; }
        .queued { background: #f3f4f6; color: #4b5563; }
        .completed { background: #d1fae5; color: #065f46; }
        .btn:disabled { cursor: not-allowed; opacity: 0.55; }
        .section-summary { cursor: pointer; display: flex; align-items: center; justify-content: space-between; gap: 12px; }
        .section-summary h2 { margin: 0; }
        .tab-panel { display: none; }
        .tab-panel.active { display: block; }
        .page-heading { display: flex; align-items: center; gap: 12px; }
        .page-heading h1 { margin: 0; }
        .mobile-menu { display: inline-flex; }
        .action-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(330px, 1fr)); gap: 14px; }
        .action-card { margin-top: 16px; }
        .action-card summary { list-style: none; cursor: pointer; }
        .action-card summary::-webkit-details-marker { display: none; }
        .action-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
        .action-heading h3 { margin: 0; font-size: 16px; }
        .action-meta { color: var(--muted); font-size: 12px; margin-top: 5px; }
        .action-stats { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 12px; }
        .action-stats .status { border-radius: 5px; }
        @media (max-width: 760px) { .sidebar { position: fixed; z-index: 5; height: 100vh; transform: translateX(-100%); transition: transform .2s ease; } .sidebar.open { transform: translateX(0); } .sidebar.collapsed { width: 248px; flex-basis: 248px; padding: 22px 14px; } .sidebar.collapsed .brand span, .sidebar.collapsed .nav-label, .sidebar.collapsed .nav-btn span:not(.nav-icon) { display: initial; } .sidebar.collapsed .nav-btn { justify-content: initial; } .main { width: 100%; } .container { padding: 18px 14px 32px; } .mobile-menu { display: inline-flex; } .topbar { align-items: flex-start; } }
    </style>
</head>
<body>
    <div class=\"container\">
        <div class=\"topbar\">
            <h1>API Dashboard Runner</h1>
            <div class=\"actions\">
                <a class=\"btn secondary\" href=\"{{ url_for('scheduler_page') }}\">Scheduler</a>
                <form method="get" action="{{ url_for('index') }}">
                    <button class="btn secondary icon-btn" type="submit" title="Refresh dashboard" aria-label="Refresh dashboard">&#x21bb;</button>
                </form>
                {% if last_run.get('pdf_path') %}
                <a class=\"btn secondary icon-btn\" href=\"{{ url_for('download_pdf', file_name=last_run.get('pdf_file_name')) }}\" title=\"Download PDF report\" aria-label=\"Download PDF report\">&#x2913;</a>
                {% endif %}
                <form method=\"post\" action=\"{{ url_for('run_tests') }}\">
                    <input type=\"hidden\" name=\"section\" value=\"all\" />
                    <button class=\"btn\" type=\"submit\" {% if is_running %}disabled{% endif %}>Run All</button>
                </form>
            </div>
        </div>

        <div class=\"tab-panel active\" id=\"dashboard-tab\">
        <div class=\"card\">
            <div class=\"topbar\">
                <h3>Connection Health</h3>
                <form method=\"get\" action=\"{{ url_for('index') }}\">
                    <button class=\"btn secondary\" type=\"submit\">Refresh Health</button>
                </form>
            </div>
            <div class=\"health-grid\">
                <div class=\"health-item\">
                    <div class=\"health-title\">
                        <strong>Database</strong>
                        <span class=\"status {{ health.get('db', {}).get('status', 'skipped') }}\">{{ health.get('db', {}).get('status', 'UNKNOWN').upper() }}</span>
                    </div>
                    <div class=\"health-message\">{{ health.get('db', {}).get('message', '') }}</div>
                </div>
                <div class=\"health-item\">
                    <div class=\"health-title\">
                        <strong>TestRail</strong>
                        <span class=\"status {{ health.get('testrail', {}).get('status', 'skipped') }}\">{{ health.get('testrail', {}).get('status', 'UNKNOWN').upper() }}</span>
                    </div>
                    <div class=\"health-message\">{{ health.get('testrail', {}).get('message', '') }}</div>
                </div>
            </div>
        </div>

        <div class=\"card\">
            <div class=\"topbar\">
                <h3>Summary section</h3>
                <div>

                {% if last_run.get('status') == 'running' %}
                <div class="card run-banner">
                    <strong>Tests are running: {{ last_run.get('current_section') or last_run.get('scope', 'All') }}</strong>
                    <div>Results refresh every 5 seconds. TestRail publication begins after all selected sections finish.</div>
                    <div class="filter-bar">
                        {% for section, section_status in last_run.get('sections', {}).items() %}
                        <span class="status {{ section_status }}">{{ section }}: {{ section_status.upper() }}</span>
                        {% endfor %}
                    </div>
                </div>
                {% elif last_run.get('generated_at') %}
                <div class="card">
                    <strong>Latest run: {{ last_run.get('scope', 'All') }}</strong>
                    <div>Status: {{ last_run.get('status', 'completed').upper() }} | Pytest exit code: {{ last_run.get('pytest_exit_code', 'n/a') }} | Updated: {{ last_run.get('generated_at') }}</div>
                </div>
                {% endif %}
                    {% if last_run.get('testrail', {}).get('plan_url') %}
                    <span>Test plan: <a href=\"{{ last_run.get('testrail', {}).get('plan_url') }}\" target=\"_blank\">{{ last_run.get('testrail', {}).get('plan_id') and 'TestRail Plan #' ~ last_run.get('testrail', {}).get('plan_id') or 'View plan' }}</a></span>
                    {% else %}
                    <span style=\"color:#6b7280;\">Test plan: not published yet</span>
                    {% endif %}
                </div>
            </div>
            <p style=\"color:#6b7280; font-size:13px; margin:0 0 10px;\">
                Cases = unique TestRail case IDs. Executions = pytest runs (a case can run under multiple scenarios).
            </p>
            <div class=\"summary-grid\" style=\"display:grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px;\">
                <div class=\"item\">
                    <strong>Total</strong>
                    <div>Cases: {{ dashboard.get('summary', {}).get('total_cases', 0) }}</div>
                    <div>Executions: {{ dashboard.get('summary', {}).get('total_executions', dashboard.get('summary', {}).get('total', 0)) }}</div>
                    {% for section, stats in dashboard.get('summary', {}).get('by_section', {}).items() %}
                    <div>{{ section }}: {{ stats.get('total_cases', 0) }} cases / {{ stats.get('total_executions', 0) }} exec</div>
                    {% endfor %}
                </div>
                <div class=\"item\">
                    <strong>Passed</strong>
                    <div>Cases: {{ dashboard.get('summary', {}).get('cases_passed', 0) }}</div>
                    <div>Executions: {{ dashboard.get('summary', {}).get('passed_executions', dashboard.get('summary', {}).get('passed', 0)) }}</div>
                    {% for section, stats in dashboard.get('summary', {}).get('by_section', {}).items() %}
                    <div>{{ section }}: {{ stats.get('cases_passed', 0) }} cases / {{ stats.get('passed_executions', 0) }} exec</div>
                    {% endfor %}
                </div>
                <div class=\"item\">
                    <strong>Failed</strong>
                    <div>Cases: {{ dashboard.get('summary', {}).get('cases_failed', 0) }}</div>
                    <div>Executions: {{ dashboard.get('summary', {}).get('failed_executions', dashboard.get('summary', {}).get('failed', 0)) }}</div>
                    {% for section, stats in dashboard.get('summary', {}).get('by_section', {}).items() %}
                    <div>{{ section }}: {{ stats.get('cases_failed', 0) }} cases / {{ stats.get('failed_executions', 0) }} exec</div>
                    {% endfor %}
                </div>
                <div class=\"item\">
                    <strong>Skipped</strong>
                    <div>Cases: {{ dashboard.get('summary', {}).get('cases_skipped', 0) }}</div>
                    <div>Executions: {{ dashboard.get('summary', {}).get('skipped_executions', dashboard.get('summary', {}).get('skipped', 0)) }}</div>
                    {% for section, stats in dashboard.get('summary', {}).get('by_section', {}).items() %}
                    <div>{{ section }}: {{ stats.get('cases_skipped', 0) }} cases / {{ stats.get('skipped_executions', 0) }} exec</div>
                    {% endfor %}
                </div>
            </div>
            <div class="filter-bar">
                <strong>Show:</strong>
                <a class="filter-link {% if status_filter == 'all' %}active{% endif %}" href="{{ url_for('index', status='all') }}">All test cases</a>
                <a class="filter-link {% if status_filter == 'failed' %}active{% endif %}" href="{{ url_for('index', status='failed') }}">Failed test cases</a>
            </div>
        </div>

        </div>

        <div class=\"tab-panel\" id=\"section-workorders-tab\">
        <div class=\"card\">
            <div class=\"topbar\">
                <div><h2 style=\"margin:0;\">Workorders</h2><div style=\"color:var(--muted); font-size:13px; margin-top:5px;\">Run one workorder action at a time for parallel tester workflows.</div></div>
                <form method=\"post\" action=\"{{ url_for('run_tests') }}\"><input type=\"hidden\" name=\"section\" value=\"workorders\" /><button class=\"btn\" type=\"submit\" {% if is_running %}disabled{% endif %}>Run All Workorders</button></form>
            </div>
        </div>
        <div class=\"action-grid\">
        {% for action in workorder_groups %}
        <details class=\"card action-card\" {% if action.failed %}open{% endif %}>
            <summary><div class=\"action-heading\"><h3>{{ action.operation.replace('POST /v1/workorders/', '') }}</h3><form method=\"post\" action=\"{{ url_for('run_tests') }}\" onclick=\"event.stopPropagation()\"><input type=\"hidden\" name=\"section\" value=\"action:{{ action.file }}\" /><button class=\"btn secondary\" type=\"submit\" {% if is_running %}disabled{% endif %}>Run action</button></form></div><div class=\"action-meta\">{{ action.file }} · {{ action.case_count }} mapped cases</div><div class=\"action-stats\"><span class=\"status passed\">{{ action.passed }} passed</span><span class=\"status failed\">{{ action.failed }} failed</span><span class=\"status skipped\">{{ action.skipped }} skipped</span></div></summary>
            <table><thead><tr><th>Status</th><th>Test case</th><th>TestRail</th><th>Output</th></tr></thead><tbody>
            {% for row in action.results if status_filter == 'all' or row.get('status', '') == status_filter %}
            <tr><td><span class=\"status {{ row.get('status', '') }}\">{{ row.get('status', '').upper() }}</span></td><td>{{ row.get('title', '') }}<br/><small>{{ row.get('nodeid', '') }}</small></td><td>{% if row.get('testrail_case_url') %}<a href=\"{{ row.get('testrail_case_url') }}\" target=\"_blank\">{{ row.get('testrail_case_id') }}</a>{% else %}{{ row.get('testrail_case_id', 'Not mapped') }}{% endif %}</td><td class=\"output-cell\"><details class=\"output-toggle\" {% if row.get('status', '') == 'failed' %}open{% endif %}><summary>Expand output</summary><pre>{{ row.get('formatted_details', row.get('details', '')) }}</pre></details></td></tr>
            {% endfor %}
            </tbody></table>
        </details>
        {% endfor %}
        {% if not workorder_groups %}<div class=\"card\"><p>No workorder results have been generated yet.</p></div>{% endif %}
        </div>
        </div>

        {% for section in sections if section|lower != 'workorders' %}
        <div class=\"tab-panel\" id=\"section-{{ section|lower }}-tab\">
            <div class=\"card\">
            {% set section_status = last_run.get('sections', {}).get(section, 'idle') %}
            <div class=\"topbar\"><div><h2 style=\"margin:0;\">{{ section }}</h2><div style=\"color:var(--muted); font-size:13px; margin-top:5px;\">Run this test area independently.</div></div><span class=\"status {{ section_status }}\">{{ section_status.upper() }}</span></div>
            <form method=\"post\" action=\"{{ url_for('run_tests') }}\" style=\"margin-top:14px;\"><input type=\"hidden\" name=\"section\" value=\"{{ section }}\" /><button class=\"btn\" type=\"submit\" {% if is_running %}disabled{% endif %}>Run {{ section }}</button></form>
            {% set sec = (dashboard.get('sections', []) | selectattr('name', 'equalto', section) | list) %}
            {% if sec %}<table><thead><tr><th>Status</th><th>Test case</th><th>TestRail</th><th>Output</th></tr></thead><tbody>
            {% for row in sec[0].get('results', []) if status_filter == 'all' or row.get('status', '') == status_filter %}<tr><td><span class=\"status {{ row.get('status', '') }}\">{{ row.get('status', '').upper() }}</span></td><td>{{ row.get('title', '') }}<br/><small>{{ row.get('nodeid', '') }}</small></td><td>{% if row.get('testrail_case_url') %}<a href=\"{{ row.get('testrail_case_url') }}\" target=\"_blank\">{{ row.get('testrail_case_id') }}</a>{% else %}{{ row.get('testrail_case_id', 'Not mapped') }}{% endif %}</td><td class=\"output-cell\"><details class=\"output-toggle\"><summary>Expand output</summary><pre>{{ row.get('formatted_details', row.get('details', '')) }}</pre></details></td></tr>{% endfor %}
            </tbody></table>{% else %}<p style=\"color:var(--muted); margin-top:18px;\">No results yet for this section. Run it to populate the report.</p>{% endif %}
            </div>
        </div>
        {% endfor %}

    </div>
    </main>
    </div>
{% if last_run.get('status') == 'running' %}
<script>window.setTimeout(function () { window.location.reload(); }, 5000);</script>
{% endif %}
<script>
    const sidebar = document.getElementById('sidebar');
    const menuToggle = document.getElementById('menu-toggle');
    if (menuToggle) menuToggle.addEventListener('click', function () {
        if (window.innerWidth <= 760) sidebar.classList.toggle('open');
        else sidebar.classList.toggle('collapsed');
    });
    document.querySelectorAll('[data-tab-target]').forEach(function (button) {
        button.addEventListener('click', function () {
            document.querySelectorAll('.tab-panel').forEach(function (panel) { panel.classList.remove('active'); });
            document.querySelectorAll('[data-tab-target]').forEach(function (item) { item.classList.remove('active'); });
            document.getElementById(button.dataset.tabTarget).classList.add('active');
            button.classList.add('active');
            sidebar.classList.remove('open');
        });
    });
</script>
</body>
</html>
"""

    template = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>API Dashboard Runner</title>
    <style>
        :root { --upland-blue: #2574db; --upland-dark: #1654a5; --blue-soft: #eaf3ff; --ink: #172b4d; --muted: #65758b; --line: #dbe5f1; --canvas: #f5f8fc; --surface: #fff; }
        * { box-sizing: border-box; }
        body { margin: 0; background: var(--canvas); color: var(--ink); font-family: 'Open Sans', sans-serif; }
        .app-shell { display: grid; grid-template-columns: 248px minmax(0, 1fr); grid-template-rows: auto minmax(0, 1fr); height: 100vh; overflow: hidden; transition: grid-template-columns .2s ease; }
        .app-shell:has(.sidebar.collapsed) { grid-template-columns: 76px minmax(0, 1fr); }
        .sidebar { grid-column: 1; grid-row: 2; width: 248px; min-height: 0; overflow-y: auto; padding: 18px 14px; background: var(--surface); border-right: 1px solid var(--line); transition: width .2s ease, padding .2s ease; }
        .sidebar.collapsed { width: 76px; padding-left: 10px; padding-right: 10px; overflow: hidden; }
        .sidebar.collapsed .nav-label, .sidebar.collapsed .nav-btn > span:not(.nav-caret), .sidebar.collapsed .nav-caret { display: none; }
        .sidebar.collapsed .nav-search { display: none; }
        .sidebar.collapsed .nav-btn { justify-content: center; width: 56px; padding-left: 0; padding-right: 0; }
        .sidebar.collapsed .nav-group { display: block; }
        .brand { display: none; }
        .nav-label { display: block; padding: 10px 14px 8px; color: var(--muted); font-size: 12px; font-weight: 600; }
        .nav-btn { display: flex; align-items: center; gap: 8px; width: 100%; min-height: 40px; padding: 9px 14px; border: 0; border-radius: 4px; background: transparent; color: #20344d; cursor: pointer; font-family: 'Open Sans', sans-serif; font-size: 14px; font-weight: 600; text-align: left; text-decoration: none; }
        .nav-btn:hover, .nav-btn.active { background: #edf0f2; color: var(--ink); }
        .nav-group { display: block; margin-bottom: 2px; }
        .nav-submenu { display: none; margin: 0 0 4px 10px; padding: 2px 0 2px 12px; border-left: 2px solid #dbe5f1; }
        .nav-group.expanded > .nav-submenu { display: block; }
        #invoice-intelligence-nav-group .nav-sub-btn { display: flex; width: 100%; min-height: 38px; gap: 6px; white-space: normal; align-items: center; }
        #invoice-intelligence-nav-group .nav-sub-btn > span:first-child { min-width: 0; flex: 1; overflow-wrap: anywhere; }
        #invoice-intelligence-nav-group > .nav-btn > span:first-child { min-width: 0; overflow-wrap: anywhere; }
        #invoice-intelligence-nav-group .nav-count { flex-shrink: 0; }
        .nav-search { display: block; width: 100%; min-height: 38px; margin: 6px 0 12px; padding: 8px 10px; border: 1px solid var(--line); border-radius: 6px; font: inherit; }
        .nav-sub-btn { display: flex; align-items: center; justify-content: space-between; gap: 8px; width: 100%; min-height: 38px; padding: 8px 10px; border: 0; border-radius: 4px; background: transparent; color: #596c82; cursor: pointer; font-family: 'Open Sans', sans-serif; font-size: 13px; font-weight: 500; text-align: left; }
        .nav-caret { margin-left: auto; color: var(--muted); font-size: 14px; }
        .nav-sub-btn:hover, .nav-sub-btn.active { background: var(--blue-soft); color: var(--upland-dark); }
        .nav-btn[data-tab-target="section-purpose-tab"], .nav-btn[data-tab-target="section-notes-tab"] { display: none; }
        .sidebar.collapsed .nav-submenu { display: none; }
        .nav-icon { display: none; }
        .main { display: contents; }
        .app-header { position: sticky; top: 0; z-index: 8; grid-column: 1 / -1; grid-row: 1; display: flex; align-items: center; justify-content: space-between; gap: 16px; min-height: 76px; padding: 14px 28px; background: var(--upland-blue); color: #fff; }
        .header-title { display: flex; align-items: center; gap: 12px; }
        .header-title h1 { margin: 0; font-family: 'Open Sans', sans-serif; font-size: 20px; font-weight: 700; color: #fff; }
        .header-mark { display: grid; place-items: center; width: 40px; height: 40px; border-radius: 50%; background: #fff; color: var(--upland-blue); font-family: 'Open Sans', sans-serif; font-size: 25px; font-weight: 800; line-height: 1; text-align: center; }
        .header-actions, .health-inline { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
        .health-inline { margin-left: auto; }
        .health-inline form { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
        .health-chip { display: inline-flex; align-items: center; gap: 7px; padding: 7px 10px; border: 1px solid rgba(255,255,255,.35); border-radius: 6px; background: rgba(255,255,255,.12); font-size: 12px; }
        .health-chip strong { font-size: 11px; }
        .health-chip .status { padding: 0; background: transparent; color: transparent; width: 13px; height: 13px; border-radius: 50%; }
        .health-chip .status.passed { background: #8ee6b4; }
        .health-chip .status.failed { background: #ff8b8b; }
        .health-chip .result-icon { width: 13px; height: 13px; padding: 0; border-radius: 50%; font-size: 0; background: transparent; }
        .health-chip .result-icon.passed { background: #8ee6b4; }
        .health-chip .result-icon.failed { background: #ff8b8b; }
        .environment-select { min-height: 34px; padding: 7px 28px 7px 9px; border: 1px solid rgba(255,255,255,.45); border-radius: 6px; background: #fff; color: var(--upland-dark); font: inherit; font-size: 12px; font-weight: 700; }
        .section-environment { border-color: var(--line); }
        .seed-filter { width: 108px; min-height: 34px; padding: 7px 9px; border: 1px solid rgba(255,255,255,.45); border-radius: 6px; background: #fff; color: var(--upland-dark); font: inherit; font-size: 12px; }
        .seed-filter.section-filter { border-color: var(--line); }
        .compat-check { margin-top: 10px; }
        .compat-result { margin-top: 8px; }
        .compat-result table { width: 100%; border-collapse: collapse; }
        .compat-result th, .compat-result td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--line); font-size: 12px; }
        .container { grid-column: 2; grid-row: 2; width: 100%; min-height: 0; overflow-y: auto; max-width: 1440px; margin: 0 auto; padding: 36px 52px 56px; }
        .tab-panel { display: none; }
        .tab-panel.active { display: block; }
        .card { margin-top: 16px; padding: 16px; border: 1px solid var(--line); border-radius: 9px; background: var(--surface); box-shadow: 0 3px 12px rgba(31,78,121,.04); }
        .section-head, .action-heading { display: flex; align-items: center; justify-content: space-between; gap: 18px; }
        .section-head form, .action-heading form { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
        .invoice-panel > .card { min-width: 0; overflow-x: auto; }
        .invoice-panel .section-head { display: grid; grid-template-columns: minmax(0, 1fr); gap: 12px; }
        .invoice-panel .section-head h2 { font-size: 22px; line-height: 1.3; }
        .invoice-panel .section-head .muted { margin-top: 4px; overflow-wrap: anywhere; }
        .invoice-panel .section-head form { width: 100%; justify-content: flex-start; }
        h2, h3 { margin: 0; }
        .muted { color: var(--muted); font-size: 13px; }
        .btn { display: inline-flex; align-items: center; justify-content: center; padding: 9px 13px; border: 0; border-radius: 6px; background: var(--upland-blue); color: #fff; cursor: pointer; font-weight: 700; text-decoration: none; }
        .btn:hover { background: var(--upland-dark); }
        .btn.secondary { background: var(--blue-soft); color: var(--upland-dark); }
        .btn.run-all { min-width: 112px; padding: 11px 18px; background: #fff; color: var(--upland-dark); box-shadow: 0 2px 0 rgba(0,0,0,.12); }
        .btn.run-all:hover { background: #eef5ff; }
        .btn.icon { width: 38px; height: 38px; padding: 0; font-size: 18px; }
        .btn:disabled { cursor: not-allowed; opacity: .55; }
        .summary-grid { display: grid; grid-template-columns: repeat(4, minmax(130px, 1fr)); gap: 10px; margin-top: 14px; }
        .summary-item { padding: 12px; border: 1px solid var(--line); border-radius: 7px; }
        .summary-item.summary-filter { cursor: pointer; transition: border-color .15s ease, background .15s ease; }
        .summary-item.summary-filter:hover, .summary-item.summary-filter.active { border-color: var(--upland-blue); background: var(--blue-soft); }
        .summary-item strong { display: block; margin-bottom: 5px; }
        .guide { margin-top: 14px; border: 1px solid var(--line); border-radius: 7px; background: #fbfdff; }
        .guide summary { padding: 11px 12px; cursor: pointer; color: var(--upland-dark); font-weight: 800; }
        .guide-content { display: grid; grid-template-columns: repeat(2, minmax(220px, 1fr)); gap: 12px; padding: 0 12px 12px; }
        .guide-item { padding: 10px; border-left: 3px solid #b9d5f7; background: #fff; }
        .guide-item strong { display: block; margin-bottom: 4px; font-size: 12px; color: var(--upland-dark); }
        .guide-item span { color: var(--muted); font-size: 13px; line-height: 1.45; }
        .guide-item ul { margin: 0; padding-left: 18px; color: var(--muted); font-size: 13px; line-height: 1.5; }
        .guide-item li { padding-left: 3px; margin: 3px 0; }
        .status { display: inline-block; padding: 4px 9px; border-radius: 999px; font-size: 11px; font-weight: 800; }
        .passed, .completed { background: #d7f5e5; color: #146c43; }
        .failed { background: #fde0e0; color: #a61b1b; }
        .skipped, .queued { background: #fff0c2; color: #805d00; }
        .running { background: #dceaff; color: #1859ad; }
        .idle { background: #edf1f5; color: var(--muted); }
        .result-icon { display: inline-grid; place-items: center; width: 22px; height: 22px; border-radius: 50%; font-size: 14px; font-weight: 900; line-height: 1; }
        .result-icon.passed { background: #c9f2dc; color: #087443; }
        .result-icon.failed { background: #ffd4d4; color: #b51e2b; }
        .result-icon.skipped { background: #ffedb0; color: #856300; }
        .running-action { display: inline-flex; align-items: center; gap: 7px; }
        .spinner { display: inline-block; width: 13px; height: 13px; border: 2px solid currentColor; border-right-color: transparent; border-radius: 50%; animation: spin .7s linear infinite; }
        @keyframes spin { to { transform: rotate(360deg); } }
        .qa-runner-track { position: fixed; inset: 0; z-index: 15; overflow: hidden; pointer-events: none; }
        .qa-runner { position: absolute; left: 8px; bottom: 16px; --qa-runner-end: calc(100vw - 320px); animation: qa-cross-screen 10s linear infinite alternate; }
        .qa-runner-group { display: flex; align-items: flex-end; gap: 8px; }
        .qa-robot-wrap { position: relative; flex: 0 0 94px; width: 94px; height: 116px; transform-origin: center bottom; animation: qa-bob .32s ease-in-out infinite alternate; }
        .qa-robot-image { position: relative; z-index: 1; display: block; width: 100%; height: 100%; object-fit: contain; }
        .qa-run-label { display: grid; gap: 1px; max-width: 205px; margin-bottom: 12px; padding: 7px 10px; border: 1px solid #c9dcf3; border-radius: 8px; background: rgba(255,255,255,.97); color: var(--ink); box-shadow: 0 4px 14px rgba(23,43,77,.12); font-size: 11px; }
        .qa-run-label strong { color: var(--upland-dark); font-size: 12px; }
        .qa-run-label span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .qa-rocket-flame { position: absolute; z-index: 0; bottom: -18px; left: calc(50% - 10px); width: 20px; height: 34px; clip-path: polygon(50% 100%, 8% 48%, 28% 8%, 43% 39%, 73% 0, 92% 53%); background: #f38181; opacity: 0; }
        .qa-rocket-flame::after { position: absolute; inset: 8px 5px 1px; background: #ffc857; content: ""; clip-path: polygon(50% 100%, 0 30%, 48% 0, 100% 34%); }
        .qa-runner.is-launching .qa-robot-wrap { animation: qa-launch 1.15s cubic-bezier(.2,.72,.24,1) forwards; }
        .qa-runner.is-launching .qa-rocket-flame { opacity: 1; animation: qa-flame .12s ease-in-out infinite alternate; }
        .qa-runner.is-launching .qa-run-label { animation: qa-fade .3s ease-out forwards; }
        @keyframes qa-cross-screen { from { left: 8px; } to { left: var(--qa-runner-end); } }
        @keyframes qa-bob { to { transform: translateY(-5px) rotate(2deg); } }
        @keyframes qa-launch { 0% { transform: translateY(0) scale(1); } 18% { transform: translateY(10px) scale(1.08); } 100% { transform: translateY(-110vh) scale(.22) rotate(12deg); } }
        @keyframes qa-flame { to { transform: scaleY(1.25); } }
        @keyframes qa-fade { to { opacity: 0; transform: translateY(8px); } }
        @media (max-width: 800px) { .qa-runner { bottom: 8px; --qa-runner-end: calc(100vw - 230px); } .qa-robot-wrap { flex-basis: 68px; width: 68px; height: 86px; } .qa-run-label { max-width: 145px; padding: 5px 7px; font-size: 10px; } .qa-run-label strong { font-size: 11px; } }
        @media (prefers-reduced-motion: reduce) { .qa-runner { left: 14px; animation: none; } .qa-robot-wrap, .qa-runner.is-launching .qa-robot-wrap { animation: none; } .qa-runner.is-launching { visibility: hidden; } }
        .action-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(330px, 1fr)); gap: 14px; }
        .action-card { margin-top: 16px; }
        .action-card summary { cursor: pointer; list-style: none; }
        .action-card summary::-webkit-details-marker { display: none; }
        .action-meta { margin-top: 5px; color: var(--muted); font-size: 12px; }
        .action-stats { display: flex; gap: 6px; margin-top: 11px; flex-wrap: wrap; }
        .action-stats .status { border-radius: 5px; }
        .nav-count { margin-left: auto; min-width: 34px; padding: 2px 6px; border-radius: 999px; background: var(--blue-soft); color: var(--upland-dark); font-size: 10px; font-weight: 800; text-align: center; }
        .nav-sub-btn .nav-count { margin-left: 6px; }
        .action-description { margin: 8px 0 0; color: var(--muted); font-size: 12px; line-height: 1.4; }
        .run-toast { position: fixed; right: 22px; bottom: 22px; z-index: 20; max-width: 360px; padding: 13px 16px; border: 1px solid #b9d5f7; border-radius: 8px; background: #fff; color: var(--ink); box-shadow: 0 8px 26px rgba(23,43,77,.18); transform: translateY(18px); opacity: 0; pointer-events: none; transition: opacity .2s ease, transform .2s ease; }
        .run-toast.visible { transform: translateY(0); opacity: 1; }
        .filter-bar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 14px; }
        .filter-link { padding: 7px 10px; border: 1px solid var(--line); border-radius: 6px; color: var(--ink); text-decoration: none; }
        .filter-link.active { background: var(--upland-blue); border-color: var(--upland-blue); color: #fff; }
        table { width: 100%; margin-top: 12px; border-collapse: collapse; }
        th, td { padding: 9px 7px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
        th { color: var(--muted); font-size: 11px; text-transform: uppercase; }
        .output-cell { min-width: 320px; width: 34%; }
        .output-toggle { width: 100%; }
        .output-toggle summary { cursor: pointer; color: var(--upland-dark); font-weight: 700; }
        pre { max-height: 420px; overflow: auto; margin: 8px 0 0; padding: 12px; border: 1px solid var(--line); border-radius: 7px; background: #f8fbff; white-space: pre-wrap; word-break: break-word; line-height: 1.45; }
        .plan-link { margin-top: 10px; font-size: 13px; }
        .plan-link a { color: var(--upland-dark); font-weight: 700; }
        .case-dialog { width: min(760px, calc(100vw - 28px)); max-height: min(82vh, 760px); padding: 0; border: 1px solid var(--line); border-radius: 8px; color: var(--ink); box-shadow: 0 20px 60px rgba(23,43,77,.25); }
        .case-dialog::backdrop { background: rgba(23,43,77,.48); }
        .case-dialog-inner { display: flex; max-height: inherit; flex-direction: column; padding: 18px; }
        .case-dialog-head, .case-dialog-foot { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
        .case-dialog-head h2 { font-size: 20px; }
        .case-search { width: 100%; min-height: 38px; margin: 14px 0 8px; padding: 8px 10px; border: 1px solid var(--line); border-radius: 6px; font: inherit; }
        .case-list { min-height: 120px; overflow: auto; border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }
        .case-row { display: grid; grid-template-columns: 22px minmax(0, 1fr); gap: 10px; padding: 10px 4px; border-bottom: 1px solid var(--line); }
        .case-row:last-child { border-bottom: 0; }
        .case-row input { margin-top: 3px; }
        .case-row-title { font-weight: 650; }
        .case-row-meta { margin-top: 3px; color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
        .case-dialog-foot { padding-top: 12px; }
        .mobile-menu { display: inline-flex; }
        @media (max-width: 800px) { .app-shell { display: grid; grid-template-columns: minmax(0, 1fr); grid-template-rows: 76px minmax(0, 1fr); height: 100dvh; overflow: hidden; } .app-header { grid-column: 1; padding: 12px 14px; } .header-title { min-width: 0; } .header-title h1 { min-width: 0; font-size: 15px; line-height: 1.2; } .header-mark { width: 32px; height: 32px; font-size: 20px; } .sidebar { position: fixed; z-index: 7; top: 76px; bottom: 0; left: 0; width: min(300px, 86vw); max-height: calc(100dvh - 76px); overflow-y: auto; transform: translateX(-100%); } .sidebar.open { transform: translateX(0); } .sidebar.collapsed { width: min(300px, 86vw); transform: translateX(-100%); } .container { grid-column: 1; grid-row: 2; height: calc(100dvh - 76px); min-height: 0; overflow-y: auto; padding: 16px 14px 30px; } .summary-grid { grid-template-columns: repeat(2, 1fr); } .health-inline { display: none; } .section-head { align-items: flex-start; flex-direction: column; } .section-head form { width: 100%; } .qa-run-banner { gap: 10px; padding: 8px 10px; } .qa-run-illustration { flex-basis: 96px; width: 96px; height: 64px; } .qa-run-copy strong { font-size: 14px; } .qa-run-copy span { font-size: 12px; } .qa-run-banner .status { padding: 4px 6px; } .case-dialog-inner { padding: 14px; } }
    </style>
</head>
<body>
<div class="app-shell">
{% macro result_icon(status) -%}<span class="result-icon {{ status }}" role="img" aria-label="{{ status|capitalize }}">{% if status == 'passed' %}✓{% elif status == 'failed' %}!{% else %}–{% endif %}</span>{%- endmacro %}
{% macro health_dot(status) -%}<span class="health-status {{ 'passed' if status == 'passed' else 'failed' }}" role="img" aria-label="{{ status|capitalize }}"></span>{%- endmacro %}
    <aside class="sidebar" id="sidebar">
        <div class="nav-label">Workspace</div>
        <input class="nav-search" id="nav-search" type="search" placeholder="Search APIs or actions" aria-label="Search APIs and actions" />
        {% if 'Purpose' in sections or 'Notes' in sections %}<div class="nav-group expanded" id="inventory-management-nav-group"><button class="nav-btn" type="button"><span class="nav-icon">▦</span><span>Inventory Management</span><span class="nav-caret">⌄</span></button><div class="nav-submenu">{% for child in ['Purpose', 'Notes'] %}{% if child in sections %}{% set child_stats = dashboard.get('summary', {}).get('by_section', {}).get(child, {}) %}<button class="nav-sub-btn" type="button" data-tab-target="section-{{ child|lower }}-tab" data-section-scope="{{ child }}"><span>{{ child }}</span><span class="nav-count">{{ child_stats.get('cases_passed', 0) }}/{{ child_stats.get('total_cases', 0) }}</span></button>{% endif %}{% endfor %}</div></div>{% endif %}
        {% for section in sections %}
        {% if section == 'Invoice Intelligence' %}
        <div class="nav-group expanded" id="payment-readiness-nav-group">
            <button class="nav-btn" type="button"><span>Payment Readiness</span><span class="nav-caret">&#8964;</span></button>
            <div class="nav-submenu">
                {% set invoice_stats = dashboard.get('summary', {}).get('by_section', {}).get(section, {}) %}
                <div class="nav-group expanded" id="invoice-intelligence-nav-group">
                    <button class="nav-btn" type="button" data-tab-target="section-invoice_intelligence-tab" data-section-scope="Invoice Intelligence"><span>Invoice Intelligence</span><span class="nav-count">{{ invoice_stats.get('cases_passed', 0) }}/{{ invoice_stats.get('total_cases', 0) }}</span><span class="nav-caret">&#8964;</span></button>
                    <div class="nav-submenu">
                        {% for action in invoice_intelligence_groups %}
                        <button class="nav-sub-btn" type="button" data-action-scope="action:{{ action.file }}" data-tab-target="invoice-intelligence-action-page-{{ loop.index }}"><span>{{ action.label }}</span><span class="nav-count">{{ action.passed }}/{{ action.case_count }}</span></button>
                        {% endfor %}
                    </div>
                </div>
            </div>
        </div>
        {% else %}
        {% if section|lower == 'self service requests' %}<div class="nav-group expanded" id="self-service-nav-group"><button class="nav-btn {% if loop.first %}active{% endif %}" type="button" data-tab-target="section-self_service_requests-tab"><span class="nav-icon">•</span><span>Self Service Requests</span><span class="nav-caret">⌄</span></button><div class="nav-submenu">{% for action in self_service_groups %}<button class="nav-sub-btn" type="button" data-action-scope="action:{{ action.file }}" data-tab-target="self-service-action-page-{{ loop.index }}">{{ action.label }}<span class="nav-count">{{ action.passed }}/{{ action.case_count }}</span></button>{% endfor %}</div></div>{% elif section|lower == 'workorders' %}<div class="nav-group expanded" id="workorders-nav-group"><button class="nav-btn {% if loop.first %}active{% endif %}" type="button" data-tab-target="section-workorders-tab"><span class="nav-icon">↗</span><span>Workorders</span><span class="nav-caret">⌄</span></button><div class="nav-submenu">{% for action in workorder_groups %}<button class="nav-sub-btn" type="button" data-action-scope="action:{{ action.file }}" data-tab-target="workorder-action-page-{{ loop.index }}">{{ action.operation.replace('POST /v1/workorders/', '') }}<span class="nav-count">{{ action.passed }}/{{ action.case_count }}</span></button>{% endfor %}</div></div>{% elif section|lower == 'sync management (vendor map)' %}<div class="nav-group expanded" id="vendor-map-nav-group"><button class="nav-btn {% if loop.first %}active{% endif %}" type="button" data-tab-target="section-sync_management_(vendor_map)-tab"><span class="nav-icon">⇄</span><span>Sync Management (Vendor Map)</span><span class="nav-caret">⌄</span></button><div class="nav-submenu">{% for action in vendor_map_groups %}<button class="nav-sub-btn" type="button" data-action-scope="action:{{ action.file }}" data-tab-target="vendor-map-action-page-{{ loop.index }}">{{ action.label }}<span class="nav-count">{{ action.passed }}/{{ action.case_count }}</span></button>{% endfor %}</div></div>{% else %}<button class="nav-btn {% if loop.first %}active{% endif %}" type="button" data-tab-target="section-{{ section|lower|replace(' ', '_') }}-tab"><span class="nav-icon">{% if section|lower == 'purpose' %}◎{% elif section|lower == 'rules' %}☷{% elif section|lower == 'notes' %}▤{% elif section|lower == 'location' %}⌖{% else %}•{% endif %}</span><span>{{ section }}</span></button>{% endif %}
        {% endif %}
        {% endfor %}
        <button class="nav-btn" type="button" data-tab-target="cancel-workorders-tab"><span class="nav-icon">✕</span><span>Cancel Work Orders</span></button>
        <button class="nav-btn" type="button" data-tab-target="cancel-self-service-tab"><span class="nav-icon">✕</span><span>Cancel Self Service Requests</span></button>
    </aside>
    <main class="main">
        <header class="app-header">
            <div class="header-title"><button class="btn secondary icon mobile-menu" id="mobile-menu-toggle" type="button" aria-label="Toggle navigation">☰</button><span class="header-mark">U</span><h1>Smart API &amp; Data Verification Dashboard - CIMPL</h1></div>
            <div class="health-inline"><a class="btn secondary" href="{{ url_for('scheduler_page') }}">Scheduler</a><span class="health-chip"><strong>DB</strong>{{ result_icon(health.get('db', {}).get('status', 'unknown')) }}</span><span class="health-chip"><strong>TestRail</strong>{{ result_icon(health.get('testrail', {}).get('status', 'unknown')) }}</span>{% if is_running %}<span class="health-chip"><span class="spinner"></span><strong>Running {{ last_run.get('scope', 'tests') }}</strong></span>{% endif %}<form method="post" action="{{ url_for('run_tests') }}"><input type="hidden" name="section" value="all" /><select class="environment-select" name="environment" aria-label="Test environment" {% if is_running %}disabled{% endif %}>{% for key, profile in test_environments.items() %}<option value="{{ key }}" {% if key == last_run.get('environment', default_environment) %}selected{% endif %}>{{ profile.label }}</option>{% endfor %}</select><input class="seed-filter" name="provider_id" placeholder="Provider" value="{{ last_run.get('provider_id', '') }}" aria-label="Optional provider ID" {% if is_running %}disabled{% endif %} /><input class="seed-filter" name="service_type_id" placeholder="Service type" value="{{ last_run.get('service_type_id', '') }}" aria-label="Optional service type ID" {% if is_running %}disabled{% endif %} /><button class="btn run-all" type="submit" {% if is_running %}disabled{% endif %}>{% if is_running %}<span class="running-action"><span class="spinner"></span>Running…</span>{% else %}▶ Run All{% endif %}</button></form></div>
        </header>
        <div class="container">
        {% if last_run.get('generated_at') %}
        <div class="card">
            <div class="section-head">
                <div><h2>Latest run</h2><div class="muted">{{ last_run.get('scope', 'All') }} · {{ last_run.get('environment_label', last_run.get('environment', '')) }} · {{ last_run.get('generated_at') }}</div></div>
                <span class="status {{ last_run.get('status', 'idle') }}">{{ last_run.get('status', 'idle')|upper }}</span>
            </div>
            <p class="muted">Pytest exit code: {{ last_run.get('pytest_exit_code', 'n/a') }} · TestRail: {{ last_run.get('testrail', {}).get('message', 'No publication details available.') }}</p>
            {% if last_run.get('testrail', {}).get('plan_url') %}<a class="btn secondary" href="{{ last_run.get('testrail', {}).get('plan_url') }}" target="_blank">Open TestRail Plan #{{ last_run.get('testrail', {}).get('plan_id') }}</a>{% endif %}
            {% if last_run.get('status') == 'failed' and last_run.get('pytest_output') %}<details class="output-toggle"><summary>Show pytest failure output</summary><pre>{{ last_run.get('pytest_output') }}</pre></details>{% endif %}
        </div>
        {% endif %}
        {% for section in sections %}
        {% set section_key = section|lower %}
        {% set section_stats = dashboard.get('summary', {}).get('by_section', {}).get(section, {}) %}
            {% set section_running = is_running and (last_run.get('scope') == section or last_run.get('current_section') == section) %}
        <section class="tab-panel {% if loop.first %}active{% endif %} {% if section_key == 'invoice intelligence' %}invoice-panel{% endif %}" id="section-{{ section_key|replace(' ', '_') }}-tab">
            <div class="card">
                <div class="section-head"><div><h2>{{ section }}</h2><div class="muted">Section summary and execution controls</div></div><form method="post" action="{{ url_for('run_tests') }}"><input type="hidden" name="section" value="{{ section }}" /><select class="environment-select section-environment" name="environment" aria-label="Test environment" {% if is_running %}disabled{% endif %}>{% for key, profile in test_environments.items() %}<option value="{{ key }}" {% if key == last_run.get('environment', default_environment) %}selected{% endif %}>{{ profile.label }}</option>{% endfor %}</select><input class="seed-filter section-filter" name="provider_id" placeholder="Provider" value="{{ last_run.get('provider_id', '') }}" aria-label="Optional provider ID" {% if is_running %}disabled{% endif %} /><input class="seed-filter section-filter" name="service_type_id" placeholder="Service type" value="{{ last_run.get('service_type_id', '') }}" aria-label="Optional service type ID" {% if is_running %}disabled{% endif %} /><button class="btn secondary" type="submit" {% if is_running %}disabled{% endif %}>{% if section_running %}<span class="running-action"><span class="spinner"></span>Running…</span>{% else %}▶ Run {{ section }}{% endif %}</button></form></div>
                <div class="summary-grid"><div class="summary-item"><strong>Total</strong><span>{{ section_stats.get('total_cases', 0) }} cases · {{ section_stats.get('total_executions', 0) }} runs</span></div><div class="summary-item"><strong>Passed</strong><span>{{ result_icon('passed') }} {{ section_stats.get('cases_passed', 0) }}</span></div><div class="summary-item"><strong>Failed</strong><span>{{ result_icon('failed') }} {{ section_stats.get('cases_failed', 0) }}</span></div><div class="summary-item"><strong>Skipped</strong><span>{{ result_icon('skipped') }} {{ section_stats.get('cases_skipped', 0) }}</span></div></div>{% if last_run.get('testrail', {}).get('plan_url') %}<div class="plan-link">Latest test plan: <a href="{{ last_run.get('testrail', {}).get('plan_url') }}" target="_blank">TestRail Plan #{{ last_run.get('testrail', {}).get('plan_id') }}</a></div>{% endif %}
                {% if section_key not in ('workorders', 'self service requests') %}{% set guide = _section_description(section) %}<details class="guide"><summary>Validation guide</summary><div class="guide-content"><div class="guide-item"><strong>Mandatory</strong><ul>{% for point in _guide_points(guide.mandatory) %}<li>{{ point }}{% if not point.endswith('.') %}.{% endif %}</li>{% endfor %}</ul></div><div class="guide-item"><strong>Optional</strong><ul>{% for point in _guide_points(guide.optional) %}<li>{{ point }}{% if not point.endswith('.') %}.{% endif %}</li>{% endfor %}</ul></div><div class="guide-item"><strong>Compatibility rules</strong><ul>{% for point in _guide_points(guide.compatibility) %}<li>{{ point }}{% if not point.endswith('.') %}.{% endif %}</li>{% endfor %}</ul></div><div class="guide-item"><strong>Automation coverage</strong><ul>{% for point in _guide_points(guide.coverage) %}<li>{{ point }}{% if not point.endswith('.') %}.{% endif %}</li>{% endfor %}</ul></div></div></details>{% endif %}
                {% if section_key == 'workorders' %}<p class="muted">Run an individual workorder action below or run all workorders together.</p>{% endif %}
            </div>
            {% if section_key == 'workorders' %}
            {% if true %}
            <div class="action-grid">
            {% for action in workorder_groups %}
            {% set action_scope = 'action:' ~ action.file %}{% set action_running = is_running and (last_run.get('scope') == action_scope or last_run.get('current_section') == action_scope) %}
            <details class="card action-card" {% if action.failed %}open{% endif %}><summary><div class="action-heading"><h3>{{ action.operation.replace('POST /v1/workorders/', '') }}</h3><form method="post" action="{{ url_for('run_tests') }}" onclick="event.stopPropagation()"><input type="hidden" name="section" value="{{ action_scope }}" /><select class="environment-select" name="environment" aria-label="Test environment" {% if is_running %}disabled{% endif %}>{% for key, profile in test_environments.items() %}<option value="{{ key }}" {% if key == last_run.get('environment', 'qa-main') %}selected{% endif %}>{{ profile.label }}</option>{% endfor %}</select><button class="btn secondary" type="submit" {% if is_running %}disabled{% endif %}>{% if action_running %}<span class="running-action"><span class="spinner"></span>Running…</span>{% else %}▶ Run action{% endif %}</button></form></div><div class="action-meta">{{ action.file }} · {{ action.case_count }} mapped cases</div><div class="action-stats">{{ result_icon('passed') }} {{ action.passed }} &nbsp; {{ result_icon('failed') }} {{ action.failed }} &nbsp; {{ result_icon('skipped') }} {{ action.skipped }}</div></summary>
                <table><thead><tr><th>Status</th><th>Test case</th><th>TestRail</th><th>Output</th></tr></thead><tbody>{% for row in action.results if status_filter == 'all' or row.get('status', '') == status_filter %}<tr><td>{{ result_icon(row.get('status', 'skipped')) }}</td><td>{{ row.get('title', '') }}<br/><small>{{ row.get('nodeid', '') }}</small></td><td>{% if row.get('testrail_case_url') %}<a href="{{ row.get('testrail_case_url') }}" target="_blank">{{ row.get('testrail_case_id') }}</a>{% else %}{{ row.get('testrail_case_id', 'Not mapped') }}{% endif %}</td><td class="output-cell"><details class="output-toggle"><summary>Expand output</summary><pre>{{ row.get('formatted_details', row.get('details', '')) }}</pre></details></td></tr>{% endfor %}</tbody></table>
            </details>
            {% endfor %}
            {% if not workorder_groups %}<div class="card"><p class="muted">No workorder results yet.</p></div>{% endif %}
            </div>
            {% endif %}
            {% elif section_key == 'sync management (vendor map)' %}
            <div class="action-grid">
            {% for action in vendor_map_groups %}
            {% set action_scope = 'action:' ~ action.file %}{% set action_running = is_running and (last_run.get('scope') == action_scope or last_run.get('current_section') == action_scope) %}
            <details class="card action-card" {% if action.failed %}open{% endif %}><summary><div class="action-heading"><h3>{{ action.label }}</h3><form method="post" action="{{ url_for('run_tests') }}" onclick="event.stopPropagation()"><input type="hidden" name="section" value="{{ action_scope }}" /><select class="environment-select" name="environment" aria-label="Test environment" {% if is_running %}disabled{% endif %}>{% for key, profile in test_environments.items() %}<option value="{{ key }}" {% if key == last_run.get('environment', 'qa-main') %}selected{% endif %}>{{ profile.label }}</option>{% endfor %}</select><button class="btn secondary" type="submit" {% if is_running %}disabled{% endif %}>{% if action_running %}<span class="running-action"><span class="spinner"></span>Running…</span>{% else %}▶ Run action{% endif %}</button></form></div><div class="action-meta">{{ action.operation }} · {{ action.file }} · {{ action.case_count }} mapped cases</div><p class="action-description">{{ action.description }}</p><div class="action-stats">{{ result_icon('passed') }} {{ action.passed }} &nbsp; {{ result_icon('failed') }} {{ action.failed }} &nbsp; {{ result_icon('skipped') }} {{ action.skipped }}</div></summary>
                {% if action.results %}<table><thead><tr><th>Status</th><th>Test case</th><th>TestRail</th><th>Output</th></tr></thead><tbody>{% for row in action.results if status_filter == 'all' or row.get('status', '') == status_filter %}<tr><td>{{ result_icon(row.get('status', 'skipped')) }}</td><td>{{ row.get('title', '') }}<br/><small>{{ row.get('nodeid', '') }}</small></td><td>{% if row.get('testrail_case_url') %}<a href="{{ row.get('testrail_case_url') }}" target="_blank">{{ row.get('testrail_case_id') }}</a>{% else %}{{ row.get('testrail_case_id', 'Not mapped') }}{% endif %}</td><td class="output-cell"><details class="output-toggle"><summary>Expand output</summary><pre>{{ row.get('formatted_details', row.get('details', '')) }}</pre></details></td></tr>{% endfor %}</tbody></table>{% else %}<p class="muted">No results yet. Run this action to populate the report.</p>{% endif %}
            </details>
            {% endfor %}
            </div>
            {% elif section_key == 'self service requests' %}
            {% if false %}
            <p class="muted">Run an individual Self Service action below. Planned actions remain visible until their automation is added.</p>
            <div class="action-grid">
            {% for action in self_service_groups %}
            {% set action_scope = 'action:' ~ action.file %}{% set action_running = is_running and (last_run.get('scope') == action_scope or last_run.get('current_section') == action_scope) %}
            <details class="card action-card" id="self-service-action-{{ loop.index }}" {% if action.failed %}open{% endif %}><summary><div class="action-heading"><h3>{{ action.label }}</h3>{% if action.implemented %}<form method="post" action="{{ url_for('run_tests') }}" onclick="event.stopPropagation()"><input type="hidden" name="section" value="{{ action_scope }}" /><select class="environment-select" name="environment" aria-label="Test environment" {% if is_running %}disabled{% endif %}>{% for key, profile in test_environments.items() %}<option value="{{ key }}" {% if key == last_run.get('environment', 'qa-main') %}selected{% endif %}>{{ profile.label }}</option>{% endfor %}</select><button class="btn secondary" type="submit" {% if is_running %}disabled{% endif %}>{% if action_running %}<span class="running-action"><span class="spinner"></span>Running…</span>{% else %}▶ Run action{% endif %}</button></form>{% else %}<span class="status idle">Planned</span>{% endif %}</div><div class="action-meta">{{ action.operation }} · {% if action.implemented %}{{ action.case_count }} mapped cases{% else %}Automation pending{% endif %}</div><p class="action-description">{{ action.description }}</p><div class="action-stats">{{ result_icon('passed') }} {{ action.passed }} &nbsp; {{ result_icon('failed') }} {{ action.failed }} &nbsp; {{ result_icon('skipped') }} {{ action.skipped }}</div></summary>
                {% if action.results %}<table><thead><tr><th>Status</th><th>Test case</th><th>TestRail</th><th>Output</th></tr></thead><tbody>{% for row in action.results if status_filter == 'all' or row.get('status', '') == status_filter %}<tr><td>{{ result_icon(row.get('status', 'skipped')) }}</td><td>{{ row.get('title', '') }}<br/><small>{{ row.get('nodeid', '') }}</small></td><td>{% if row.get('testrail_case_url') %}<a href="{{ row.get('testrail_case_url') }}" target="_blank">{{ row.get('testrail_case_id') }}</a>{% else %}{{ row.get('testrail_case_id', 'Not mapped') }}{% endif %}</td><td class="output-cell"><details class="output-toggle"><summary>Expand output</summary><pre>{{ row.get('formatted_details', row.get('details', '')) }}</pre></details></td></tr>{% endfor %}</tbody></table>{% else %}<p class="muted">{% if action.implemented %}No results yet. Run this action to populate the report.{% else %}This Self Service action is planned; its automation will be added next.{% endif %}</p>{% endif %}
            </details>
            {% endfor %}
            </div>
            {% endif %}
            {% else %}
            {% set sec = (dashboard.get('sections', []) | selectattr('name', 'equalto', section) | list) %}
            {% if sec %}<div class="card"><table><thead><tr><th>Status</th><th>Test case</th><th>TestRail</th><th>Output</th></tr></thead><tbody>{% for row in sec[0].get('results', []) if status_filter == 'all' or row.get('status', '') == status_filter %}<tr><td>{{ result_icon(row.get('status', 'skipped')) }}</td><td>{{ row.get('title', '') }}<br/><small>{{ row.get('nodeid', '') }}</small></td><td>{% if row.get('testrail_case_url') %}<a href="{{ row.get('testrail_case_url') }}" target="_blank">{{ row.get('testrail_case_id') }}</a>{% else %}{{ row.get('testrail_case_id', 'Not mapped') }}{% endif %}</td><td><details class="output-toggle"><summary>Expand output</summary><pre>{{ row.get('formatted_details', row.get('details', '')) }}</pre></details></td></tr>{% endfor %}</tbody></table></div>{% else %}<div class="card"><p class="muted">No results yet for this section. Run it to populate the report.</p></div>{% endif %}
            {% endif %}
        </section>
        {% endfor %}
        {% for action in workorder_groups %}
        {% set action_scope = 'action:' ~ action.file %}{% set action_running = is_running and (last_run.get('scope') == action_scope or last_run.get('current_section') == action_scope) %}
        <section class="tab-panel" id="workorder-action-page-{{ loop.index }}">
            <div class="card">
                <div class="section-head"><div><h2>{{ action.operation.replace('POST /v1/workorders/', '') }}</h2><div class="muted">{{ action.operation }}</div></div><form method="post" action="{{ url_for('run_tests') }}"><input type="hidden" name="section" value="{{ action_scope }}" /><select class="environment-select section-environment" name="environment" aria-label="Test environment" {% if is_running %}disabled{% endif %}>{% for key, profile in test_environments.items() %}<option value="{{ key }}" {% if key == last_run.get('environment', default_environment) %}selected{% endif %}>{{ profile.label }}</option>{% endfor %}</select><input class="seed-filter section-filter" name="provider_id" placeholder="Provider" value="{{ last_run.get('provider_id', '') }}" aria-label="Optional provider ID" {% if is_running %}disabled{% endif %} /><input class="seed-filter section-filter" name="service_type_id" placeholder="Service type" value="{{ last_run.get('service_type_id', '') }}" aria-label="Optional service type ID" {% if is_running %}disabled{% endif %} /><button class="btn secondary" type="submit" {% if is_running %}disabled{% endif %}>{% if action_running %}<span class="running-action"><span class="spinner"></span>Running…</span>{% else %}▶ Run action{% endif %}</button></form></div>
                <div class="summary-grid"><div class="summary-item"><strong>Total</strong><span>{{ action.case_count }} mapped cases</span></div><div class="summary-item"><strong>Passed</strong><span>{{ result_icon('passed') }} {{ action.passed }}</span></div><div class="summary-item"><strong>Failed</strong><span>{{ result_icon('failed') }} {{ action.failed }}</span></div><div class="summary-item"><strong>Skipped</strong><span>{{ result_icon('skipped') }} {{ action.skipped }}</span></div></div>{% if last_run.get('testrail', {}).get('plan_url') %}<div class="plan-link">Latest test plan: <a href="{{ last_run.get('testrail', {}).get('plan_url') }}" target="_blank">TestRail Plan #{{ last_run.get('testrail', {}).get('plan_id') }}</a></div>{% endif %}
                {% set guide = _section_description('workorders') %}<details class="guide"><summary>Validation guide</summary><div class="guide-content"><div class="guide-item"><strong>Mandatory</strong><ul>{% for point in _guide_points(guide.mandatory) %}<li>{{ point }}{% if not point.endswith('.') %}.{% endif %}</li>{% endfor %}</ul></div><div class="guide-item"><strong>Optional</strong><ul>{% for point in _guide_points(guide.optional) %}<li>{{ point }}{% if not point.endswith('.') %}.{% endif %}</li>{% endfor %}</ul></div><div class="guide-item"><strong>Compatibility rules</strong><ul>{% for point in _guide_points(guide.compatibility) %}<li>{{ point }}{% if not point.endswith('.') %}.{% endif %}</li>{% endfor %}</ul></div><div class="guide-item"><strong>Automation coverage</strong><ul>{% for point in _guide_points(guide.coverage) %}<li>{{ point }}{% if not point.endswith('.') %}.{% endif %}</li>{% endfor %}</ul></div></div></details>
                <div class="compat-check" data-compat-scope="workorders" data-compat-file="{{ action.file }}"><button type="button" class="btn secondary compat-btn">Check DB Compatibility</button><div class="compat-result" hidden></div></div>
            </div>
            {% if action.results %}<div class="card"><table><thead><tr><th>Status</th><th>Test case</th><th>TestRail</th><th>Output</th></tr></thead><tbody>{% for row in action.results if status_filter == 'all' or row.get('status', '') == status_filter %}<tr><td>{{ result_icon(row.get('status', 'skipped')) }}</td><td>{{ row.get('title', '') }}<br/><small>{{ row.get('nodeid', '') }}</small></td><td>{% if row.get('testrail_case_url') %}<a href="{{ row.get('testrail_case_url') }}" target="_blank">{{ row.get('testrail_case_id') }}</a>{% else %}{{ row.get('testrail_case_id', 'Not mapped') }}{% endif %}</td><td class="output-cell"><details class="output-toggle"><summary>Expand output</summary><pre>{{ row.get('formatted_details', row.get('details', '')) }}</pre></details></td></tr>{% endfor %}</tbody></table></div>{% else %}<div class="card"><p class="muted">No results yet. Run this action to populate the report.</p></div>{% endif %}
        </section>
        {% endfor %}
        {% for action in self_service_groups %}
        {% set action_scope = 'action:' ~ action.file %}{% set action_running = is_running and (last_run.get('scope') == action_scope or last_run.get('current_section') == action_scope) %}
        <section class="tab-panel" id="self-service-action-page-{{ loop.index }}">
            <div class="card">
                <div class="section-head"><div><h2>{{ action.label }}</h2><div class="muted">{{ action.operation }}</div></div>{% if action.implemented %}<form method="post" action="{{ url_for('run_tests') }}"><input type="hidden" name="section" value="{{ action_scope }}" /><select class="environment-select section-environment" name="environment" aria-label="Test environment" {% if is_running %}disabled{% endif %}>{% for key, profile in test_environments.items() %}<option value="{{ key }}" {% if key == last_run.get('environment', default_environment) %}selected{% endif %}>{{ profile.label }}</option>{% endfor %}</select><input class="seed-filter section-filter" name="provider_id" placeholder="Provider" value="{{ last_run.get('provider_id', '') }}" aria-label="Optional provider ID" {% if is_running %}disabled{% endif %} /><input class="seed-filter section-filter" name="service_type_id" placeholder="Service type" value="{{ last_run.get('service_type_id', '') }}" aria-label="Optional service type ID" {% if is_running %}disabled{% endif %} /><button class="btn secondary" type="submit" {% if is_running %}disabled{% endif %}>{% if action_running %}<span class="running-action"><span class="spinner"></span>Running…</span>{% else %}▶ Run action{% endif %}</button></form>{% else %}<span class="status idle">Planned</span>{% endif %}</div>
                {% if action.implemented %}<div class="summary-grid"><div class="summary-item"><strong>Total</strong><span>{{ action.case_count }} mapped cases</span></div><div class="summary-item"><strong>Passed</strong><span>{{ result_icon('passed') }} {{ action.passed }}</span></div><div class="summary-item"><strong>Failed</strong><span>{{ result_icon('failed') }} {{ action.failed }}</span></div><div class="summary-item"><strong>Skipped</strong><span>{{ result_icon('skipped') }} {{ action.skipped }}</span></div></div>{% if last_run.get('testrail', {}).get('plan_url') %}<div class="plan-link">Latest test plan: <a href="{{ last_run.get('testrail', {}).get('plan_url') }}" target="_blank">TestRail Plan #{{ last_run.get('testrail', {}).get('plan_id') }}</a></div>{% endif %}{% endif %}
                <details class="guide"><summary>Action information</summary><div class="guide-content">{% set action_guide = _self_service_action_guide(action.file) %}{% if action_guide %}<div class="guide-item"><strong>Mandatory</strong><ul>{% for point in _guide_points(action_guide.mandatory) %}<li>{{ point }}{% if not point.endswith('.') %}.{% endif %}</li>{% endfor %}</ul></div><div class="guide-item"><strong>Optional</strong><ul>{% for point in _guide_points(action_guide.optional) %}<li>{{ point }}{% if not point.endswith('.') %}.{% endif %}</li>{% endfor %}</ul></div><div class="guide-item"><strong>Compatibility rules</strong><ul>{% for point in _guide_points(action_guide.compatibility) %}<li>{{ point }}{% if not point.endswith('.') %}.{% endif %}</li>{% endfor %}</ul></div><div class="guide-item"><strong>Automation coverage</strong><ul>{% for point in _guide_points(action_guide.coverage) %}<li>{{ point }}{% if not point.endswith('.') %}.{% endif %}</li>{% endfor %}</ul></div>{% else %}<div class="guide-item"><strong>Status</strong><ul><li>{% if action.implemented %}Automation implemented and ready to run.{% else %}Automation pending for this action.{% endif %}</li></ul></div><div class="guide-item"><strong>Endpoint</strong><ul><li>{{ action.operation }}.</li></ul></div>{% endif %}</div></details>
                <div class="compat-check" data-compat-scope="self_service_requests" data-compat-file="{{ action.file }}"><button type="button" class="btn secondary compat-btn">Check DB Compatibility</button><div class="compat-result" hidden></div></div>
            </div>
            {% if action.results %}<div class="card"><table><thead><tr><th>Status</th><th>Test case</th><th>TestRail</th><th>Output</th></tr></thead><tbody>{% for row in action.results if status_filter == 'all' or row.get('status', '') == status_filter %}<tr><td>{{ result_icon(row.get('status', 'skipped')) }}</td><td>{{ row.get('title', '') }}<br/><small>{{ row.get('nodeid', '') }}</small></td><td>{% if row.get('testrail_case_url') %}<a href="{{ row.get('testrail_case_url') }}" target="_blank">{{ row.get('testrail_case_id') }}</a>{% else %}{{ row.get('testrail_case_id', 'Not mapped') }}{% endif %}</td><td class="output-cell"><details class="output-toggle"><summary>Expand output</summary><pre>{{ row.get('formatted_details', row.get('details', '')) }}</pre></details></td></tr>{% endfor %}</tbody></table></div>{% else %}<div class="card"><p class="muted">{% if action.implemented %}No results yet. Run this action to populate the report.{% else %}This action is planned and does not have automation yet.{% endif %}</p></div>{% endif %}
        </section>
        {% endfor %}
        {% for action in vendor_map_groups %}
        {% set action_scope = 'action:' ~ action.file %}{% set action_running = is_running and (last_run.get('scope') == action_scope or last_run.get('current_section') == action_scope) %}
        <section class="tab-panel" id="vendor-map-action-page-{{ loop.index }}">
            <div class="card"><div class="section-head"><div><h2>{{ action.label }}</h2><div class="muted">{{ action.operation }}</div></div><form method="post" action="{{ url_for('run_tests') }}"><input type="hidden" name="section" value="{{ action_scope }}" /><select class="environment-select section-environment" name="environment" aria-label="Test environment" {% if is_running %}disabled{% endif %}>{% for key, profile in test_environments.items() %}<option value="{{ key }}" {% if key == last_run.get('environment', default_environment) %}selected{% endif %}>{{ profile.label }}</option>{% endfor %}</select><button class="btn secondary" type="submit" {% if is_running %}disabled{% endif %}>{% if action_running %}<span class="running-action"><span class="spinner"></span>Running…</span>{% else %}▶ Run action{% endif %}</button></form></div><div class="summary-grid"><div class="summary-item"><strong>Total</strong><span>{{ action.case_count }} mapped cases</span></div><div class="summary-item"><strong>Passed</strong><span>{{ result_icon('passed') }} {{ action.passed }}</span></div><div class="summary-item"><strong>Failed</strong><span>{{ result_icon('failed') }} {{ action.failed }}</span></div><div class="summary-item"><strong>Skipped</strong><span>{{ result_icon('skipped') }} {{ action.skipped }}</span></div></div><details class="guide"><summary>Action information</summary><div class="guide-content"><div class="guide-item"><strong>Endpoint</strong><ul><li>{{ action.operation }}.</li></ul></div><div class="guide-item"><strong>Coverage</strong><ul><li>{{ action.description }}</li></ul></div></div></details></div>
            {% if action.results %}<div class="card"><table><thead><tr><th>Status</th><th>Test case</th><th>TestRail</th><th>Output</th></tr></thead><tbody>{% for row in action.results if status_filter == 'all' or row.get('status', '') == status_filter %}<tr><td>{{ result_icon(row.get('status', 'skipped')) }}</td><td>{{ row.get('title', '') }}<br/><small>{{ row.get('nodeid', '') }}</small></td><td>{% if row.get('testrail_case_url') %}<a href="{{ row.get('testrail_case_url') }}" target="_blank">{{ row.get('testrail_case_id') }}</a>{% else %}{{ row.get('testrail_case_id', 'Not mapped') }}{% endif %}</td><td class="output-cell"><details class="output-toggle"><summary>Expand output</summary><pre>{{ row.get('formatted_details', row.get('details', '')) }}</pre></details></td></tr>{% endfor %}</tbody></table></div>{% else %}<div class="card"><p class="muted">No results yet. Run this action to populate the report.</p></div>{% endif %}
        </section>
        {% endfor %}
        {% for action in invoice_intelligence_groups %}
        {% set action_scope = 'action:' ~ action.file %}
        {% set action_running = is_running and (last_run.get('scope') == action_scope or last_run.get('current_section') == action_scope) %}
        <section class="tab-panel invoice-panel" id="invoice-intelligence-action-page-{{ loop.index }}">
            <div class="card">
            <div class="section-head">
                <div><h2>{{ action.label }}</h2><div class="muted">{{ action.operation }}</div></div>
                <form method="post" action="{{ url_for('run_tests') }}">
                    <input type="hidden" name="section" value="{{ action_scope }}" />
                    <select class="environment-select section-environment" name="environment" aria-label="Test environment" {% if is_running %}disabled{% endif %}>
                        {% for key, profile in test_environments.items() %}<option value="{{ key }}" {% if key == last_run.get('environment', default_environment) %}selected{% endif %}>{{ profile.label }}</option>{% endfor %}
                    </select>
                    <button class="btn secondary" type="submit" {% if is_running %}disabled{% endif %}>{% if action_running %}<span class="running-action"><span class="spinner"></span>Running...</span>{% else %}Run {{ action.label.split(' - ')[0] }}{% endif %}</button>
                </form>
            </div>
            <div class="summary-grid">
                <div class="summary-item"><strong>Total</strong><span>{{ action.case_count }} cases &middot; {{ action.results|length }} runs</span></div>
                <div class="summary-item"><strong>Passed</strong><span>{{ result_icon('passed') }} {{ action.passed }}</span></div>
                <div class="summary-item"><strong>Failed</strong><span>{{ result_icon('failed') }} {{ action.failed }}</span></div>
                <div class="summary-item"><strong>Skipped</strong><span>{{ result_icon('skipped') }} {{ action.skipped }}</span></div>
            </div>
            <div class="compat-check" data-compat-scope="invoice_intelligence" data-compat-file="{{ action.file }}">
                <button type="button" class="btn secondary compat-btn">Check DB Compatibility</button>
                <div class="compat-result" hidden></div>
            </div>
            </div>
            <div class="card">
            {% if action.results %}
            <table><thead><tr><th>Status</th><th>Test case</th><th>TestRail</th><th>Output</th></tr></thead>
                <tbody>{% for row in action.results if status_filter == 'all' or row.get('status', '') == status_filter %}
                    <tr><td>{{ result_icon(row.get('status', 'skipped')) }}</td><td>{{ row.get('title', '') }}</td>
                        <td>{% if row.get('testrail_case_url') %}<a href="{{ row.get('testrail_case_url') }}" target="_blank">{{ row.get('testrail_case_id') }}</a>{% else %}{{ row.get('testrail_case_id', 'Not mapped') }}{% endif %}</td>
                        <td class="output-cell"><details class="output-toggle"><summary>Expand output</summary><pre>{{ row.get('formatted_details', row.get('details', '')) }}</pre></details></td></tr>
                {% endfor %}</tbody>
            </table>
            {% else %}<p class="muted">No results yet. Run this action to populate the report.</p>{% endif %}
            </div>
        </section>
        {% endfor %}
        <section class="tab-panel" id="cancel-workorders-tab">
            <div class="card">
                <div class="section-head"><div><h2>Cancel Work Orders</h2><div class="muted">Replays and cancels test-created workorders (mirrors the manual DB cleanup script).</div></div></div>
                <div class="guide-item"><strong>Environment</strong></div>
                <select class="environment-select" id="cancel-wo-environment" aria-label="Test environment">{% for key, profile in test_environments.items() %}<option value="{{ key }}" {% if key == default_environment %}selected{% endif %}>{{ profile.label }}</option>{% endfor %}</select>
                <div class="guide-item" style="margin-top:12px;"><strong>Workorder numbers</strong><div class="muted">Comma or newline separated. Only workorders you list are touched; already-cancelled/completed ones are skipped automatically.</div></div>
                <textarea id="cancel-wo-numbers" rows="6" style="width:100%;max-width:640px;margin-top:6px;padding:9px;border:1px solid var(--line);border-radius:7px;font:inherit;" placeholder="7935, 7934, 7933"></textarea>
                <div style="margin-top:10px;"><button type="button" class="btn secondary" id="cancel-wo-apply">Apply</button></div>
                <div id="cancel-wo-result" class="compat-result" hidden></div>
            </div>
        </section>
        <section class="tab-panel" id="cancel-self-service-tab">
            <div class="card">
                <div class="section-head"><div><h2>Cancel Self Service Requests</h2><div class="muted">Marks pending Self Service requests as Cancelled (RequestStatusId=2) so their service becomes reusable again. Manual, opt-in only - never runs automatically during a test run.</div></div></div>
                <div class="guide-item"><strong>Environment</strong></div>
                <select class="environment-select" id="cancel-ssr-environment" aria-label="Test environment">{% for key, profile in test_environments.items() %}<option value="{{ key }}" {% if key == default_environment %}selected{% endif %}>{{ profile.label }}</option>{% endfor %}</select>
                <div class="guide-item" style="margin-top:12px;"><strong>Self Service request numbers</strong><div class="muted">Comma or newline separated (e.g. SR2026-003067). Only requests you list are touched, and only if still Pending - anything already Approved/Rejected/Cancelled is skipped automatically.</div></div>
                <textarea id="cancel-ssr-numbers" rows="6" style="width:100%;max-width:640px;margin-top:6px;padding:9px;border:1px solid var(--line);border-radius:7px;font:inherit;" placeholder="SR2026-003067, SR2026-003066"></textarea>
                <div style="margin-top:10px;"><button type="button" class="btn secondary" id="cancel-ssr-apply">Apply</button></div>
                <div id="cancel-ssr-result" class="compat-result" hidden></div>
            </div>
        </section>
        </div>
    </main>
</div>
{% if is_running %}
<div class="qa-runner-track" aria-hidden="true">
    <div class="qa-runner" id="qa-runner">
        <div class="qa-runner-group">
            <div class="qa-robot-wrap"><span class="qa-rocket-flame"></span><img class="qa-robot-image" src="{{ url_for('static', filename='qa-rover.svg') }}" alt="" width="94" height="116" /></div>
            <div class="qa-run-label"><strong>The QA robot is on the case</strong><span id="qa-run-section">Checking {{ last_run.get('current_section') or last_run.get('scope', 'selected tests') }}</span></div>
        </div>
    </div>
</div>
{% endif %}
<dialog class="case-dialog" id="case-dialog" aria-labelledby="case-dialog-title">
    <div class="case-dialog-inner">
        <div class="case-dialog-head"><h2 id="case-dialog-title">Test cases</h2><button class="btn secondary icon" id="case-dialog-close" type="button" aria-label="Close case list">×</button></div>
        <input class="case-search" id="case-search" type="search" placeholder="Filter by case, title, or section" aria-label="Filter test cases" />
        <div class="muted" id="case-dialog-status" role="status">Loading cases…</div>
        <div class="case-list" id="case-list"></div>
        <div class="case-dialog-foot"><span class="muted" id="case-selection-count">No cases selected</span><button class="btn secondary" id="case-dialog-done" type="button">Done</button></div>
    </div>
</dialog>
<div class="run-toast" id="run-toast" role="status" aria-live="polite"></div>
<script>
const sidebar = document.getElementById('sidebar');
const mobileMenuToggle = document.getElementById('mobile-menu-toggle');
const navigationCounts = {{ navigation_counts|tojson }};
const dashboardSections = {{ sections|tojson }};
function setNavigationCount(button, section) {
    const count = navigationCounts[section] || { passed: 0, total: 0 };
    if (!button) return;
    let badge = button.querySelector('.nav-count');
    if (!badge) {
        badge = document.createElement('span');
        badge.className = 'nav-count';
        button.appendChild(badge);
    }
    badge.textContent = (count.passed || 0) + '/' + (count.total || 0);
}
setNavigationCount(document.querySelector('#inventory-management-nav-group > .nav-btn'), 'Inventory Management');
setNavigationCount(document.querySelector('#self-service-nav-group > .nav-btn'), 'Self Service Requests');
setNavigationCount(document.querySelector('#vendor-map-nav-group > .nav-btn'), 'Sync Management (Vendor Map)');
setNavigationCount(document.querySelector('#workorders-nav-group > .nav-btn'), 'Workorders');
setNavigationCount(document.querySelector('#payment-readiness-nav-group > .nav-btn'), 'Payment Readiness');
dashboardSections.forEach(function (section) {
    const target = 'section-' + section.toLowerCase().replaceAll(' ', '_') + '-tab';
    document.querySelectorAll('[data-tab-target]').forEach(function (button) {
        if (button.dataset.tabTarget === target) setNavigationCount(button, section);
    });
});
if (localStorage.getItem('dashboardSidebarCollapsed') === 'true' && window.innerWidth > 800) sidebar.classList.add('collapsed');
function toggleSidebar() { if (window.innerWidth <= 800) sidebar.classList.toggle('open'); else { sidebar.classList.toggle('collapsed'); localStorage.setItem('dashboardSidebarCollapsed', sidebar.classList.contains('collapsed')); } if (mobileMenuToggle) mobileMenuToggle.setAttribute('aria-expanded', String(window.innerWidth <= 800 ? sidebar.classList.contains('open') : !sidebar.classList.contains('collapsed'))); }
if (mobileMenuToggle) mobileMenuToggle.addEventListener('click', toggleSidebar);
const navSearch = document.getElementById('nav-search');
const searchableNavButtons = [...sidebar.querySelectorAll('[data-tab-target]')].filter(function (button) {
    return document.getElementById(button.dataset.tabTarget);
});
function updateNavigationSearch() {
    const query = (navSearch.value || '').trim().toLowerCase();
    const matches = [];
    searchableNavButtons.forEach(function (button) {
        const panel = document.getElementById(button.dataset.tabTarget);
        const searchable = (button.textContent + ' ' + (panel ? panel.textContent : '')).toLowerCase();
        const matched = !query || searchable.includes(query);
        button.style.display = matched ? '' : 'none';
        if (matched) matches.push(button);
    });
    sidebar.querySelectorAll('.nav-group').forEach(function (group) {
        const parent = group.querySelector(':scope > .nav-btn');
        const children = [...group.querySelectorAll('.nav-sub-btn')];
        const visibleChild = children.some(function (button) { return button.style.display !== 'none'; });
        group.style.display = !query || visibleChild || (parent && parent.style.display !== 'none') ? '' : 'none';
        if (parent && visibleChild && parent.style.display === 'none') parent.style.display = '';
    });
    return matches;
}
if (navSearch) {
    navSearch.addEventListener('input', updateNavigationSearch);
    navSearch.addEventListener('keydown', function (event) {
        if (event.key !== 'Enter') return;
        event.preventDefault();
        const matches = updateNavigationSearch();
        const target = matches.find(function (button) { return button.classList.contains('nav-sub-btn'); }) || matches[0];
        if (target) target.click();
    });
}
const caseDialog = document.getElementById('case-dialog');
const caseList = document.getElementById('case-list');
const caseDialogStatus = document.getElementById('case-dialog-status');
const caseSearch = document.getElementById('case-search');
const caseSelectionCount = document.getElementById('case-selection-count');
const caseDataCache = new Map();
const selectedNodesByScope = new Map();
let activeCaseScope = '';
function selectedNodes(scope) {
    if (!selectedNodesByScope.has(scope)) selectedNodesByScope.set(scope, new Set());
    return selectedNodesByScope.get(scope);
}
function syncCaseSelection() {
    const selected = selectedNodes(activeCaseScope);
    caseList.querySelectorAll('.case-row').forEach(function (row) {
        const nodeIds = JSON.parse(row.dataset.nodeIds || '[]');
        const checkbox = row.querySelector('input[type="checkbox"]');
        checkbox.checked = nodeIds.length > 0 && nodeIds.every(function (nodeId) { return selected.has(nodeId); });
    });
    caseSelectionCount.textContent = selected.size ? selected.size + ' test execution' + (selected.size === 1 ? '' : 's') + ' selected' : 'No cases selected; Run executes the full section or action';
}
function applyCaseFilter() {
    const query = (caseSearch.value || '').trim().toLowerCase();
    caseList.querySelectorAll('.case-row').forEach(function (row) {
        row.hidden = !row.dataset.searchText.includes(query);
    });
}
function renderCases(cases) {
    caseList.replaceChildren();
    cases.forEach(function (testCase) {
        const row = document.createElement('label');
        row.className = 'case-row';
        row.dataset.nodeIds = JSON.stringify(testCase.node_ids || []);
        row.dataset.searchText = (testCase.case_id + ' ' + testCase.title + ' ' + testCase.section).toLowerCase();
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.disabled = !testCase.selectable;
        checkbox.setAttribute('aria-label', 'Select case C' + testCase.case_id);
        const detail = document.createElement('span');
        const title = document.createElement('span');
        title.className = 'case-row-title';
        title.textContent = 'C' + testCase.case_id + ' · ' + testCase.title;
        const metadata = document.createElement('span');
        metadata.className = 'case-row-meta';
        metadata.textContent = testCase.section + (testCase.selectable ? '' : ' · No pytest mapping in this section/action');
        detail.append(title, document.createElement('br'), metadata);
        row.append(checkbox, detail);
        checkbox.addEventListener('change', function () {
            const selected = selectedNodes(activeCaseScope);
            (testCase.node_ids || []).forEach(function (nodeId) {
                if (checkbox.checked) selected.add(nodeId);
                else selected.delete(nodeId);
            });
            syncCaseSelection();
        });
        caseList.appendChild(row);
    });
    applyCaseFilter();
    syncCaseSelection();
}
function openCaseDialog(scope, button) {
    activeCaseScope = scope;
    caseDialog.showModal();
    document.getElementById('case-dialog-title').textContent = 'Cases · ' + scope.replace(/^action:/, '');
    caseSearch.value = '';
    const cached = caseDataCache.get(scope);
    if (cached && cached.expiresAt > Date.now()) {
        caseDialogStatus.textContent = cached.cases.length + ' TestRail cases. Unmapped cases are unavailable for selection.';
        renderCases(cached.cases);
        return;
    }
    caseDialogStatus.textContent = 'Loading TestRail cases…';
    caseList.replaceChildren();
    button.disabled = true;
    fetch({{ url_for('api_dashboard_cases')|tojson }} + '?scope=' + encodeURIComponent(scope), { cache: 'no-store' })
        .then(function (response) { return response.json().then(function (data) { if (!response.ok) throw new Error(data.error || 'Could not load TestRail cases.'); return data; }); })
        .then(function (data) {
            caseDataCache.set(scope, { cases: data.cases || [], expiresAt: Date.now() + 300000 });
            caseDialogStatus.textContent = (data.cases || []).length + ' TestRail cases. Unmapped cases are unavailable for selection.';
            renderCases(data.cases || []);
        })
        .catch(function (error) { caseDialogStatus.textContent = error.message; })
        .finally(function () { button.disabled = false; });
}
document.getElementById('case-dialog-close').addEventListener('click', function () { caseDialog.close(); });
document.getElementById('case-dialog-done').addEventListener('click', function () { caseDialog.close(); });
caseSearch.addEventListener('input', applyCaseFilter);
document.querySelectorAll('form').forEach(function (form) {
    const scopeInput = form.querySelector('input[name="section"]');
    if (!scopeInput || !scopeInput.value || scopeInput.value === 'all') return;
    const pickerButton = document.createElement('button');
    pickerButton.type = 'button';
    pickerButton.className = 'btn secondary case-picker-trigger';
    pickerButton.textContent = 'Show Cases';
    pickerButton.addEventListener('click', function (event) {
        event.preventDefault();
        event.stopPropagation();
        openCaseDialog(scopeInput.value, pickerButton);
    });
    form.appendChild(pickerButton);
    form.addEventListener('submit', function () {
        form.querySelectorAll('input[data-selected-node]').forEach(function (input) { input.remove(); });
        selectedNodes(scopeInput.value).forEach(function (nodeId) {
            const input = document.createElement('input');
            input.type = 'hidden';
            input.name = 'node_id';
            input.value = nodeId;
            input.dataset.selectedNode = 'true';
            form.appendChild(input);
        });
    });
});
const savedTab = localStorage.getItem('dashboardActiveTab');
const initialButton = savedTab && document.querySelector('[data-tab-target="' + savedTab + '"]');
document.querySelectorAll('[data-tab-target]').forEach(function (button) { button.addEventListener('click', function () { document.querySelectorAll('.tab-panel').forEach(function (panel) { panel.classList.remove('active'); }); document.querySelectorAll('[data-tab-target]').forEach(function (item) { item.classList.remove('active'); }); document.getElementById(button.dataset.tabTarget).classList.add('active'); button.classList.add('active'); localStorage.setItem('dashboardActiveTab', button.dataset.tabTarget); const actionTarget = button.dataset.actionTarget; if (actionTarget) { const target = document.getElementById(actionTarget); if (target) { target.open = true; target.scrollIntoView({ behavior: 'smooth', block: 'start' }); } } sidebar.classList.remove('open'); }); });
const selfServiceGroup = document.getElementById('self-service-nav-group');
['payment-readiness-nav-group', 'invoice-intelligence-nav-group'].forEach(function (id) {
    const group = document.getElementById(id);
    const parent = group && group.querySelector(':scope > .nav-btn');
    if (parent) parent.addEventListener('click', function () { group.classList.toggle('expanded'); });
});
const selfServiceParent = selfServiceGroup && selfServiceGroup.querySelector('[data-tab-target="section-self_service_requests-tab"]');
if (selfServiceParent) selfServiceParent.addEventListener('click', function (event) { if (event.target === selfServiceParent || event.target.closest('.nav-icon, .nav-caret')) selfServiceGroup.classList.toggle('expanded'); });
const workordersGroup = document.getElementById('workorders-nav-group');
const workordersParent = workordersGroup && workordersGroup.querySelector('[data-tab-target="section-workorders-tab"]');
if (workordersParent) workordersParent.addEventListener('click', function (event) { if (event.target === workordersParent || event.target.closest('.nav-icon, .nav-caret')) workordersGroup.classList.toggle('expanded'); });
if (initialButton) initialButton.click();
const runToast = document.getElementById('run-toast');
let previousRunStatus = {{ last_run.get('status', 'idle')|tojson }};
let previousGeneratedAt = {{ last_run.get('generated_at', '')|tojson }};
function showRunToast(message) {
    if (!runToast) return;
    runToast.textContent = message;
    runToast.classList.add('visible');
    window.setTimeout(function () { runToast.classList.remove('visible'); }, 6500);
}
function updateRunCounters(data) {
    const counts = Object.assign({}, data.section_counts || {}, data.action_counts || {});
    document.querySelectorAll('[data-action-scope]').forEach(function (button) {
        const count = counts[button.dataset.actionScope];
        const badge = button.querySelector('.nav-count');
        if (!count || !badge) return;
        badge.textContent = (count.passed || 0) + '/' + (count.total || 0);
    });
    document.querySelectorAll('[data-section-scope]').forEach(function (button) {
        const count = (data.section_counts || {})[button.dataset.sectionScope];
        const badge = button.querySelector('.nav-count');
        if (!count || !badge) return;
        badge.textContent = (count.passed || 0) + '/' + (count.total || 0);
    });
}
function pollRunStatus() {
    fetch({{ url_for('run_status')|tojson }}, { cache: 'no-store' }).then(function (response) { return response.json(); }).then(function (data) {
        updateRunCounters(data);
        const runSectionLabel = document.getElementById('qa-run-section');
        if (runSectionLabel && data.current_section) runSectionLabel.textContent = 'Checking ' + data.current_section;
        if (data.status !== 'running' && previousRunStatus === 'running' && data.generated_at !== previousGeneratedAt) {
            const outcome = data.status === 'completed' ? 'Run completed' : 'Run finished with errors';
            showRunToast(outcome + '. Refreshing results…');
            const qaRunner = document.getElementById('qa-runner');
            if (qaRunner) qaRunner.classList.add('is-launching');
            window.setTimeout(function () { window.location.reload(); }, qaRunner ? 1250 : 900);
        }
        previousRunStatus = data.status;
        previousGeneratedAt = data.generated_at || previousGeneratedAt;
    }).catch(function () {}).finally(function () {
        window.setTimeout(pollRunStatus, 2000);
    });
}
pollRunStatus();
document.querySelectorAll('.summary-grid').forEach(function (summaryGrid) {
    const panel = summaryGrid.closest('.tab-panel');
    if (!panel) return;
    const cards = [...summaryGrid.querySelectorAll('.summary-item')];
    const rows = [...panel.querySelectorAll('tbody tr')];
    if (!cards.length || !rows.length) return;

    function applyFilter(filter) {
        rows.forEach(function (row) {
            const icon = row.querySelector('.result-icon');
            const status = icon && ['passed', 'failed', 'skipped'].find(function (name) { return icon.classList.contains(name); });
            row.hidden = filter !== 'all' && status !== filter;
        });
        cards.forEach(function (card) {
            card.classList.toggle('active', card.dataset.statusFilter === filter);
            card.setAttribute('aria-pressed', String(card.dataset.statusFilter === filter));
        });
    }

    cards.forEach(function (card) {
        const label = card.querySelector('strong')?.textContent.trim().toLowerCase();
        const filter = label === 'total' ? 'all' : label;
        if (!['all', 'passed', 'failed', 'skipped'].includes(filter)) return;
        card.dataset.statusFilter = filter;
        card.classList.add('summary-filter');
        card.setAttribute('role', 'button');
        card.setAttribute('tabindex', '0');
        card.setAttribute('aria-pressed', filter === 'all' ? 'true' : 'false');
        card.addEventListener('click', function () { applyFilter(filter); });
        card.addEventListener('keydown', function (event) {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                applyFilter(filter);
            }
        });
    });
    applyFilter('all');
});
document.querySelectorAll('.compat-btn').forEach(function (btn) {
    btn.addEventListener('click', function () {
        const wrap = btn.closest('.compat-check');
        const resultBox = wrap.querySelector('.compat-result');
        const panel = btn.closest('section.tab-panel');
        const envSelect = panel.querySelector('select[name="environment"]');
        const providerInput = panel.querySelector('input[name="provider_id"]');
        const serviceInput = panel.querySelector('input[name="service_type_id"]');
        const originalLabel = btn.textContent;
        btn.disabled = true;
        btn.textContent = 'Checking…';
        resultBox.hidden = true;
        fetch({{ url_for('api_check_compatibility')|tojson }}, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                scope: wrap.dataset.compatScope,
                file: wrap.dataset.compatFile,
                environment: envSelect ? envSelect.value : '',
                provider_id: providerInput ? providerInput.value : '',
                service_type_id: serviceInput ? serviceInput.value : ''
            })
        }).then(function (response) { return response.json(); }).then(function (data) {
            resultBox.hidden = false;
            if (data.error) { resultBox.innerHTML = '<p class="muted">' + data.error + '</p>'; return; }
            if (!data.compatible || !data.compatible.length) { resultBox.innerHTML = '<p class="muted">' + (data.message || "This API doesn't have any compatibility configured.") + '</p>'; return; }
            if (wrap.dataset.compatScope === 'invoice_intelligence') {
                const columns = [['providerId', 'Provider'], ['accountNo', 'Account'], ['billingAccount', 'Billing account'], ['currencyId', 'Currency'], ['invoiceDate', 'Invoice date'], ['hasAIAnalysis', 'AI Analysis available'], ['hasAIBrief', 'AI Brief available']];
                const table = document.createElement('table');
                const header = table.createTHead().insertRow();
                columns.forEach(function (column) { const cell = document.createElement('th'); cell.textContent = column[1]; header.appendChild(cell); });
                const body = table.createTBody();
                data.compatible.forEach(function (candidate) {
                    const row = body.insertRow();
                    columns.forEach(function (column) { const value = candidate[column[0]]; row.insertCell().textContent = typeof value === 'boolean' ? (value ? 'Yes' : 'No') : String(value ?? ''); });
                });
                resultBox.replaceChildren(table);
                resultBox.style.overflowX = 'auto';
                return;
            }
            const rows = data.compatible.map(function (candidate) {
                return '<tr><td>' + candidate.providerId + (candidate.providerName ? ' - ' + candidate.providerName : '') + '</td><td>' + candidate.serviceTypeId + (candidate.serviceTypeName ? ' - ' + candidate.serviceTypeName : '') + '</td></tr>';
            }).join('');
            resultBox.innerHTML = '<table><thead><tr><th>Provider</th><th>Service Type</th></tr></thead><tbody>' + rows + '</tbody></table>';
        }).catch(function (err) {
            resultBox.hidden = false;
            resultBox.innerHTML = '<p class="muted">Check failed: ' + err + '</p>';
        }).finally(function () {
            btn.disabled = false;
            btn.textContent = originalLabel;
        });
    });
});
const cancelWoApply = document.getElementById('cancel-wo-apply');
if (cancelWoApply) {
    cancelWoApply.addEventListener('click', function () {
        const envSelect = document.getElementById('cancel-wo-environment');
        const numbersInput = document.getElementById('cancel-wo-numbers');
        const resultBox = document.getElementById('cancel-wo-result');
        const originalLabel = cancelWoApply.textContent;
        if (!numbersInput.value.trim()) {
            resultBox.hidden = false;
            resultBox.innerHTML = '<p class="muted">Enter at least one workorder number.</p>';
            return;
        }
        cancelWoApply.disabled = true;
        cancelWoApply.textContent = 'Applying…';
        resultBox.hidden = true;
        fetch({{ url_for('api_cancel_workorders')|tojson }}, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ environment: envSelect.value, workorder_numbers: numbersInput.value })
        }).then(function (response) { return response.json(); }).then(function (data) {
            resultBox.hidden = false;
            if (data.error) { resultBox.innerHTML = '<p class="muted">' + data.error + '</p>'; return; }
            const rows = (data.results || []).map(function (item) {
                return '<tr><td>' + item.number + '</td><td>' + item.status + '</td><td>' + (item.message || '') + '</td></tr>';
            }).join('');
            resultBox.innerHTML = '<table><thead><tr><th>Workorder</th><th>Status</th><th>Message</th></tr></thead><tbody>' + rows + '</tbody></table>';
        }).catch(function (err) {
            resultBox.hidden = false;
            resultBox.innerHTML = '<p class="muted">Request failed: ' + err + '</p>';
        }).finally(function () {
            cancelWoApply.disabled = false;
            cancelWoApply.textContent = originalLabel;
        });
    });
}
const cancelSsrApply = document.getElementById('cancel-ssr-apply');
if (cancelSsrApply) {
    cancelSsrApply.addEventListener('click', function () {
        const envSelect = document.getElementById('cancel-ssr-environment');
        const numbersInput = document.getElementById('cancel-ssr-numbers');
        const resultBox = document.getElementById('cancel-ssr-result');
        const originalLabel = cancelSsrApply.textContent;
        if (!numbersInput.value.trim()) {
            resultBox.hidden = false;
            resultBox.innerHTML = '<p class="muted">Enter at least one self service request number.</p>';
            return;
        }
        cancelSsrApply.disabled = true;
        cancelSsrApply.textContent = 'Applying…';
        resultBox.hidden = true;
        fetch({{ url_for('api_cancel_self_service_requests')|tojson }}, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ environment: envSelect.value, request_numbers: numbersInput.value })
        }).then(function (response) { return response.json(); }).then(function (data) {
            resultBox.hidden = false;
            if (data.error) { resultBox.innerHTML = '<p class="muted">' + data.error + '</p>'; return; }
            const rows = (data.results || []).map(function (item) {
                return '<tr><td>' + item.number + '</td><td>' + item.status + '</td><td>' + (item.message || '') + '</td></tr>';
            }).join('');
            resultBox.innerHTML = '<table><thead><tr><th>Request</th><th>Status</th><th>Message</th></tr></thead><tbody>' + rows + '</tbody></table>';
        }).catch(function (err) {
            resultBox.hidden = false;
            resultBox.innerHTML = '<p class="muted">Request failed: ' + err + '</p>';
        }).finally(function () {
            cancelSsrApply.disabled = false;
            cancelSsrApply.textContent = originalLabel;
        });
    });
}
</script>
</body>
</html>
"""

    return render_template_string(
        template,
        dashboard=dashboard_data,
        _section_description=_section_description,
        _self_service_action_guide=_self_service_action_guide,
        _guide_points=_guide_points,
        workorder_groups=_workorder_groups(dashboard_data),
        vendor_map_groups=_vendor_map_groups(dashboard_data),
        self_service_groups=_self_service_groups(dashboard_data),
        invoice_intelligence_groups=_invoice_intelligence_groups(dashboard_data),
        sections=sections,
        last_run=last_run,
        is_running=is_running,
        health=health,
        status_filter=status_filter,
        navigation_counts=_navigation_counts(dashboard_data),
        test_environments=TEST_ENVIRONMENTS,
        default_environment=_default_environment_key(),
    )


@app.get("/api/run-status")
def run_status():
    last_run = _load_last_run()
    return jsonify({
        "status": last_run.get("status", "idle"),
        "scope": last_run.get("scope", ""),
        "current_section": last_run.get("current_section", ""),
        "sections": last_run.get("sections", {}),
        "section_counts": last_run.get("section_counts", {}),
        "action_counts": last_run.get("action_counts", {}),
        "generated_at": last_run.get("generated_at", ""),
    })


@app.get("/api/dashboard-cases")
def api_dashboard_cases():
    scope_name = request.args.get("scope", "").strip()
    try:
        pytest_cases = _pytest_case_mapping(scope_name)
        _, testrail_cases = _testrail_case_data()
    except (TestRailError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        return jsonify({"error": str(exc)}), 503

    cases = []
    section_names = {scope_name.casefold()}
    for folder, display in SECTION_DISPLAY_NAMES.items():
        if display.casefold() == scope_name.casefold():
            section_names.add(folder.replace("_", " ").casefold())
    for case in testrail_cases:
        case_id = str(case.get("id", "")).strip()
        nodes = pytest_cases.get(case_id, [])
        section_path = str(case.get("dashboard_section", "Unsectioned"))
        if scope_name.startswith("action:"):
            if not nodes:
                continue
        elif not nodes and not section_names.intersection(
            part.strip().casefold() for part in section_path.split(" / ")
        ):
            continue
        cases.append({
            "case_id": case_id,
            "title": str(case.get("title", "Untitled case")),
            "section": section_path,
            "node_ids": [item["nodeid"] for item in nodes],
            "selectable": bool(nodes),
        })
    return jsonify({"scope": scope_name, "cases": cases})


def _scheduler_page(message: str = "", error: str = "", status_code: int = 200):
    schedules = _get_schedule_store().list_schedules()
    runs = _get_schedule_store().list_runs()
    weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    template = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Schedules | API Automation</title>
    <style>
        :root { color-scheme: light; --upland-blue: #2574db; --upland-dark: #1654a5; --blue-soft: #eaf3ff; --ink: #172b4d; --muted: #65758b; --line: #dbe5f1; --paper: #f5f8fc; --surface: #fff; --red: #a23b36; }
        * { box-sizing: border-box; }
        body { margin: 0; background: var(--paper); color: var(--ink); font: 14px/1.5 'Open Sans', sans-serif; }
        .shell { min-height: 100vh; margin: 0; }
        .app-header { position: sticky; top: 0; z-index: 5; display: flex; align-items: center; justify-content: space-between; gap: 16px; min-height: 76px; padding: 14px 28px; background: var(--upland-blue); color: #fff; }
        .brand-title { display: flex; align-items: center; gap: 12px; min-width: 0; }
        .header-mark { display: grid; place-items: center; flex: 0 0 40px; width: 40px; height: 40px; border-radius: 50%; background: #fff; color: var(--upland-blue); font-size: 25px; font-weight: 800; line-height: 1; }
        .crumbs { color: rgba(255,255,255,.8); font-size: 12px; }
        .crumbs a { color: #fff; }
        h1 { margin: 1px 0 0; color: #fff; font-size: 20px; font-weight: 700; }
        .header-actions { display: flex; align-items: center; gap: 8px; }
        .content { width: 100%; max-width: 1440px; margin: 0 auto; padding: 30px 52px 56px; }
        h2 { margin: 0 0 16px; font-size: 17px; }
        .muted { color: var(--muted); }
        .layout { display: grid; grid-template-columns: minmax(280px, 360px) minmax(0, 1fr); gap: 24px; align-items: start; }
        .panel { background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 20px; box-shadow: 0 3px 12px rgba(31,78,121,.04); }
        label { display: block; margin: 0 0 6px; font-weight: 600; }
        input, select { width: 100%; min-height: 38px; border: 1px solid var(--line); border-radius: 6px; background: white; padding: 7px 9px; color: var(--ink); font: inherit; }
        input[type=checkbox] { width: 16px; min-height: 16px; accent-color: var(--upland-blue); }
        .field { margin-bottom: 15px; }
        .row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
        .checks { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 12px; max-height: 220px; overflow: auto; padding: 10px; border: 1px solid var(--line); border-radius: 6px; }
        .checks label { display: flex; align-items: center; gap: 8px; margin: 0; font-weight: 400; }
        .checks .all { grid-column: 1 / -1; border-bottom: 1px solid var(--line); padding-bottom: 8px; font-weight: 600; }
        .button { display: inline-flex; justify-content: center; align-items: center; min-height: 36px; padding: 7px 12px; border: 1px solid var(--upland-blue); border-radius: 6px; background: var(--upland-blue); color: white; font: inherit; font-weight: 700; text-decoration: none; cursor: pointer; }
        .button:hover { background: var(--upland-dark); }
        .button.secondary { border-color: rgba(255,255,255,.55); background: #fff; color: var(--upland-dark); }
        .button.danger { border-color: #e0c4c1; background: white; color: var(--red); }
        .inline-form { display: inline; }
        .actions { display: flex; flex-wrap: wrap; gap: 6px; }
        .notice { margin-bottom: 16px; padding: 10px 12px; border-left: 3px solid var(--green); background: #eaf4f0; }
        .notice.error { border-color: var(--red); background: #fbefed; }
        .table-wrap { overflow-x: auto; }
        table { width: 100%; border-collapse: collapse; text-align: left; }
        th, td { padding: 10px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }
        th { color: var(--muted); font-size: 12px; font-weight: 650; }
        td { min-width: 92px; }
        .status { font-weight: 700; text-transform: uppercase; font-size: 11px; }
        .status.completed { color: var(--green); }
        .status.failed, .status.interrupted { color: var(--red); }
        .stack { display: grid; gap: 24px; }
        [hidden] { display: none !important; }
        @media (max-width: 780px) { .app-header { padding: 12px 14px; } .header-mark { flex-basis: 34px; width: 34px; height: 34px; } .content { padding: 18px 14px 32px; } .layout { grid-template-columns: 1fr; } h1 { font-size: 18px; } .panel { padding: 16px; } .row { grid-template-columns: 1fr; gap: 0; } .header-actions .button { padding: 7px 9px; } }
    </style>
</head>
<body>
<main class="shell">
    <header class="app-header">
        <div class="brand-title"><span class="header-mark">U</span><div><div class="crumbs"><a href="{{ url_for('index') }}">API Automation</a> / Scheduler</div><h1>Test Schedules</h1></div></div>
        <div class="header-actions"><a class="button secondary" href="{{ url_for('index') }}">Dashboard</a><a class="button secondary" href="{{ url_for('scheduler_page') }}" title="Refresh schedules and run history">&#x21bb; Refresh</a></div>
    </header>
    <div class="content">
    {% if message %}<div class="notice">{{ message }}</div>{% endif %}
    {% if error %}<div class="notice error" role="alert">{{ error }}</div>{% endif %}
    <div class="layout">
        <section class="panel">
            <h2>Create schedule</h2>
            <form method="post" action="{{ url_for('create_schedule') }}">
                <div class="field"><label for="schedule-name">Name</label><input id="schedule-name" name="name" maxlength="100" required placeholder="e.g. Weekly QA smoke suite" /></div>
                <div class="field"><label for="schedule-environment">Environment</label><select id="schedule-environment" name="environment" required>{% for key, profile in test_environments.items() %}<option value="{{ key }}">{{ profile.label }}</option>{% endfor %}</select></div>
                <div class="field"><label>Sections</label><div class="checks">
                    <label class="all"><input type="checkbox" name="section" value="all" /> All sections</label>
                    {% for section in sections %}<label><input type="checkbox" name="section" value="{{ section }}" /> {{ section }}</label>{% endfor %}
                </div></div>
                <div class="field"><label for="schedule-type">Schedule type</label><select id="schedule-type" name="schedule_type"><option value="recurring">Recurring</option><option value="once">One time</option></select></div>
                <div id="once-fields" hidden>
                    <div class="field"><label for="run-date">Run date</label><input id="run-date" name="run_date" type="date" /></div>
                </div>
                <div id="recurring-fields">
                    <div class="field"><label for="frequency">Repeat</label><select id="frequency" name="frequency"><option value="weekly">Weekly</option><option value="monthly">Monthly</option></select></div>
                    <div class="field" id="weekday-field"><label for="weekday">Day of week</label><select id="weekday" name="weekday">{% for day in weekdays %}<option value="{{ loop.index0 }}">{{ day }}</option>{% endfor %}</select></div>
                    <div class="field" id="month-day-field" hidden><label for="month-day">Day of month</label><input id="month-day" name="month_day" type="number" min="1" max="31" value="1" /></div>
                </div>
                <div class="field"><label for="run-at">Run time ({{ server_timezone }})</label><input id="run-at" name="run_at" type="time" required value="02:00" /></div>
                <button class="button" type="submit">Save schedule</button>
            </form>
        </section>
        <div class="stack">
            <section class="panel">
                <h2>Schedules</h2>
                {% if schedules %}<div class="table-wrap"><table><thead><tr><th>Name / sections</th><th>Environment</th><th>Timing</th><th>Next run</th><th>Status</th><th>Actions</th></tr></thead><tbody>
                {% for schedule in schedules %}<tr>
                    <td><strong>{{ schedule.name }}</strong><br /><span class="muted">{{ schedule.sections|join(', ') }}</span></td>
                    <td>{{ test_environments.get(schedule.environment, {}).get('label', schedule.environment) }}</td>
                    <td>{% if schedule.schedule_type == 'once' %}One time{% elif schedule.frequency == 'weekly' %}Weekly, {{ weekdays[schedule.weekday] }}{% else %}Monthly, day {{ schedule.month_day }}{% endif %}<br />{{ schedule.run_at }}</td>
                    <td>{{ schedule.next_run_at or 'Completed' }}</td>
                    <td><span class="status {{ 'completed' if schedule.enabled else 'muted' }}">{{ 'Enabled' if schedule.enabled else 'Paused / complete' }}</span></td>
                    <td><div class="actions">
                        {% if schedule.next_run_at %}<form class="inline-form" method="post" action="{{ url_for('toggle_schedule', schedule_id=schedule.id) }}"><input type="hidden" name="enabled" value="{{ 0 if schedule.enabled else 1 }}" /><button class="button secondary" type="submit">{{ 'Pause' if schedule.enabled else 'Enable' }}</button></form>{% endif %}
                        <form class="inline-form" method="post" action="{{ url_for('delete_schedule', schedule_id=schedule.id) }}" onsubmit="return confirm('Delete this schedule? Run history will be kept.');"><button class="button danger" type="submit" aria-label="Delete {{ schedule.name }}">Delete</button></form>
                    </div></td>
                </tr>{% endfor %}
                </tbody></table></div>{% else %}<p class="muted">No schedules yet.</p>{% endif %}
            </section>
            <section class="panel">
                <h2>Run history</h2>
                {% if runs %}<div class="table-wrap"><table><thead><tr><th>Started</th><th>Schedule</th><th>Environment / sections</th><th>Status</th><th>Log</th></tr></thead><tbody>
                {% for run in runs %}<tr>
                    <td>{{ run.started_at }}{% if run.finished_at %}<br /><span class="muted">Finished {{ run.finished_at }}</span>{% endif %}</td>
                    <td>{{ run.schedule_name }}<br /><span class="muted">{{ run.summary }}</span></td>
                    <td>{{ test_environments.get(run.environment, {}).get('label', run.environment) }}<br /><span class="muted">{{ run.sections|join(', ') }}</span></td>
                    <td><span class="status {{ run.status }}">{{ run.status }}</span></td>
                    <td>{% if run.log_file %}<a href="{{ url_for('download_schedule_log', run_id=run.id) }}">Download</a>{% else %}<span class="muted">Pending</span>{% endif %}</td>
                </tr>{% endfor %}
                </tbody></table></div>{% else %}<p class="muted">No scheduled runs yet.</p>{% endif %}
            </section>
        </div>
    </div>
    </div>
</main>
<script>
const typeSelect = document.getElementById('schedule-type');
const frequencySelect = document.getElementById('frequency');
const onceFields = document.getElementById('once-fields');
const recurringFields = document.getElementById('recurring-fields');
const weekdayField = document.getElementById('weekday-field');
const monthDayField = document.getElementById('month-day-field');
function updateScheduleFields() {
    const oneTime = typeSelect.value === 'once';
    onceFields.hidden = !oneTime;
    recurringFields.hidden = oneTime;
    document.getElementById('run-date').required = oneTime;
    const monthly = frequencySelect.value === 'monthly';
    weekdayField.hidden = monthly;
    monthDayField.hidden = !monthly;
}
typeSelect.addEventListener('change', updateScheduleFields);
frequencySelect.addEventListener('change', updateScheduleFields);
updateScheduleFields();
</script>
</body>
</html>
"""
    return render_template_string(
        template,
        message=message,
        error=error,
        status_code=status_code,
        schedules=schedules,
        runs=runs,
        sections=_test_sections(),
        test_environments=TEST_ENVIRONMENTS,
        weekdays=weekdays,
        server_timezone=datetime.now().astimezone().tzname() or "server time",
    ), status_code


@app.get("/scheduler")
def scheduler_page():
    return _scheduler_page(request.args.get("message", ""))


@app.post("/scheduler/create")
def create_schedule():
    name = request.form.get("name", "").strip()
    environment_key = request.form.get("environment", "").strip()
    selected_sections = list(dict.fromkeys(request.form.getlist("section")))
    schedule_type = request.form.get("schedule_type", "").strip()
    run_at = request.form.get("run_at", "").strip()
    available_sections = _test_sections()
    if not name or len(name) > 100:
        return _scheduler_page(error="Enter a schedule name up to 100 characters.", status_code=400)
    if environment_key not in TEST_ENVIRONMENTS:
        return _scheduler_page(error="Select a configured environment.", status_code=400)
    if not selected_sections or any(section != "all" and section not in available_sections for section in selected_sections):
        return _scheduler_page(error="Select at least one valid test section.", status_code=400)
    selected_sections = ["all"] if "all" in selected_sections else selected_sections
    if schedule_type not in {"once", "recurring"}:
        return _scheduler_page(error="Select one-time or recurring scheduling.", status_code=400)
    try:
        parsed_time = datetime.strptime(run_at, "%H:%M").time()
    except ValueError:
        return _scheduler_page(error="Enter a valid run time.", status_code=400)

    schedule = {
        "name": name,
        "environment": environment_key,
        "sections": selected_sections,
        "schedule_type": schedule_type,
        "run_at": parsed_time.strftime("%H:%M"),
        "frequency": None,
        "weekday": None,
        "month_day": None,
    }
    if schedule_type == "once":
        run_date = request.form.get("run_date", "").strip()
        try:
            run_at_datetime = datetime.strptime(f"{run_date} {run_at}", "%Y-%m-%d %H:%M")
        except ValueError:
            return _scheduler_page(error="Choose a valid one-time run date and time.", status_code=400)
        if run_at_datetime <= datetime.now():
            return _scheduler_page(error="The one-time schedule must be in the future (server-local time).", status_code=400)
        schedule["next_run_at"] = _stamp(run_at_datetime)
    else:
        frequency = request.form.get("frequency", "").strip()
        schedule["frequency"] = frequency
        if frequency == "weekly":
            try:
                schedule["weekday"] = int(request.form.get("weekday", ""))
                schedule["next_run_at"] = _stamp(next_occurrence(frequency, schedule["run_at"], weekday=schedule["weekday"]))
            except (ValueError, TypeError) as exc:
                return _scheduler_page(error=str(exc) or "Choose a valid weekday.", status_code=400)
        elif frequency == "monthly":
            try:
                schedule["month_day"] = int(request.form.get("month_day", ""))
                schedule["next_run_at"] = _stamp(next_occurrence(frequency, schedule["run_at"], month_day=schedule["month_day"]))
            except (ValueError, TypeError) as exc:
                return _scheduler_page(error=str(exc) or "Choose a valid day of the month.", status_code=400)
        else:
            return _scheduler_page(error="Choose weekly or monthly recurrence.", status_code=400)

    _get_schedule_store().create(schedule)
    return redirect(url_for("scheduler_page", message="Schedule saved."))


@app.post("/scheduler/<int:schedule_id>/toggle")
def toggle_schedule(schedule_id: int):
    enabled = request.form.get("enabled") == "1"
    if not _get_schedule_store().set_enabled(schedule_id, enabled):
        abort(404)
    return redirect(url_for("scheduler_page", message="Schedule updated."))


@app.post("/scheduler/<int:schedule_id>/delete")
def delete_schedule(schedule_id: int):
    if not _get_schedule_store().delete(schedule_id):
        abort(404)
    return redirect(url_for("scheduler_page", message="Schedule deleted. Run history was kept."))


@app.get("/scheduler/runs/<int:run_id>/log")
def download_schedule_log(run_id: int):
    run = _get_schedule_store().get_run(run_id)
    if not run or not run.get("log_file"):
        abort(404)
    log_path = SCHEDULE_LOG_DIR / Path(run["log_file"]).name
    if not log_path.is_file():
        abort(404)
    return send_file(log_path, as_attachment=True, download_name=log_path.name)


@app.post("/run")
def run_tests():
    section = request.form.get("section", "all")
    scope_name = section if section else "all"
    environment_key = request.form.get("environment", _default_environment_key())
    if environment_key not in TEST_ENVIRONMENTS:
        environment_key = _default_environment_key()
    provider_id, service_type_id, filter_error = _parse_seed_filters(
        request.form.get("provider_id", ""), request.form.get("service_type_id", "")
    )
    if not filter_error:
        filter_error = _validate_seed_filters(environment_key, provider_id, service_type_id)
    selected_node_ids = list(dict.fromkeys(item.strip() for item in request.form.getlist("node_id") if item.strip()))
    if selected_node_ids and not filter_error:
        try:
            mapped_node_ids = {
                item["nodeid"]
                for items in _pytest_case_mapping(scope_name).values()
                for item in items
            }
        except (RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
            return jsonify({"error": f"Could not validate selected tests: {exc}"}), 503
        invalid_node_ids = [node_id for node_id in selected_node_ids if node_id not in mapped_node_ids]
        if invalid_node_ids:
            return jsonify({"error": "One or more selected tests do not belong to this section or action."}), 400
    if filter_error:
        _save_last_run({
            "scope": scope_name,
            "status": "blocked",
            "environment": environment_key,
            "environment_label": _environment_profile(environment_key)["label"],
            "provider_id": provider_id,
            "service_type_id": service_type_id,
            "pytest_exit_code": None,
            "pytest_output": filter_error,
            "testrail": {"status": "skipped", "message": filter_error},
            "sections": {},
            "current_section": "",
            "generated_at": datetime.utcnow().isoformat(),
        })
        return redirect(url_for("index"))
    if not _run_lock.acquire(blocking=False):
        return redirect(url_for("index"))

    sections = _test_sections() if scope_name.lower() == "all" else [scope_name]
    section_status = {name: "queued" for name in sections}

    _save_last_run({
        "scope": scope_name,
        "environment": environment_key,
        "environment_label": _environment_profile(environment_key)["label"],
        "provider_id": provider_id,
        "service_type_id": service_type_id,
        "status": "running",
        "current_section": "",
        "sections": section_status,
        "pytest_exit_code": None,
        "pytest_output": "",
        "testrail": {"status": "pending", "message": "TestRail publication will start after pytest completes."},
        "generated_at": datetime.utcnow().isoformat(),
    })

    def run_and_release():
        try:
            _execute_test_run(scope_name, environment_key, provider_id, service_type_id, selected_node_ids or None)
        finally:
            _run_lock.release()

    threading.Thread(target=run_and_release, daemon=True).start()
    return redirect(url_for("index"))


_COMPATIBILITY_MODULES = {
    "workorders": "tests.workorders.{module}",
    "self_service_requests": "tests.self_service_requests.{module}",
    "invoice_intelligence": "tests.invoice_intelligence.{module}",
}


@app.post("/api/check-compatibility")
def api_check_compatibility():
    payload = request.get_json(silent=True) or {}
    scope = str(payload.get("scope") or "").strip()
    file_name = str(payload.get("file") or "").strip()
    environment_key = str(payload.get("environment") or "").strip() or _default_environment_key()
    if environment_key not in TEST_ENVIRONMENTS:
        environment_key = _default_environment_key()

    module_template = _COMPATIBILITY_MODULES.get(scope)
    if not module_template or not file_name.endswith(".py"):
        return jsonify({"error": "No compatibility check is configured for this action."}), 404

    provider_id, service_type_id, filter_error = _parse_seed_filters(
        str(payload.get("provider_id") or ""), str(payload.get("service_type_id") or "")
    )
    if filter_error:
        return jsonify({"error": filter_error}), 400

    module_path = module_template.format(module=file_name[:-3])
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        return jsonify({"error": f"Unable to load test module: {exc}"}), 404

    preview = getattr(module, "preview_seed_compatibility", None)
    if not callable(preview):
        return jsonify({"error": "This action does not support DB compatibility checks yet."}), 404

    previous_db_name = os.environ.get("DB_NAME")
    profile = _environment_profile(environment_key)
    os.environ["DB_NAME"] = profile["db_name"]
    db = None
    try:
        db = DBHelper()
        candidates = preview(db, provider_id=provider_id, service_type_id=service_type_id, limit=2)
        if not candidates:
            if scope == "invoice_intelligence":
                count = db.fetch_scalar("SELECT COUNT(*) FROM [core].[InvoiceNarrative]")
                message = "No data available in [core].[InvoiceNarrative]; tests were not executed." if count == 0 else "InvoiceNarrative contains data, but no invoice matches the selected provider/header mapping."
                return jsonify({"compatible": [], "message": message})
            return jsonify({"compatible": [], "message": "This API doesn't have any compatibility configured."})
        return jsonify({"compatible": candidates})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"DB compatibility check failed: {exc}"}), 500
    finally:
        if db:
            db.close()
        if previous_db_name is None:
            os.environ.pop("DB_NAME", None)
        else:
            os.environ["DB_NAME"] = previous_db_name


@app.post("/api/cancel-workorders")
def api_cancel_workorders():
    payload = request.get_json(silent=True) or {}
    environment_key = str(payload.get("environment") or "").strip() or _default_environment_key()
    if environment_key not in TEST_ENVIRONMENTS:
        environment_key = _default_environment_key()

    numbers = parse_workorder_numbers(str(payload.get("workorder_numbers") or ""))
    if not numbers:
        return jsonify({"error": "Enter at least one workorder number."}), 400
    if len(numbers) > 50:
        return jsonify({"error": "Enter 50 or fewer workorder numbers per request."}), 400

    previous_db_name = os.environ.get("DB_NAME")
    profile = _environment_profile(environment_key)
    os.environ["DB_NAME"] = profile["db_name"]
    db = None
    try:
        db = DBHelper()
        results = cancel_workorders(db, numbers)
        return jsonify({"results": results})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"Workorder cancellation failed: {exc}"}), 500
    finally:
        if db:
            db.close()
        if previous_db_name is None:
            os.environ.pop("DB_NAME", None)
        else:
            os.environ["DB_NAME"] = previous_db_name


@app.post("/api/cancel-self-service-requests")
def api_cancel_self_service_requests():
    payload = request.get_json(silent=True) or {}
    environment_key = str(payload.get("environment") or "").strip() or _default_environment_key()
    if environment_key not in TEST_ENVIRONMENTS:
        environment_key = _default_environment_key()

    numbers = parse_request_numbers(str(payload.get("request_numbers") or ""))
    if not numbers:
        return jsonify({"error": "Enter at least one self service request number."}), 400
    if len(numbers) > 50:
        return jsonify({"error": "Enter 50 or fewer request numbers per request."}), 400

    previous_db_name = os.environ.get("DB_NAME")
    profile = _environment_profile(environment_key)
    os.environ["DB_NAME"] = profile["db_name"]
    db = None
    try:
        db = DBHelper()
        results = cancel_self_service_requests(db, numbers)
        return jsonify({"results": results})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"Self service request cancellation failed: {exc}"}), 500
    finally:
        if db:
            db.close()
        if previous_db_name is None:
            os.environ.pop("DB_NAME", None)
        else:
            os.environ["DB_NAME"] = previous_db_name


@app.get("/download/<file_name>")
def download_pdf(file_name: str):
    target = PDF_DIR / file_name
    if not target.exists():
        return "File not found", 404

    data = target.read_bytes()
    return (
        data,
        200,
        {
            "Content-Type": "application/pdf",
            "Content-Disposition": f"attachment; filename={file_name}",
        },
    )


if __name__ == "__main__":
    _serve_local_dashboard()
