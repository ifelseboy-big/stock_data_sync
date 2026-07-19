from app.scheduler.factory import create_scheduler


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
    assert scheduler.get_job("plan-etf-share-size") is not None
    assert scheduler.get_job("plan-next-year-trade-calendar") is not None
    assert scheduler.get_job("plan-daily-preopen") is not None
    assert scheduler.get_job("plan-daily-close") is not None
    assert scheduler.get_job("plan-daily-late") is not None
    assert scheduler.get_job("plan-daily-final") is not None


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
