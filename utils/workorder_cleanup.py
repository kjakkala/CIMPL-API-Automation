"""DB cleanup helper for cancelling test-created workorders (dashboard 'Cancel Work Orders' tab).

Mirrors the manual SQL script provided by QA: for each workorder number, replay its pending
transaction(s) via cimpl.ExecuteTransactionInsert, then mark the WorkorderSummary as cancelled
(WorkorderStatusId=6). Only touches workorders that are NOT already in a terminal status (3, 6).
"""

from __future__ import annotations

TERMINAL_WORKORDER_STATUS_IDS = (3, 6)
CANCELLED_WORKORDER_STATUS_ID = 6


def parse_workorder_numbers(raw: str) -> list[str]:
    numbers = []
    for chunk in raw.replace("\r", "\n").replace(",", "\n").split("\n"):
        value = chunk.strip()
        if value:
            numbers.append(value)
    # de-duplicate while preserving order
    seen = set()
    unique = []
    for number in numbers:
        if number not in seen:
            seen.add(number)
            unique.append(number)
    return unique


def cancel_workorders(db, workorder_numbers: list[str]) -> list[dict]:
    results = []
    for number in workorder_numbers:
        try:
            results.append(_cancel_one(db, number))
        except Exception as exc:  # noqa: BLE001
            results.append({"number": number, "status": "error", "message": str(exc)})
    return results


def _cancel_one(db, number: str) -> dict:
    workorder_row = db.fetch_one(
        """SELECT TOP 1 wo.WorkorderId AS workorder_id, wo.TenantId AS tenant_id, ws.WorkorderStatusId AS status_id
        FROM cimpl.Workorder wo
        INNER JOIN cimpl.WorkorderSummary ws ON ws.WorkorderId = wo.WorkorderId
        WHERE wo.Number = ?""",
        (number,),
    )
    if not workorder_row:
        return {"number": number, "status": "not_found", "message": "No workorder found with this number."}

    if int(workorder_row["status_id"]) in TERMINAL_WORKORDER_STATUS_IDS:
        return {"number": number, "status": "skipped", "message": f"Workorder is already in a terminal status ({workorder_row['status_id']})."}

    workorder_id = int(workorder_row["workorder_id"])
    tenant_id = int(workorder_row["tenant_id"])

    transaction_rows = db.fetch_all(
        "SELECT TransactId AS transact_id FROM cimpl.TransactWorkorderService WHERE WorkorderId = ? AND TransactId IS NOT NULL",
        (workorder_id,),
    )
    for row in transaction_rows:
        db.execute("EXEC cimpl.ExecuteTransactionInsert ?, ?", (tenant_id, int(row["transact_id"])))

    db.execute(
        "UPDATE ws SET ws.WorkorderStatusId = ? FROM cimpl.WorkorderSummary AS ws WHERE ws.WorkorderId = ?",
        (CANCELLED_WORKORDER_STATUS_ID, workorder_id),
    )

    return {
        "number": number,
        "status": "cancelled",
        "message": f"Replayed {len(transaction_rows)} transaction(s) and marked the workorder cancelled.",
    }
