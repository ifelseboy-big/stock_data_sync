import pytest

from app.scheduler.catalog import SCHEDULED_JOB_BY_ID
from app.scheduler.factory import create_scheduler
from app.scheduler.jobs import registered_job_functions


def test_scheduler_registers_dispatch_job() -> None:
    scheduler = create_scheduler()

    job = scheduler.get_job("dispatch-collection-tasks")

    assert job is not None
    assert job.name == SCHEDULED_JOB_BY_ID["dispatch-collection-tasks"].name


def test_scheduler_registers_collection_coordination_jobs() -> None:
    scheduler = create_scheduler()

    assert scheduler.get_job("close-collection-batches") is not None
    assert scheduler.get_job("reconcile-collection-runtime") is not None
    assert scheduler.get_job("plan-trade-calendar") is not None
    assert scheduler.get_job("plan-stock-master") is not None
    assert scheduler.get_job("plan-etf-master") is not None
    assert scheduler.get_job("plan-special-master") is not None
    assert scheduler.get_job("plan-concept-board-members") is not None
    assert scheduler.get_job("plan-monthly-index-weights") is not None
    assert scheduler.get_job("plan-etf-share-size") is not None
    assert scheduler.get_job("plan-next-year-trade-calendar") is not None
    assert scheduler.get_job("plan-daily-preopen") is not None
    assert scheduler.get_job("plan-daily-close") is not None
    assert scheduler.get_job("plan-daily-late") is not None
    assert scheduler.get_job("plan-daily-final") is not None
    assert scheduler.get_job("plan-theme-members") is not None
    assert scheduler.get_job("plan-hot-rank") is not None


@pytest.mark.parametrize(
    ("job_id", "expected_trigger"),
    (
        ("plan-trade-calendar", "cron[hour='8', minute='20']"),
        ("plan-stock-master", "cron[hour='8', minute='30']"),
        ("plan-etf-master", "cron[hour='8', minute='35']"),
        ("plan-special-master", "cron[hour='8', minute='40']"),
        ("plan-concept-board-members", "cron[hour='10']"),
    ),
)
def test_master_jobs_run_daily(job_id: str, expected_trigger: str) -> None:
    scheduler = create_scheduler()

    job = scheduler.get_job(job_id)

    assert job is not None
    assert str(job.trigger) == expected_trigger
    assert SCHEDULED_JOB_BY_ID[job_id].schedule.startswith("每日 ")


def test_intrinsically_monthly_jobs_keep_their_existing_schedule() -> None:
    scheduler = create_scheduler()

    index_weights = scheduler.get_job("plan-monthly-index-weights")
    next_year_calendar = scheduler.get_job("plan-next-year-trade-calendar")

    assert index_weights is not None
    assert str(index_weights.trigger) == "cron[day='2', hour='8', minute='50']"
    assert SCHEDULED_JOB_BY_ID[index_weights.id].schedule == "每月 2 日 08:50"
    assert next_year_calendar is not None
    assert str(next_year_calendar.trigger) == (
        "cron[month='10-12', day='1', hour='8', minute='25']"
    )
    assert SCHEDULED_JOB_BY_ID[next_year_calendar.id].schedule == "10–12 月每月 1 日 08:25"


def test_scheduler_registers_partition_job() -> None:
    scheduler = create_scheduler()

    job = scheduler.get_job("ensure-future-partitions")

    assert job is not None
    assert job.name == SCHEDULED_JOB_BY_ID["ensure-future-partitions"].name
    assert str(job.trigger) == "cron[hour='8', minute='30']"


def test_scheduler_registers_global_processing_jobs() -> None:
    scheduler = create_scheduler()

    assert scheduler.get_job("plan-processing-tasks") is not None
    assert scheduler.get_job("dispatch-processing-task") is not None
    assert scheduler.get_job("reconcile-processing-runtime") is not None


def test_scheduler_catalog_registry_and_visible_jobs_stay_in_sync() -> None:
    scheduler = create_scheduler()
    visible_job_ids = {
        job.id for job in scheduler.get_jobs() if job.id != "dispatch-manual-scheduled-jobs"
    }

    assert visible_job_ids == set(SCHEDULED_JOB_BY_ID)
    assert set(registered_job_functions()) == set(SCHEDULED_JOB_BY_ID)
    assert all(definition.description for definition in SCHEDULED_JOB_BY_ID.values())
