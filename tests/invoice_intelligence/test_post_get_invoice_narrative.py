import json

import pytest


SECTION = "Invoice Intelligence"
OPERATION = "POST /v1/invoice-intelligence/GetInvoiceNarrative"
EMPTY_MESSAGE = "No data available in [core].[InvoiceNarrative]; tests were not executed."
CASES = [
    ("17893532", "Retrieve invoice narrative using DB-derived invoice identifiers", "positive"),
    ("17893533", "aiAnalysis is a mandatory non-empty response field", "analysis"),
    ("17893534", "aiBrief is a mandatory non-empty response field", "brief"),
    ("17893535", "Response follows the invoice narrative JSON contract", "contract"),
    ("17893536", "AI Analysis text matches core.InvoiceNarrative.AIAnalysis", "analysis_db"),
    ("17893537", "AI Brief text matches core.InvoiceNarrative.AIBrief", "brief_db"),
    ("17893538", "Multiple DB invoices return their respective AI texts", "multiple"),
    ("17893539", "Repeated invoice retrieval preserves DB text consistency", "repeat"),
    ("17893545", "Reject missing currencyId", "missing_currencyId"),
    ("17893546", "Reject null currencyId", "null_currencyId"),
    ("17893547", "Reject missing providerId", "missing_providerId"),
    ("17893548", "Reject null providerId", "null_providerId"),
    ("17893549", "Reject missing invoiceDate", "missing_invoiceDate"),
    ("17893550", "Reject null invoiceDate", "null_invoiceDate"),
    ("17893551", "Reject zero currencyId", "zero_currencyId"),
    ("17893552", "Reject negative currencyId", "negative_currencyId"),
    ("17893553", "Reject zero providerId", "zero_providerId"),
    ("17893554", "Reject negative providerId", "negative_providerId"),
    ("17893555", "Reject invalid JSON type for currencyId", "type_currencyId"),
    ("17893556", "Reject invalid JSON type for providerId", "type_providerId"),
    ("17893557", "Reject invalid JSON type for invoiceDate", "type_invoiceDate"),
    ("17893558", "Reject invalid JSON type for accountNo", "type_accountNo"),
    ("17893559", "Reject invalid JSON type for billingAccount", "type_billingAccount"),
    ("17893560", "Reject impossible invoice date", "invalid_date"),
    ("17893561", "Reject empty JSON request object", "empty_body"),
    ("17893562", "Reject malformed JSON", "malformed_json"),
    ("17893563", "Missing null and empty accountNo return no narrative", "absent_accountNo"),
    ("17893564", "Missing null and empty billingAccount return no narrative", "absent_billingAccount"),
    ("17893565", "No narrative for DB-confirmed unmatched currencyId", "unmatched_currencyId"),
    ("17893566", "No narrative for DB-confirmed unmatched providerId", "unmatched_providerId"),
    ("17893567", "No narrative for DB-confirmed unmatched invoiceDate", "unmatched_invoiceDate"),
    ("17893568", "No narrative for DB-confirmed unmatched accountNo", "unmatched_accountNo"),
    ("17893569", "No narrative for DB-confirmed unmatched billingAccount", "unmatched_billingAccount"),
    ("17893570", "Reject request without Authorization", "no_auth"),
    ("17893571", "Reject invalid bearer token", "invalid_token"),
    ("17893572", "Reject unsupported GET method", "wrong_method"),
    ("17893573", "Treat SQL-like account text as literal data", "sql_text"),
    ("17893574", "Treat HTML-like account text as literal data", "html_text"),
]
CASE_PARAMS = [
    pytest.param(
        scenario,
        id=f"C{case_id}",
        marks=[
            pytest.mark.swagger_section(SECTION),
            pytest.mark.swagger_operation(OPERATION),
            pytest.mark.testrail_case(case_id),
            pytest.mark.dashboard_title(title),
        ],
    )
    for case_id, title, scenario in CASES
]


def _load_rows(db):
    if db.fetch_scalar("SELECT COUNT(*) FROM [core].[InvoiceNarrative]") == 0:
        pytest.skip(EMPTY_MESSAGE)
    rows = db.fetch_all("""
        WITH candidates AS (
             SELECT n.InvoiceNarrativeId, n.ProviderId, n.AccountNo,
                 n.BillingAccount, n.InvoiceDate, n.AIAnalysis, n.AIBrief,
                 h.CurrencyId,
                   ROW_NUMBER() OVER (
                       PARTITION BY n.ProviderId, n.AccountNo, n.BillingAccount,
                                    n.InvoiceDate, h.CurrencyId
                       ORDER BY n.InvoiceNarrativeId DESC
                   ) AS CandidateRank
            FROM [core].[InvoiceNarrative] n
            INNER JOIN [core].[BillingAccountInvoiceHeader] h
                ON h.BillingAccountInvoiceHeaderId = n.BillingAccountInvoiceHeaderId
            WHERE h.CurrencyId IS NOT NULL
        )
        SELECT TOP 3 * FROM candidates
        WHERE CandidateRank = 1
        ORDER BY InvoiceNarrativeId DESC
    """)
    assert rows, "InvoiceNarrative contains data but no invoice has a usable header/currency mapping."
    return rows


def _payload(row):
    return {
        "currencyId": row["CurrencyId"],
        "providerId": row["ProviderId"],
        "invoiceDate": row["InvoiceDate"].isoformat(),
        "accountNo": row["AccountNo"],
        "billingAccount": row["BillingAccount"],
    }


def preview_seed_compatibility(db, *, provider_id=None, service_type_id=None, limit=2):
    if service_type_id is not None:
        raise ValueError("Invoice narrative does not use a service type filter.")
    if db.fetch_scalar("SELECT COUNT(*) FROM [core].[InvoiceNarrative]") == 0:
        return []
    rows = db.fetch_all("""
        SELECT TOP (?) n.InvoiceNarrativeId, n.ProviderId, n.ProviderName,
            n.AccountNo, n.BillingAccount, n.InvoiceDate, n.AIAnalysis, n.AIBrief,
            h.CurrencyId
        FROM [core].[InvoiceNarrative] n
        INNER JOIN [core].[BillingAccountInvoiceHeader] h
            ON h.BillingAccountInvoiceHeaderId = n.BillingAccountInvoiceHeaderId
        WHERE h.CurrencyId IS NOT NULL AND (? IS NULL OR n.ProviderId = ?)
        ORDER BY n.InvoiceNarrativeId DESC
    """, (max(1, min(int(limit), 10)), provider_id, provider_id))
    return [{
        **_payload(row),
        "invoiceNarrativeId": row["InvoiceNarrativeId"],
        "providerName": row["ProviderName"],
        "hasAIAnalysis": bool(str(row["AIAnalysis"] or "").strip()),
        "hasAIBrief": bool(str(row["AIBrief"] or "").strip()),
    } for row in rows]


def _text(value):
    return " ".join(value.split())


def _record_output(request, messages):
    properties = request.node.user_properties
    properties[:] = [(key, value) for key, value in properties if key != "dashboard_metrics"]
    properties.append(("dashboard_metrics", "\n\n".join(messages)))


def _record_response(request, messages, response, payload):
    messages.append(
        f"Request: {OPERATION}\n"
        f"Request body:\n{json.dumps(payload, indent=2, default=str)}\n"
        f"Response status: HTTP {response.status}\n"
        f"Response body:\n{response.text() or '<empty>'}"
    )
    _record_output(request, messages)


def _negative_payload(scenario, row):
    payload = _payload(row)
    kind, separator, field = scenario.partition("_")
    if kind == "missing":
        payload.pop(field)
    elif kind == "null":
        payload[field] = None
    elif kind == "zero":
        payload[field] = 0
    elif kind == "negative":
        payload[field] = -1
    elif kind == "type":
        payload[field] = "not-an-integer" if field in ("providerId", "currencyId") else []
    elif kind == "unmatched":
        payload[field] = {
            "providerId": 2147483647,
            "currencyId": 2147483647,
            "invoiceDate": "2099-01-01",
            "accountNo": "__IIT02_UNMATCHED_ACCOUNT__",
            "billingAccount": "__IIT02_UNMATCHED_BILLING__",
        }[field]
    elif scenario == "invalid_date":
        payload["invoiceDate"] = "2024-02-30"
    elif scenario == "empty_body":
        payload = {}
    elif scenario == "sql_text":
        payload["accountNo"] = "invoice-probe' OR '1'='1"
    elif scenario == "html_text":
        payload["accountNo"] = "<script>invoice_probe</script>"
    return payload


def _matching_count(db, payload):
    return db.fetch_scalar("""
        SELECT COUNT(*) FROM [core].[InvoiceNarrative] n
        INNER JOIN [core].[BillingAccountInvoiceHeader] h
            ON h.BillingAccountInvoiceHeaderId = n.BillingAccountInvoiceHeaderId
        WHERE h.CurrencyId = ? AND n.ProviderId = ?
          AND n.InvoiceDate = CONVERT(date, ?)
          AND n.AccountNo = ? AND n.BillingAccount = ?
    """, tuple(payload.get(field) for field in (
        "currencyId", "providerId", "invoiceDate", "accountNo", "billingAccount",
    )))


def _run_negative(scenario, row, context, db, request):
    payload = _negative_payload(scenario, row)
    messages = []
    variants = [payload]
    if scenario.startswith("absent_"):
        field = scenario.removeprefix("absent_")
        variants = []
        for value in (None, ""):
            variants.append({**payload, field: value})
        omitted = dict(payload)
        omitted.pop(field)
        variants.append(omitted)
    no_match = scenario.startswith(("unmatched_", "absent_")) or scenario in ("sql_text", "html_text")
    for variant in variants:
        if no_match:
            count = _matching_count(db, variant)
            messages.append(f"DB compatibility check: matching invoice records={count}.")
            _record_output(request, messages)
            assert count == 0, "Negative seed unexpectedly matches an invoice; choose a different nonmatching value."
        kwargs = {}
        expected_status = 204 if no_match else 400
        request_body = variant
        if scenario == "no_auth":
            kwargs["headers"] = {"Authorization": ""}
            expected_status = 401
        elif scenario == "invalid_token":
            kwargs["headers"] = {"Authorization": "Bearer invoice-automation-invalid-token"}
            expected_status = 401
        elif scenario == "malformed_json":
            request_body = '{"currencyId":'
            kwargs["headers"] = {"Content-Type": "application/json"}
        if scenario == "wrong_method":
            response = context.get("invoice-intelligence/GetInvoiceNarrative")
            expected_status = 405
            messages.append("Request method: GET (POST-only endpoint).")
            request_body = None
        else:
            response = context.post("invoice-intelligence/GetInvoiceNarrative", data=request_body, **kwargs)
        _record_response(request, messages, response, request_body)
        assert response.status == expected_status, f"Expected HTTP {expected_status}, received {response.status}: {response.text()}"
        if expected_status in (204, 401):
            assert not response.text().strip(), "Expected an empty response with no invoice narrative."
        elif expected_status == 400:
            error = response.json()
            assert isinstance(error, dict) and (error.get("errors") or error.get("Message")), "Expected validation error details."
            assert "aiAnalysis" not in error and "aiBrief" not in error, "Error response disclosed invoice narrative."
        else:
            assert not response.text().strip(), "Unsupported method must not return invoice data."
        messages.append(f"Expected negative/security outcome matched: HTTP {expected_status}; no invoice narrative disclosed.")
        _record_output(request, messages)


def _assert_narrative(response, row):
    assert response.status == 200, f"{OPERATION}: HTTP {response.status}: {response.text()}"
    assert "application/json" in response.headers.get("content-type", "").lower()
    result = response.json()
    assert isinstance(result, dict), "Expected a JSON response object."
    for field, column, label in [
        ("aiAnalysis", "AIAnalysis", "AI Analysis"),
        ("aiBrief", "AIBrief", "AI Brief"),
    ]:
        assert field in result, f"Mandatory response field {field} is missing."
        value = result[field]
        assert isinstance(value, str) and value.strip(), f"{field} must contain non-empty text."
        expected = row[column]
        assert isinstance(expected, str), f"DB column {column} must contain text."
        assert _text(value) == _text(expected), (
            f"{label} text does not match DB column {column} for InvoiceNarrativeId={row['InvoiceNarrativeId']}."
        )
        print(f"{label} matched with DB ({column}); InvoiceNarrativeId={row['InvoiceNarrativeId']}.")
    return result


@pytest.fixture(scope="module")
def invoice_rows(db):
    return _load_rows(db)


@pytest.fixture(scope="module")
def narrative_context(invoice_rows, request):
    return request.getfixturevalue("api_context")


@pytest.mark.parametrize("scenario", CASE_PARAMS)
def test_get_invoice_narrative(scenario, invoice_rows, narrative_context, request, db):
    if scenario not in {"positive", "analysis", "brief", "contract", "analysis_db", "brief_db", "multiple", "repeat"}:
        _run_negative(scenario, invoice_rows[0], narrative_context, db, request)
        return
    rows = invoice_rows if scenario == "multiple" else invoice_rows[:1]
    messages = []
    for row in rows:
        previous = None
        for execution in range(2 if scenario == "repeat" else 1):
            print(f"DB-derived invoice request: {_payload(row)}; execution={execution + 1}.")
            response = narrative_context.post("invoice-intelligence/GetInvoiceNarrative", data=_payload(row))
            _record_response(request, messages, response, _payload(row))
            result = _assert_narrative(response, row)
            normalized = {field: _text(result[field]) for field in ("aiAnalysis", "aiBrief")}
            if previous is not None:
                assert normalized == previous, "Repeated retrieval changed the AI narrative text."
            previous = normalized
            messages.append(
                f"InvoiceNarrativeId={row['InvoiceNarrativeId']}; execution={execution + 1}: "
                "AI Analysis matched with DB (AIAnalysis); AI Brief matched with DB (AIBrief)."
            )
            _record_output(request, messages)