from datetime import datetime, timedelta
import threading

from utils.dashboard_scheduler import ScheduleStore, ScheduleWorker, next_occurrence

import dashboard_server


def test_next_occurrence_handles_weekly_and_short_months():
    monday = datetime(2026, 3, 2, 1, 0)
    assert next_occurrence("weekly", "02:00", now=monday, weekday=0) == datetime(2026, 3, 2, 2, 0)
    assert next_occurrence("weekly", "02:00", now=datetime(2026, 3, 2, 3, 0), weekday=0) == datetime(2026, 3, 9, 2, 0)
    assert next_occurrence("monthly", "02:00", now=datetime(2026, 2, 1), month_day=31) == datetime(2026, 2, 28, 2, 0)
    assert next_occurrence("monthly", "02:00", now=datetime(2026, 2, 28, 3, 0), month_day=31) == datetime(2026, 3, 31, 2, 0)


def test_worker_claims_one_time_schedule_once_and_writes_history(tmp_path):
    store = ScheduleStore(tmp_path / "scheduler.sqlite3")
    due_at = datetime(2026, 3, 2, 2, 0)
    schedule_id = store.create({
        "name": "One-time QA",
        "environment": "qa-main",
        "sections": ["Purpose"],
        "schedule_type": "once",
        "frequency": None,
        "run_at": "02:00",
        "weekday": None,
        "month_day": None,
        "next_run_at": due_at.isoformat(timespec="minutes"),
    })
    lock = threading.Lock()
    worker = ScheduleWorker(store, lock, lambda schedule: {
        "status": "completed", "log_file": "run.log", "summary": "Passed"
    })

    assert worker.run_once(due_at + timedelta(minutes=1))
    assert not worker.run_once(due_at + timedelta(minutes=2))
    schedule = store.list_schedules()[0]
    run = store.list_runs()[0]
    assert schedule["id"] == schedule_id
    assert not schedule["enabled"]
    assert schedule["next_run_at"] is None
    assert run["status"] == "completed"
    assert run["log_file"] == "run.log"


def test_worker_waits_when_another_run_holds_lock(tmp_path):
    store = ScheduleStore(tmp_path / "scheduler.sqlite3")
    due_at = datetime(2026, 3, 2, 2, 0)
    store.create({
        "name": "One-time QA",
        "environment": "qa-main",
        "sections": ["Purpose"],
        "schedule_type": "once",
        "frequency": None,
        "run_at": "02:00",
        "weekday": None,
        "month_day": None,
        "next_run_at": due_at.isoformat(timespec="minutes"),
    })
    lock = threading.Lock()
    lock.acquire()
    try:
        worker = ScheduleWorker(store, lock, lambda schedule: {})
        assert not worker.run_once(due_at + timedelta(minutes=1))
        assert not store.list_runs()
    finally:
        lock.release()


def test_scheduler_page_creates_one_time_schedule_and_rejects_invalid_environment(monkeypatch, tmp_path):
    store = ScheduleStore(tmp_path / "scheduler.sqlite3")
    environment_key = next(iter(dashboard_server.TEST_ENVIRONMENTS))
    monkeypatch.setattr(dashboard_server, "_get_schedule_store", lambda: store)
    monkeypatch.setattr(dashboard_server, "_test_sections", lambda: ["Purpose", "Notes"])
    run_date = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")

    with dashboard_server.app.test_client() as client:
        page = client.get("/scheduler")
        assert page.status_code == 200
        assert b"Test Schedules" in page.data
        assert b"Monthly" in page.data

        response = client.post("/scheduler/create", data={
            "name": "Scheduled smoke",
            "environment": environment_key,
            "section": "Purpose",
            "schedule_type": "once",
            "run_date": run_date,
            "run_at": "02:00",
        })
        assert response.status_code == 302

        invalid = client.post("/scheduler/create", data={
            "name": "Invalid environment",
            "environment": "unknown",
            "section": "Purpose",
            "schedule_type": "once",
            "run_date": run_date,
            "run_at": "02:00",
        })
        assert invalid.status_code == 400

    schedule = store.list_schedules()[0]
    assert schedule["name"] == "Scheduled smoke"
    assert schedule["sections"] == ["Purpose"]
    assert schedule["schedule_type"] == "once"
    assert schedule["environment"] == environment_key


def test_scheduler_page_creates_monthly_schedule_and_downloads_run_log(monkeypatch, tmp_path):
    store = ScheduleStore(tmp_path / "scheduler.sqlite3")
    log_directory = tmp_path / "scheduled_runs"
    monkeypatch.setattr(dashboard_server, "_get_schedule_store", lambda: store)
    monkeypatch.setattr(dashboard_server, "_test_sections", lambda: ["Purpose"])
    monkeypatch.setattr(dashboard_server, "SCHEDULE_LOG_DIR", log_directory)
    environment_key = next(iter(dashboard_server.TEST_ENVIRONMENTS))

    with dashboard_server.app.test_client() as client:
        response = client.post("/scheduler/create", data={
            "name": "Monthly QA",
            "environment": environment_key,
            "section": "Purpose",
            "schedule_type": "recurring",
            "frequency": "monthly",
            "month_day": "31",
            "run_at": "02:00",
        })
        assert response.status_code == 302

        schedule = store.list_schedules()[0]
        assert schedule["frequency"] == "monthly"
        assert schedule["month_day"] == 31
        assert store.set_enabled(schedule["id"], False)
        assert not store.list_schedules()[0]["enabled"]
        assert store.set_enabled(schedule["id"], True)

        due_at = datetime.now().replace(second=0, microsecond=0) - timedelta(minutes=1)
        run_schedule_id = store.create({
            "name": "One-time log test",
            "environment": environment_key,
            "sections": ["Purpose"],
            "schedule_type": "once",
            "frequency": None,
            "run_at": "02:00",
            "weekday": None,
            "month_day": None,
            "next_run_at": due_at.isoformat(timespec="minutes"),
        })
        worker = ScheduleWorker(store, threading.Lock(), lambda schedule: {
            "status": "completed", "log_file": "test-run.log", "summary": "Passed"
        })
        assert worker.run_once(datetime.now())
        run = next(item for item in store.list_runs() if item["schedule_id"] == run_schedule_id)
        log_directory.mkdir(parents=True)
        (log_directory / run["log_file"]).write_text("scheduled output", encoding="utf-8")

        download = client.get(f"/scheduler/runs/{run['id']}/log")
        assert download.status_code == 200
        assert download.data == b"scheduled output"