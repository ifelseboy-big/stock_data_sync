from app.scheduler.factory import create_scheduler


def test_scheduler_registers_dispatch_job() -> None:
    scheduler = create_scheduler()

    job = scheduler.get_job("dispatch-due-tasks")

    assert job is not None
    assert job.name == "扫描待执行任务"
