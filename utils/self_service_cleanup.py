"""DB cleanup helper for cancelling test-created Self Service requests (dashboard tab).

Only cancels rows the user explicitly names by Number (e.g. SR2026-003067), and only if the
request is still Pending (RequestStatusId=1 in cimpl.RequestStatus). This is a manual, opt-in
cleanup action - it must never be wired into the normal pytest "Run" flow.
"""

from __future__ import annotations

PENDING_STATUS_ID = 1
CANCEL_STATUS_ID = 2


def parse_request_numbers(raw: str) -> list[str]:
    numbers = []
    for chunk in raw.replace("\r", "\n").replace(",", "\n").split("\n"):
        value = chunk.strip()
        if value:
            numbers.append(value)
    seen = set()
    unique = []
    for number in numbers:
        if number not in seen:
            seen.add(number)
            unique.append(number)
    return unique


def cancel_self_service_requests(db, request_numbers: list[str]) -> list[dict]:
    results = []
    for number in request_numbers:
        try:
            results.append(_cancel_one(db, number))
        except Exception as exc:  # noqa: BLE001
            results.append({"number": number, "status": "error", "message": str(exc)})
    return results


def _cancel_one(db, number: str) -> dict:
    row = db.fetch_one(
        "SELECT TOP 1 SelfServiceActionLogId, StatusId FROM cimpl.SelfServiceActionLog WHERE Number = ?",
        (number,),
    )
    if not row:
        return {"number": number, "status": "not_found", "message": "No self service request found with this number."}

    if int(row["StatusId"]) != PENDING_STATUS_ID:
        return {"number": number, "status": "skipped", "message": f"Request is not Pending (StatusId={row['StatusId']}); leaving it as-is."}

    db.execute(
        "UPDATE cimpl.SelfServiceActionLog SET StatusId = ? WHERE SelfServiceActionLogId = ?",
        (CANCEL_STATUS_ID, int(row["SelfServiceActionLogId"])),
    )
    return {"number": number, "status": "cancelled", "message": "Marked as Cancel (StatusId=2)."}
