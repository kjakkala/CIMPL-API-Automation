# IIS Deployment Guide

This guide describes how to deploy the Cimpl API Automation dashboard on a Windows QA server using IIS as the public web server and Waitress as the Flask WSGI server.

## Architecture

```text
Browser
  |
  v
IIS HTTPS :443/ApiAutomation
  |
  | URL Rewrite + ARR reverse proxy (as an IIS Application under /ApiAutomation)
  v
Waitress 127.0.0.1:5050
  |
  v
dashboard_server.py  (APP_BASE_PATH=/ApiAutomation)
  |
  +-- pytest and Playwright API tests
  +-- Cimpl APIs
  +-- SQL Server
  +-- TestRail
```

Do not expose Flask's development server directly for a shared QA deployment. The app is published under the `/ApiAutomation` base path; see the IIS Reverse Proxy section for details.

## Server Requirements

Install or enable the following on the QA server:

- Windows Server with IIS
- Python 3.11 or newer
- IIS URL Rewrite
- IIS Application Request Routing (ARR)
- Microsoft ODBC Driver 18 or newer for SQL Server
- Network access to the Cimpl APIs, SQL Server, and TestRail

Enable IIS roles from an elevated PowerShell prompt:

```powershell
Install-WindowsFeature Web-Server, Web-WebSockets, Web-Filtering, Web-Static-Content
```

Install IIS URL Rewrite and ARR through the approved Microsoft/IIS installation process. After installing ARR, enable proxying:

```text
IIS Manager
  -> Select the server node
  -> Application Request Routing Cache
  -> Server Proxy Settings
  -> Enable proxy
```

## Deploy the Project

Use a protected application directory, for example:

```text
C:\Apps\CimplApiAutomation
```

Copy the project source to that directory. Do not copy or commit local development artifacts:

- `.git`
- `.venv`
- `__pycache__`
- Local `reports` output
- Populated `.env` files from another machine

Create the server's `.env` separately and protect it with Windows file permissions.

## Python Environment

From an elevated or deployment PowerShell prompt:

```powershell
cd C:\Apps\CimplApiAutomation
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install waitress
```

Verify the environment:

```powershell
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -m pip check
```

For local development, start `dashboard_server.py`. The production WSGI entry point is
`dashboard_wsgi.py`; it starts the persistent schedule worker when Waitress loads the app.

## Configure `.env`

Create `C:\Apps\CimplApiAutomation\.env` on the server. Never place real secrets in source control.

Use the shared OAuth credentials and database credentials approved for the QA server. The environment profiles control which API/database pair is used by each dashboard run:

```dotenv
# Shared OAuth credentials
CLIENT_ID=
CLIENT_SECRET=
API_USERNAME=<qa-api-user>
API_PASSWORD=<qa-api-password>
GRANT_TYPE=password

# Standalone fallback values for direct pytest runs
TOKEN_URL=https://cplca1webq01.uplandsoftware.net/auth/oauth/token
BASE_URL=https://cplca1webq01.uplandsoftware.net/api/v1

# Dashboard environment selector
TEST_ENVIRONMENTS=qa-main,qa-rc
DEFAULT_TEST_ENVIRONMENT=qa-main

# QA Main
TEST_ENV_QA_MAIN_LABEL=QA Main
TEST_ENV_QA_MAIN_TOKEN_URL=https://cplca1webq01.uplandsoftware.net/auth/oauth/token
TEST_ENV_QA_MAIN_BASE_URL=https://cplca1webq01.uplandsoftware.net/api/v1
TEST_ENV_QA_MAIN_SELF_SERVICE_BASE_URL=https://cplca1webq01.uplandsoftware.net/requestApi
TEST_ENV_QA_MAIN_DB_NAME=CI_JK_CimplDevelopT1

# QA-RC
TEST_ENV_QA_RC_LABEL=QA-RC
TEST_ENV_QA_RC_TOKEN_URL=https://cplca1webq01.uplandsoftware.net/rc-auth/oauth/token
TEST_ENV_QA_RC_BASE_URL=https://cplca1webq01.uplandsoftware.net/api/v1
TEST_ENV_QA_RC_SELF_SERVICE_BASE_URL=https://cplca1webq01.uplandsoftware.net/rc-requestApi
TEST_ENV_QA_RC_DB_NAME=CI_JK_CimplRCT1

# SQL Server
DB_SERVER=<sql-server-instance>
DB_NAME=CI_JK_CimplDevelopT1
DB_USER=<sql-user>
DB_PASSWORD=<sql-password>
DB_TRUSTED_CONNECTION=0

# TestRail
TESTRAIL_ENABLE_ON_RUN=1
TESTRAIL_URL=https://upland.testrail.com
TESTRAIL_EMAIL=<testrail-user>
TESTRAIL_API_KEY=<testrail-api-key>
TESTRAIL_PROJECT_ID=30
TESTRAIL_MILESTONE_ID=2676
TESTRAIL_SUITE_ID=63080
```

For Windows integrated SQL authentication, use a service account with database permissions and set:

```dotenv
DB_TRUSTED_CONNECTION=1
```

```dotenv
# Reverse proxy base path (must match the IIS application path below)
APP_BASE_PATH=/ApiAutomation
```

The dashboard passes the selected environment's token URL, API URL, Self Service URL, and database name into the pytest child process. The dashboard process itself is not switched globally.

## Validate API and Database Access

Check the SQL Server ODBC driver:

```powershell
Get-OdbcDriver | Where-Object Name -like "*SQL Server*"
```

Confirm the service account can reach:

- `Cplca1sqldq02.uplandsoftware.net\qa`
- `CI_JK_CimplDevelopT1`
- `CI_JK_CimplRCT1`

Confirm outbound HTTPS access to:

- `cplca1webq01.uplandsoftware.net`
- `upland.testrail.com`

Run a collection check before starting the web server:

```powershell
cd C:\Apps\CimplApiAutomation
.\.venv\Scripts\python.exe -m py_compile dashboard_server.py
.\.venv\Scripts\python.exe -m pytest --collect-only -q
```

Run a small API slice from the server:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\self_service_requests\test_post_self_service_provision.py -k C17838560 -s
```

The Self Service Provision suite validates API responses and `cimpl.SelfServiceActionLog`.

## Start Waitress

The app is hosted under the `/ApiAutomation` base path in IIS, so `APP_BASE_PATH` must be set before Waitress starts. This makes the dashboard's generated links (`url_for`) include the `/ApiAutomation` prefix while the Flask app still listens on the site root internally.

Test Waitress locally first:

```powershell
cd C:\Apps\CimplApiAutomation
$env:APP_BASE_PATH = "/ApiAutomation"
.\.venv\Scripts\waitress-serve.exe --listen=127.0.0.1:5050 dashboard_wsgi:app
```

From the server, verify:

```powershell
Invoke-WebRequest http://127.0.0.1:5050
```

The dashboard listens locally at `http://127.0.0.1:5050` (unprefixed) and is reachable through IIS at:

```text
https://<qa-dashboard-hostname>/ApiAutomation
```

## Run Waitress as a Windows Service

Use the organization's approved Windows service wrapper. These steps use NSSM (Non-Sucking Service Manager).

### 1. Install NSSM

Download the approved NSSM build and place `nssm.exe` somewhere permanent, e.g. `C:\Tools\nssm\nssm.exe`. Add that folder to the system `PATH`, or reference the full path in every command below.

### 2. Create the service

From an elevated PowerShell prompt:

```powershell
nssm install CimplApiDashboard "C:\Apps\CimplApiAutomation\.venv\Scripts\waitress-serve.exe" "--listen=127.0.0.1:5050 dashboard_wsgi:app"
```

This opens the NSSM GUI editor if run without all required parameters; the command above is the non-interactive form and creates the service directly. You can also run `nssm install CimplApiDashboard` alone to use the GUI, then fill in:

```text
Path:             C:\Apps\CimplApiAutomation\.venv\Scripts\waitress-serve.exe
Startup directory: C:\Apps\CimplApiAutomation
Arguments:        --listen=127.0.0.1:5050 dashboard_wsgi:app
```

### 3. Set the startup directory explicitly

Waitress and the app resolve relative paths (`.env`, `reports\`) from the process's working directory, so set it explicitly:

```powershell
nssm set CimplApiDashboard AppDirectory "C:\Apps\CimplApiAutomation"
```

### 4. Set environment variables

`APP_BASE_PATH` must be set on the service process so `url_for()` emits the `/ApiAutomation` prefix:

```powershell
nssm set CimplApiDashboard AppEnvironmentExtra APP_BASE_PATH=/ApiAutomation
```

To add more than one variable, separate them with `\r\n` (NSSM's multi-value separator), for example in Python:

```powershell
nssm set CimplApiDashboard AppEnvironmentExtra "APP_BASE_PATH=/ApiAutomation`r`nPYTHONUNBUFFERED=1"
```

### 5. Run the service under a dedicated account

```powershell
nssm set CimplApiDashboard ObjectName ".\svc-cimpl-dashboard" "<account-password>"
```

Grant that account:

- Read access to the application directory
- Read access to `.env`
- Write access to `reports\`
- SQL Server permissions
- Outbound API and TestRail access
- "Log on as a service" right (NSSM grants this automatically when setting `ObjectName`)

### 6. Configure restart behavior and logging

```powershell
nssm set CimplApiDashboard AppExit Default Restart
nssm set CimplApiDashboard AppRestartDelay 5000
nssm set CimplApiDashboard AppStdout "C:\Apps\CimplApiAutomation\logs\service-stdout.log"
nssm set CimplApiDashboard AppStderr "C:\Apps\CimplApiAutomation\logs\service-stderr.log"
nssm set CimplApiDashboard AppRotateFiles 1
```

Create the `logs\` folder first (`New-Item -ItemType Directory C:\Apps\CimplApiAutomation\logs`) and grant the service account write access to it.

### 7. Start and verify

```powershell
Set-Service CimplApiDashboard -StartupType Automatic
Start-Service CimplApiDashboard
Get-Service CimplApiDashboard
Invoke-WebRequest http://127.0.0.1:5050
```

Keep Waitress bound to `127.0.0.1` so port `5050` is not exposed directly to the network.

### Useful NSSM commands

```powershell
nssm status CimplApiDashboard      # check current state
nssm restart CimplApiDashboard     # apply config changes
nssm edit CimplApiDashboard        # open the GUI editor for existing service
nssm remove CimplApiDashboard confirm   # uninstall the service
```

## Configure IIS Reverse Proxy

The dashboard is published under the `/ApiAutomation` base path, not at the site root. Create an IIS **Application** named `ApiAutomation` under the QA site (Default Web Site or the dedicated QA site), with a physical path such as:

```text
C:\Apps\CimplApiAutomation\iis
```

In IIS Manager: right-click the site -> **Add Application** -> Alias `ApiAutomation` -> Physical path as above.

Create `web.config` in that IIS application directory. Because IIS URL Rewrite match rules for an application are evaluated relative to the application's own path, the rule below does not need to reference `/ApiAutomation` explicitly -- IIS already strips it before matching:

```xml
<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <system.webServer>
    <rewrite>
      <rules>
        <rule name="ReverseProxyToWaitress" stopProcessing="true">
          <match url="(.*)" />
          <action
            type="Rewrite"
            url="http://127.0.0.1:5050/{R:1}"
            logRewrittenUrl="true" />
        </rule>
      </rules>
    </rewrite>
  </system.webServer>
</configuration>
```

Create an IIS HTTPS binding on the parent site:

```text
Type:       https
Port:       443
Host name:  <qa-dashboard-hostname>
Certificate: approved QA TLS certificate
```

Users should access the dashboard through:

```text
https://<qa-dashboard-hostname>/ApiAutomation
```

not directly through port `5050`. Confirm `APP_BASE_PATH=/ApiAutomation` is set for the Waitress service (see above) so links generated by the app (`url_for`) include the `/ApiAutomation` prefix and resolve correctly through this application.

## Firewall and Access

Allow inbound traffic only to the IIS HTTPS port, normally TCP `443`. Do not expose port `5050` externally.

Restrict dashboard access to the QA network or place it behind the organization's approved authentication/reverse-proxy layer. The dashboard displays API responses, database validation details, and TestRail links.

## Reports and Run Behavior

The dashboard writes shared artifacts under `reports\`:

```text
reports\last_run.json
reports\test_dashboard.json
reports\test_dashboard.html
reports\pdf\
```

The dashboard allows one run at a time in a single process. A production deployment should use one dashboard service instance unless cross-process locking and per-run report directories are added.

The scheduler stores schedules and run history in `reports\dashboard_scheduler.sqlite3`
and execution logs in `reports\scheduled_runs\`. The service account needs write access
to both paths. Schedules use the server's local time zone; a missed one-time run becomes
due and will run when the service starts again. Keep the scheduler database and log folder
when deploying application updates. Run one dashboard service instance so scheduler and
manual runs share the same run lock.

## Deployment Checklist

- [ ] Python installed
- [ ] IIS installed
- [ ] URL Rewrite installed
- [ ] ARR installed and proxying enabled
- [ ] SQL Server ODBC Driver installed
- [ ] Project copied to the QA server
- [ ] Virtual environment created
- [ ] `requirements.txt` installed
- [ ] Waitress installed
- [ ] Protected `.env` created
- [ ] API authentication verified
- [ ] SQL Server access verified
- [ ] TestRail access verified
- [ ] Pytest collection succeeds
- [ ] One small API test passes
- [ ] Waitress runs locally on `127.0.0.1:5050`
- [ ] `APP_BASE_PATH=/ApiAutomation` set for the Waitress service
- [ ] Windows service starts automatically
- [ ] IIS Application `ApiAutomation` created and reverse proxy returns HTTP `200`
- [ ] HTTPS binding configured
- [ ] Port `5050` is not externally exposed
- [ ] QA Main and QA-RC selectors are visible
- [ ] Reports directory is writable by the service account
- [ ] `dashboard_wsgi.py` and `utils/dashboard_scheduler.py` deployed
- [ ] Scheduler database and run-log paths are writable by the service account
- [ ] Scheduler page creates a test schedule and displays run history
