from __future__ import annotations

import calendar
import json
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path


def _local_now() -> datetime:
    return datetime.now().replace(second=0, microsecond=0)


def _stamp(value: datetime) -> str:
    return value.isoformat(timespec="minutes")


def next_occurrence(
    frequency: str,
    run_time: str,
    *,
    now: datetime | None = None,
    weekday: int | None = None,
    month_day: int | None = None,
) -> datetime:
    current = now or _local_now()
    hour, minute = (int(part) for part in run_time.split(":"))
    candidate = current.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if frequency == "weekly":
        if weekday is None or not 0 <= weekday <= 6:
            raise ValueError("Choose a weekday for a weekly schedule.")
        candidate += timedelta(days=(weekday - candidate.weekday()) % 7)
        if candidate <= current:
            candidate += timedelta(days=7)
        return candidate

    if frequency == "monthly":
        if month_day is None or not 1 <= month_day <= 31:
            raise ValueError("Choose a day from 1 to 31 for a monthly schedule.")
        year, month = current.year, current.month
        while True:
            actual_day = min(month_day, calendar.monthrange(year, month)[1])
            candidate = current.replace(year=year, month=month, day=actual_day, hour=hour, minute=minute, second=0, microsecond=0)
            if candidate > current:
                return candidate
            if month == 12:
                year, month = year + 1, 1
            else:
                month += 1

    raise ValueError("Choose weekly or monthly recurrence.")


class ScheduleStore:
    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def _connect(self):
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schedules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    environment TEXT NOT NULL,
                    sections_json TEXT NOT NULL,
                    schedule_type TEXT NOT NULL,
                    frequency TEXT,
                    run_at TEXT,
                    weekday INTEGER,
                    month_day INTEGER,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    next_run_at TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS schedule_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    schedule_id INTEGER NOT NULL,
                    schedule_name TEXT NOT NULL,
                    environment TEXT NOT NULL,
                    sections_json TEXT NOT NULL,
                    scheduled_for TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT NOT NULL,
                    log_file TEXT,
                    summary TEXT NOT NULL DEFAULT ''
                );
                """
            )

    def mark_abandoned_runs(self) -> None:
        now = _stamp(_local_now())
        with self._connect() as connection:
            connection.execute(
                "UPDATE schedule_runs SET status = 'interrupted', finished_at = ?, "
                "summary = 'Dashboard service restarted while this scheduled run was active.' "
                "WHERE status = 'running'",
                (now,),
            )

    @staticmethod
    def _schedule_dict(row: sqlite3.Row) -> dict:
        result = dict(row)
        result["sections"] = json.loads(result.pop("sections_json"))
        result["enabled"] = bool(result["enabled"])
        return result

    def create(self, schedule: dict) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT INTO schedules
                (name, environment, sections_json, schedule_type, frequency, run_at,
                 weekday, month_day, enabled, next_run_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                (
                    schedule["name"],
                    schedule["environment"],
                    json.dumps(schedule["sections"]),
                    schedule["schedule_type"],
                    schedule.get("frequency"),
                    schedule.get("run_at"),
                    schedule.get("weekday"),
                    schedule.get("month_day"),
                    schedule["next_run_at"],
                    _stamp(_local_now()),
                ),
            )
            return int(cursor.lastrowid)

    def list_schedules(self) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM schedules ORDER BY enabled DESC, next_run_at, id"
            ).fetchall()
        return [self._schedule_dict(row) for row in rows]

    def list_runs(self, limit: int = 100) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM schedule_runs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        results = []
        for row in rows:
            result = dict(row)
            result["sections"] = json.loads(result.pop("sections_json"))
            results.append(result)
        return results

    def get_run(self, run_id: int) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM schedule_runs WHERE id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["sections"] = json.loads(result.pop("sections_json"))
        return result

    def set_enabled(self, schedule_id: int, enabled: bool) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM schedules WHERE id = ?", (schedule_id,)
            ).fetchone()
            if row is None:
                return False
            next_run_at = row["next_run_at"]
            if enabled and row["schedule_type"] == "once" and not next_run_at:
                return False
            if enabled and row["schedule_type"] == "recurring":
                next_run = next_occurrence(
                    row["frequency"],
                    row["run_at"],
                    weekday=row["weekday"],
                    month_day=row["month_day"],
                )
                next_run_at = _stamp(next_run)
            connection.execute(
                "UPDATE schedules SET enabled = ?, next_run_at = ? WHERE id = ?",
                (int(enabled), next_run_at, schedule_id),
            )
        return True

    def delete(self, schedule_id: int) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))
        return cursor.rowcount > 0

    def has_enabled_schedules(self) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM schedules WHERE enabled = 1 LIMIT 1"
            ).fetchone()
        return row is not None

    def claim_due(self, now: datetime | None = None) -> dict | None:
        current = now or _local_now()
        current_stamp = _stamp(current)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM schedules WHERE enabled = 1 AND next_run_at <= ? "
                "ORDER BY next_run_at, id LIMIT 1",
                (current_stamp,),
            ).fetchone()
            if row is None:
                connection.commit()
                return None

            schedule = self._schedule_dict(row)
            scheduled_for = row["next_run_at"]
            if schedule["schedule_type"] == "recurring":
                next_run_at = _stamp(
                    next_occurrence(
                        schedule["frequency"],
                        schedule["run_at"],
                        now=current,
                        weekday=schedule["weekday"],
                        month_day=schedule["month_day"],
                    )
                )
                connection.execute(
                    "UPDATE schedules SET next_run_at = ? WHERE id = ?",
                    (next_run_at, schedule["id"]),
                )
            else:
                connection.execute(
                    "UPDATE schedules SET enabled = 0, next_run_at = NULL WHERE id = ?",
                    (schedule["id"],),
                )

            cursor = connection.execute(
                """INSERT INTO schedule_runs
                (schedule_id, schedule_name, environment, sections_json, scheduled_for,
                 started_at, status)
                VALUES (?, ?, ?, ?, ?, ?, 'running')""",
                (
                    schedule["id"],
                    schedule["name"],
                    schedule["environment"],
                    json.dumps(schedule["sections"]),
                    scheduled_for,
                    current_stamp,
                ),
            )
            schedule["run_id"] = int(cursor.lastrowid)
            schedule["scheduled_for"] = scheduled_for
            connection.commit()
        return schedule

    def finish_run(self, run_id: int, status: str, log_file: str, summary: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE schedule_runs
                SET finished_at = ?, status = ?, log_file = ?, summary = ? WHERE id = ?""",
                (_stamp(_local_now()), status, log_file, summary, run_id),
            )


class ScheduleWorker:
    def __init__(self, store: ScheduleStore, run_lock, executor, poll_seconds: int = 10):
        self.store = store
        self.run_lock = run_lock
        self.executor = executor
        self.poll_seconds = poll_seconds
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def run_once(self, now: datetime | None = None) -> bool:
        if not self.run_lock.acquire(blocking=False):
            return False
        try:
            schedule = self.store.claim_due(now)
            if schedule is None:
                return False
            try:
                result = self.executor(schedule)
            except Exception as exc:  # noqa: BLE001
                result = {
                    "status": "failed",
                    "log_file": "",
                    "summary": f"Scheduled runner failed: {exc}",
                }
            self.store.finish_run(
                schedule["run_id"],
                result.get("status", "failed"),
                result.get("log_file", ""),
                result.get("summary", ""),
            )
            return True
        finally:
            self.run_lock.release()

    def start(self) -> threading.Thread:
        if self.thread and self.thread.is_alive():
            return self.thread
        self.store.mark_abandoned_runs()
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, name="dashboard-scheduler", daemon=True)
        self.thread.start()
        return self.thread

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=self.poll_seconds + 1)

    def _run(self) -> None:
        while not self.stop_event.wait(self.poll_seconds):
            self.run_once()