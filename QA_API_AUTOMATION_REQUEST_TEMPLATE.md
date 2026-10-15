# QA API Automation Request Template

Use this template when asking GitHub Copilot Chat in VS Code to develop or extend API automation in this project. Fill in the request below, attach the supporting files, and submit it in chat. Keep this file as the shared blank template; put task-specific answers in chat or the work ticket.

The six inputs below are normally enough to begin. Use `N/A` when an item does not apply and `Unknown` when it needs investigation. Copilot should review the supplied material and existing project code before asking for additional details. Ask only about missing information that blocks implementation or safe execution.

## Request To Submit

```text
Develop API automation in this existing project using the workflow and completion
criteria in QA_API_AUTOMATION_REQUEST_TEMPLATE.md.

1. API link
   Swagger/OpenAPI link:
   API method and endpoint/action:
   Known-good sample request/response, if available (remove secrets):

2. TestRail destination
   Project > Suite > Section path or section URL to load cases:
   Existing case IDs to reuse, if applicable:

3. Jira ticket
   Attach the Word export of the Jira ticket, including acceptance criteria.
   Ticket ID/link:
   Attached file:

4. Confluence documentation
   Attach the Confluence document/export or provide an accessible link.
   Attached file/link:

5. Existing manual TestRail cases (REFERENCE ONLY)
   Attach an export if available, otherwise enter N/A.
   Attached file:

6. Database validation
   Relevant tables and relationships:
   SQL queries:
   Explain what each query checks and the expected result:
   Expected DB changes for successful and rejected requests:

Execution details (only if not already clear from the supplied material/config):
   Approved QA environment:
   Target TestRail test plan/run link, or request to create a new one:
   Execution/data restrictions, if any:

Proceed with review, case generation, TestRail creation/update in the destination
above, automation linked to real case IDs, scoped execution, and result publishing
to the approved TestRail plan/run. Ask before creating a new plan/run if the
destination is unclear. Do not run against production or clean up data without
explicit approval. Do not commit, push, or merge changes unless requested.
```

## Standard Copilot Workflow

1. **Review the inputs and existing code.** Read the Jira acceptance criteria, Confluence documentation, Swagger contract, reference cases, and DB explanations. Verify the actual method/path using Swagger where available. Identify coverage gaps, conflicting expectations, and missing execution prerequisites. Never request credentials in chat; use approved local configuration. If a link or attachment cannot be read, request an accessible export rather than inventing its contents.
2. **Generate the case matrix.** Cover positive, negative/validation, boundary, authorization/security, and DB validation scenarios as applicable. Include expected status, response fields/messages, and DB outcomes. Treat attached manual cases as reference, not a limit on coverage. Reuse suitable existing TestRail cases and identify duplicates before adding cases. Do not silently replace requirement expectations with observed API behavior; report discrepancies for a decision.
3. **Create/import and link TestRail cases.** Use the existing TestRail client and the supplied project/suite/section. Create or update only cases within the approved scope, preserve unrelated cases, and obtain real case IDs before mapping automation. Use existing project field/label conventions. If TestRail is unavailable, report the blocker and do not fabricate IDs or claim cases were imported.
4. **Generate automation.** Follow the nearest applicable test module and reuse shared authentication, DB fixtures, and helpers. Use the existing case-matrix pattern where appropriate and the `swagger_section`, `swagger_operation`, `testrail_case`, and `dashboard_title` markers. Validate status and response content, not only HTTP success. For persistence flows, check relevant before/after row counts and stored values using existing helpers such as `PostApiDbScripts` where appropriate.
5. **Integrate the dashboard.** Register the action where required and preserve existing grouping, case links, run controls, and reporting. Add a read-only `preview_seed_compatibility` function for DB-backed actions where the dashboard supports it. Preserve provider/service-type filter support for relevant Workorder/Self-Service actions. Keep execution counts distinct from unique TestRail case counts.
6. **Validate and execute.** Run focused local checks first, then the requested API cases in the approved QA environment. Select compatible data and avoid records already pending or changed by other runs. Coordinate shared test data across QAs; different Git branches do not isolate the database. Missing compatible data must be reported explicitly, not presented as a pass. Keep cleanup manual, opt-in, and limited to explicitly approved records; never add automatic cleanup to pytest execution.
7. **Publish to TestRail.** Execute through pytest/the dashboard and publish mapped results to the appropriate run within the approved TestRail plan, or the explicitly supplied standalone run. A plan contains runs; do not treat a plan ID as a run ID. Confirm that every in-scope case has an execution or a documented reason it was not executed. Publish actual outcomes using supported TestRail statuses and include useful evidence. Mark automation status only after implementation and verification, following existing project conventions.
8. **Hand over.** Summarize changed files, case IDs and TestRail links, commands executed, pass/fail/skip counts, requirement discrepancies, blockers, and data created or left behind. Distinguish completed local work from failed or unperformed TestRail updates. Supply a concise PR-ready summary; do not commit, push, or merge automatically.

## Required Execution Evidence

Capture evidence before assertions so failed tests retain the same diagnostic context as successful tests. Include it in dashboard results and in TestRail result comments or supported attachments as appropriate:

- TestRail case ID/title, API method/path, and QA environment.
- Sanitized actual request parameters/body and relevant non-secret headers.
- Actual HTTP status and full response body after sensitive-data redaction, including non-JSON and empty responses.
- Expected versus actual response checks and a clear assertion failure reason.
- DB validation performed: query/check description, relevant sanitized before/after values or counts, expected result, and pass/fail outcome. Explicitly identify DB checks that are not applicable or could not run.
- Final case outcome and useful execution/report references.

Never include passwords, tokens, authorization headers, connection strings, or unredacted sensitive personal data in chat, logs, reports, or TestRail.

## Team And Completion Rules

- Work on the assigned task branch and keep changes narrowly scoped. Preserve other people's work and coordinate changes to shared files such as `conftest.py`, `dashboard_server.py`, and shared utilities.
- Keep populated `.env`, virtual environments, generated reports, scheduler databases, and local artifacts out of commits. Only use sanitized configuration templates in source control.
- Completion means the agreed coverage is implemented, real TestRail case mappings exist, dashboard integration works, scoped checks/execution are reported, and results are published to the approved destination. Clearly list exceptions instead of declaring blocked or unverified work complete.

## Important Scope Note

This is a request template and workflow reference, not an automatically applied Copilot instructions file. QAs must attach/reference this file in their request or use the request block above. It does not by itself configure GitHub branch protection, CI gates, or TestRail credentials.