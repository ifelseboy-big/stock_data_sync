from app.scheduler.catalog import SCHEDULED_JOB_BY_ID
from app.scheduler.factory import create_scheduler
from app.scheduler.jobs import registered_job_functions


def test_scheduler_registers_dispatch_job() -> None:
    scheduler = create_scheduler()

    job = scheduler.get_job("dispatch-collection-tasks")

    assert job is not None
    assert job.name == "派发采集任务"


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


def test_scheduler_registers_partition_job() -> None:
    scheduler = create_scheduler()

    job = scheduler.get_job("ensure-future-partitions")

    assert job is not None
    assert job.name == "检查未来月份分区"
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
