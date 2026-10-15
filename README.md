# Cimpl API Automation

Internal Python-based API automation project for validating API responses against SQL Server, generating dashboard artifacts, and publishing mapped results to TestRail.

## What Is In This Repo

- Pytest-based API validation suites under `tests/`
- Shared helpers under `utils/`
- A lightweight Flask dashboard in `dashboard_server.py`
- Persistent one-time, weekly, and monthly dashboard test schedules
- Scheduled run history and downloadable logs under `reports/`
- HTML, JSON, and PDF report generation
- Optional TestRail publishing

## Request API Automation With Copilot

QAs should use [QA_API_AUTOMATION_REQUEST_TEMPLATE.md](QA_API_AUTOMATION_REQUEST_TEMPLATE.md)
for new API automation and extensions. It lists the API, TestRail, Jira, Confluence,
reference-case, and DB inputs to provide, plus the standard review-to-execution workflow
and required response/DB evidence.

## What Should Not Be Committed

These are intentionally excluded from source control:

- `.env`
- `.venv/`
- `reports/`
- local cache files

Only commit sanitized configuration templates such as `.env.example`.

## Local Setup

1. Create and activate a virtual environment.
2. Install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

3. Copy `.env.example` to `.env` and fill in real values.

## Required Environment Variables

The runtime expects values such as:

- `TOKEN_URL`
- `CLIENT_ID`
- `CLIENT_SECRET`
- `API_USERNAME`
- `API_PASSWORD`
- `GRANT_TYPE`
- `BASE_URL`
- `DB_SERVER`
- `DB_NAME`
- `DB_USER`
- `DB_PASSWORD`
- `DB_TRUSTED_CONNECTION`
- `TESTRAIL_ENABLE_ON_RUN`
- `TESTRAIL_URL`
- `TESTRAIL_EMAIL`
- `TESTRAIL_API_KEY`
- `TESTRAIL_PROJECT_ID`
- `TESTRAIL_MILESTONE_ID`
- `TESTRAIL_SUITE_ID`

Use `.env.example` as the template. Never commit the populated `.env` file.

## Run Tests

Run all tests:

```powershell
.\.venv\Scripts\python.exe -m pytest tests -v
```

Run one section:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\location -v
```

Invoice narrative: 38 cases (TestRail C17893532-C17893539 and C17893545-C17893574):

```powershell
.\.venv\Scripts\python.exe -m pytest tests\invoice_intelligence -v
```

The dashboard hierarchy is Payment Readiness > Invoice Intelligence > IIT02 - Get
Invoice Narrative. Each invoice action has its own counts, results, Show Cases,
and run controls; additional IIT actions can be registered alongside IIT02. Before API
authentication, the suite checks `[core].[InvoiceNarrative]`; an empty table skips
all cases with an informational message. Positive requests use invoice identifiers
from that table and the currency ID from the related invoice header. Both AI
response fields are mandatory non-empty strings and their text must match
`AIAnalysis` and `AIBrief`, ignoring whitespace differences only. Dashboard output
explicitly records each successful DB comparison.
Every execution records the full request body, HTTP status, and response body
before assertions, including failures. Coverage includes required identifier/date
validation, malformed JSON/types, DB-confirmed incompatible invoice keys, missing
account data, unauthorized access, invalid tokens, unsupported methods, and literal
SQL/HTML-like account text. Verified no-match behavior is HTTP 204 with an empty
body; a successful HTTP 200 narrative must contain both mandatory AI fields.
IIT02 also provides a read-only Check DB Compatibility preview.

## Run Dashboard

```powershell
.\.venv\Scripts\python.exe dashboard_server.py
```

Then open `http://127.0.0.1:5050`.

Open **Scheduler** in the dashboard to select an environment and one or more test sections,
then create a one-time or recurring schedule. Weekly and monthly times use the server's local
time zone. A monthly day that does not exist in a given month runs on that month's final day.
Schedules and run logs are stored in `reports/dashboard_scheduler.sqlite3` and
`reports/scheduled_runs/`; keep these when deploying updates if you want to preserve history.

When launched this way, the dashboard stops after 30 minutes without user activity. Its
background status polling does not reset the timer, and an active test run delays shutdown.
Set `DASHBOARD_IDLE_TIMEOUT_SECONDS` to change the timeout, or to `0` to disable it.

## Add This Project To GitHub

1. Create a new private repository in GitHub.
2. Open a terminal in this project root.
3. Initialize Git locally:

```powershell
git init
git branch -M main
```

4. Stage the repo:

```powershell
git add .
```

5. Create the first commit:

```powershell
git commit -m "Initial commit for API automation project"
```

6. Connect to GitHub:

```powershell
git remote add origin https://github.com/<org-or-user>/<repo-name>.git
```

7. Push:

```powershell
git push -u origin main
```

## Recommended GitHub Model

- Store source code in a private GitHub repository.
- Keep secrets out of GitHub.
- Pull the repo onto a shared internal server for team usage.
- Run tests and host the dashboard from that server.
