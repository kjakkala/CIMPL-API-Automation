"""Builds a product-agnostic 'base project' zip from this repo.

Strips Cimpl-specific tests/business content and keeps the reusable
framework: dashboard, conftest fixtures, DB/TestRail/PDF helpers, and
one example test per pattern (GET + POST-with-DB-validation).

Usage:
    python packaging/build_base_project.py
Output:
    dist/api-automation-base-project.zip
"""

from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STAGE = ROOT / "dist" / "base-project"
ZIP_PATH = ROOT / "dist" / "api-automation-base-project.zip"

FILES_TO_COPY = [
    "conftest.py",
    "dashboard_server.py",
    "dashboard_wsgi.py",
    "pytest.ini",
    "requirements.txt",
    "DEPLOYMENT_IIS.md",
    ".gitignore",
]

UTILS_TO_COPY = [
    "__init__.py",
    "db_helper.py",
    "post_api_db_scripts.py",
    "testrail_client.py",
    "dashboard_scheduler.py",
    "test_metadata.py",
    "pdf_report.py",
]

STATIC_ASSETS_TO_COPY = [
    "qa-rover.svg",
]

ENV_EXAMPLE = """# Copy this file to .env and fill in real values. Never commit .env to source control.

# --- Shared OAuth2 credentials ---
CLIENT_ID=
CLIENT_SECRET=
API_USERNAME=your_username
API_PASSWORD=your_password
GRANT_TYPE=password

# --- Standalone/default target used when pytest runs outside the dashboard ---
TOKEN_URL=https://your-api-host/auth/oauth/token
BASE_URL=https://your-api-host/api/v1

# --- Dashboard environments (add profiles here to populate the dashboard selector) ---
TEST_ENVIRONMENTS=env1,env2
DEFAULT_TEST_ENVIRONMENT=env1

TEST_ENV_ENV1_LABEL=Environment 1
TEST_ENV_ENV1_TOKEN_URL=https://your-api-host/auth/oauth/token
TEST_ENV_ENV1_BASE_URL=https://your-api-host/api/v1
TEST_ENV_ENV1_DB_NAME=your_db_name_env1

TEST_ENV_ENV2_LABEL=Environment 2
TEST_ENV_ENV2_TOKEN_URL=https://your-api-host-2/auth/oauth/token
TEST_ENV_ENV2_BASE_URL=https://your-api-host-2/api/v1
TEST_ENV_ENV2_DB_NAME=your_db_name_env2

# --- TestRail dashboard link template ---
TESTRAIL_CASE_URL_TEMPLATE=https://yourcompany.testrail.com/index.php?/cases/view/{case_id}

# --- TestRail run integration ---
TESTRAIL_ENABLE_ON_RUN=1
TESTRAIL_URL=https://yourcompany.testrail.com
TESTRAIL_EMAIL=your_email@example.com
TESTRAIL_API_KEY=your_api_key
TESTRAIL_PROJECT_ID=1
TESTRAIL_MILESTONE_ID=1
TESTRAIL_SUITE_ID=1

# --- SQL Server connection ---
DB_SERVER=your_db_server
DB_NAME=your_db_name
DB_USER=
DB_PASSWORD=
DB_TRUSTED_CONNECTION=1
"""

README = """# API Automation - Base Project

Reusable Python API automation framework: pytest suites, SQL Server
validation, a Flask test dashboard, and optional TestRail publishing.

This is a stripped framework template. It contains one example GET
test and one example POST test with DB validation. Add your own
`tests/<section>/` folders following that pattern; the dashboard
auto-discovers sections from folder names.

## Setup

```powershell
python -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Fill in `.env` with your API, database, and TestRail values.

## Run tests

```powershell
.\\.venv\\Scripts\\python.exe -m pytest tests -v
```

## Run dashboard

```powershell
.\\.venv\\Scripts\\python.exe dashboard_server.py
```

Open http://127.0.0.1:5050

See `DEPLOYMENT_IIS.md` for production deployment behind IIS.
"""

EXAMPLE_GET_TEST = '''"""Example GET test demonstrating the framework pattern."""
from __future__ import annotations

import os

import pytest

from utils.test_metadata import api_case


@api_case(section="Examples", operation="GET /v1/example-items", testrail_id="", title="Get example items")
def test_get_example_items(api_context):
    response = api_context.get("example-items")
    assert response.status == 200, f"Expected 200, got {response.status}: {response.text()}"
    payload = response.json()
    assert isinstance(payload, (list, dict)), "Expected a JSON list or object in the response"
'''

EXAMPLE_POST_TEST = '''"""Example POST test demonstrating DB-backed seed data and validation."""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from utils.post_api_db_scripts import PostApiDbScripts
from utils.test_metadata import api_case


def _seed(db) -> dict:
    """Replace with a query that selects real, compatible seed data for your product."""
    row = db.fetch_one("SELECT TOP 1 1 AS placeholder")
    assert row, "No seed data available"
    return {"placeholder": row["placeholder"]}


@api_case(section="Examples", operation="POST /v1/example-items", testrail_id="", title="Create example item")
def test_post_example_item(api_context, db, request):
    seed = _seed(db)
    marker = f"AUTO-EXAMPLE-{date.today():%Y%m%d}"
    body = {"name": marker, "dueDate": (date.today() + timedelta(days=2)).isoformat()}

    scripts = PostApiDbScripts(db)
    before = scripts.count_rows(table="dbo.ExampleItem", where_sql="Name = ?", params=(marker,))

    response = api_context.post(
        "example-items",
        data=json.dumps(body),
        headers={"Content-Type": "application/json", "accept": "application/json"},
    )

    assert response.status in {200, 201}, f"Expected success, got {response.status}: {response.text()}"

    after = scripts.count_rows(table="dbo.ExampleItem", where_sql="Name = ?", params=(marker,))
    scripts.assert_count_delta(before=before, after=after, expected_delta=1, context="Create example item")
'''


def _copy_files() -> None:
    for name in FILES_TO_COPY:
        shutil.copy2(ROOT / name, STAGE / name)

    (STAGE / "utils").mkdir(parents=True, exist_ok=True)
    for name in UTILS_TO_COPY:
        shutil.copy2(ROOT / "utils" / name, STAGE / "utils" / name)

    (STAGE / "static").mkdir(parents=True, exist_ok=True)
    for name in STATIC_ASSETS_TO_COPY:
        shutil.copy2(ROOT / "static" / name, STAGE / "static" / name)

    (STAGE / "tests" / "examples").mkdir(parents=True, exist_ok=True)
    (STAGE / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (STAGE / "tests" / "examples" / "__init__.py").write_text("", encoding="utf-8")
    (STAGE / "tests" / "examples" / "test_get_example.py").write_text(EXAMPLE_GET_TEST, encoding="utf-8")
    (STAGE / "tests" / "examples" / "test_post_example.py").write_text(EXAMPLE_POST_TEST, encoding="utf-8")

    (STAGE / ".env.example").write_text(ENV_EXAMPLE, encoding="utf-8")
    (STAGE / "README.md").write_text(README, encoding="utf-8")


def _genericize_dashboard() -> None:
    path = STAGE / "dashboard_server.py"
    text = path.read_text(encoding="utf-8")

    text = re.sub(
        r"SECTION_DISPLAY_NAMES = \{.*?\n\}\n",
        "SECTION_DISPLAY_NAMES = {}\n",
        text,
        count=1,
        flags=re.DOTALL,
    )
    text = re.sub(
        r"SECTION_DESCRIPTIONS = \{.*?\n\}\n",
        "SECTION_DESCRIPTIONS = {}  # Add per-section guides here, e.g. {\"examples\": {...}}\n",
        text,
        count=1,
        flags=re.DOTALL,
    )
    text = re.sub(
        r"WORKORDER_ACTIONS = \{.*?\n\}\n",
        "WORKORDER_ACTIONS = {}  # Example: map \"POST /v1/your-action\" to its test filename\n",
        text,
        count=1,
        flags=re.DOTALL,
    )
    text = re.sub(
        r"SELF_SERVICE_ACTIONS = \{.*?\n\}\n",
        "SELF_SERVICE_ACTIONS = {}  # Example: map an action label to its operation/file/implemented flag\n",
        text,
        count=1,
        flags=re.DOTALL,
    )
    text = re.sub(
        r'        row = db\.fetch_one\(\n'
        r'            "SELECT TOP 1 p\.ProviderId.*?\n'
        r'            tuple\(params\),\n'
        r'        \)\n',
        '        # NOTE: This query is a placeholder. Replace with a query against your\n'
        '        # own schema to validate the optional seed filters below.\n'
        '        row = db.fetch_one(\n'
        '            "SELECT 1",\n'
        '            tuple(),\n'
        '        )\n',
        text,
        count=1,
        flags=re.DOTALL,
    )
    path.write_text(text, encoding="utf-8")


def _genericize_conftest() -> None:
    path = STAGE / "conftest.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace("https://upland.testrail.com/index.php?/cases/view/{case_id}", "https://yourcompany.testrail.com/index.php?/cases/view/{case_id}")
    path.write_text(text, encoding="utf-8")


def _genericize_deployment_doc() -> None:
    path = STAGE / "DEPLOYMENT_IIS.md"
    text = path.read_text(encoding="utf-8")
    replacements = {
        "cplca1webq01.uplandsoftware.net": "your-api-host.example.com",
        "upland.testrail.com": "yourcompany.testrail.com",
        "Cplca1sqldq02.uplandsoftware.net\\\\qa": "your-db-server\\\\instance",
        "cimpl.SelfServiceActionLog": "your persistence table(s)",
        "TEST_ENV_QA_MAIN": "TEST_ENV_ENV1",
        "TEST_ENV_QA_RC": "TEST_ENV_ENV2",
        "rc-auth/oauth/token": "auth/oauth/token",
        "rc-requestApi": "requestApi",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8")


def _zip() -> None:
    ZIP_PATH.parent.mkdir(parents=True, exist_ok=True)
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as archive:
        for file_path in STAGE.rglob("*"):
            if file_path.is_file() and "__pycache__" not in file_path.parts:
                archive.write(file_path, file_path.relative_to(STAGE))


def main() -> None:
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True, exist_ok=True)

    _copy_files()
    _genericize_dashboard()
    _genericize_conftest()
    _genericize_deployment_doc()
    _zip()

    print(f"Base project staged at: {STAGE}")
    print(f"Zip created at: {ZIP_PATH}")


if __name__ == "__main__":
    main()
