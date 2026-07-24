import json
import subprocess
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.core.config import Settings
from app.integrations.lark import (
    DataSyncAlert,
    LarkCliNotifier,
    LarkNotificationError,
)
from app.scheduler import data_sync_alerts
from app.scheduler.data_sync_alerts import REGULAR_DATA_SYNC_JOB_IDS


class FakeSession:
    def __init__(self, scalar_results: list[tuple[Any, ...]]) -> None:
        self._scalar_results = iter(scalar_results)

    def __enter__(self) -> "FakeSession":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def scalars(self, _: object) -> tuple[Any, ...]:
        return next(self._scalar_results)


def test_previous_day_report_accepts_a_fully_successful_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 7, 24, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    executions = tuple(
        SimpleNamespace(
            job_id=job_id,
            status="SUCCESS",
            error_message=None,
            created_at=now,
            execution_id=uuid4(),
        )
        for job_id in REGULAR_DATA_SYNC_JOB_IDS
    )
    batch_id = uuid4()
    batch = SimpleNamespace(
        batch_id=batch_id,
        batch_type="DAILY",
        status="CLOSED",
        processing_plan_version="1",
    )
    collection_task = SimpleNamespace(
        api_name="daily",
        scope_key="20260723",
        status="SUCCESS",
    )
    processing_task = SimpleNamespace(
        output_dataset="stock_daily.core",
        status="SUCCESS",
    )
    fake_session = FakeSession(
        [
            (),
            executions,
            (batch,),
            (collection_task,),
            (processing_task,),
        ]
    )
    monkeypatch.setattr(data_sync_alerts, "SyncSessionFactory", lambda: fake_session)

    report = data_sync_alerts.build_previous_day_data_sync_alert(now=now)

    assert report.successful is True
    assert report.business_date.isoformat() == "2026-07-23"
    assert report.scheduler_issues == ()
    assert report.batch_statuses == {"CLOSED": 1}


def test_previous_day_report_flags_missing_jobs_and_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 7, 24, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    fake_session = FakeSession([(), (), ()])
    monkeypatch.setattr(data_sync_alerts, "SyncSessionFactory", lambda: fake_session)

    report = data_sync_alerts.build_previous_day_data_sync_alert(now=now)

    assert report.successful is False
    assert len(report.scheduler_issues) == len(REGULAR_DATA_SYNC_JOB_IDS)
    assert report.issue_details == ("前一日没有生成任何生产采集批次",)


def test_lark_cli_notifier_sends_a_bot_direct_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "ok": True,
                    "identity": "bot",
                    "data": {"message_id": "om_test"},
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    notifier = LarkCliNotifier(
        Settings(
            app_name="Stock Sync",
            lark_alert_enabled=True,
            lark_cli_path="/opt/lark-cli",
            lark_cli_node_path="/opt/node/bin/node",
            lark_alert_recipient_open_id="ou_recipient",
            lark_alert_send_as="bot",
        )
    )
    alert = DataSyncAlert(
        business_date=datetime(2026, 7, 23).date(),
        checked_at=datetime(2026, 7, 24, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        scheduler_issues=("plan-daily-final 状态为 FAILED",),
        batch_statuses={"CLOSED": 1},
        collection_statuses={"FAILED": 1, "SUCCESS": 10},
        processing_statuses={"SUCCESS": 3},
        issue_details=("采集 daily/20260723 状态为 FAILED",),
    )

    message_id = notifier.send_data_sync_alert(alert)

    command = captured["command"]
    assert message_id == "om_test"
    assert command[:3] == ["/opt/lark-cli", "im", "+messages-send"]
    assert command[command.index("--as") + 1] == "bot"
    assert command[command.index("--user-id") + 1] == "ou_recipient"
    assert command[command.index("--idempotency-key") + 1] == "stock-sync-alert-20260723"
    assert "前一日数据同步未全部成功" in command[command.index("--text") + 1]
    assert captured["kwargs"]["timeout"] == 30
    assert captured["kwargs"]["env"]["PATH"].startswith("/opt/node/bin:")


def test_lark_cli_notifier_rejects_a_failed_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            1,
            stdout="",
            stderr=json.dumps(
                {
                    "ok": False,
                    "error": {"message": "bot cannot reach recipient"},
                }
            ),
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    notifier = LarkCliNotifier(
        Settings(
            lark_alert_enabled=True,
            lark_alert_recipient_open_id="ou_recipient",
        )
    )
    alert = DataSyncAlert(
        business_date=datetime(2026, 7, 23).date(),
        checked_at=datetime(2026, 7, 24, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        scheduler_issues=("missing",),
        batch_statuses={},
        collection_statuses={},
        processing_statuses={},
        issue_details=(),
    )

    with pytest.raises(LarkNotificationError, match="bot cannot reach recipient"):
        notifier.send_data_sync_alert(alert)


def test_lark_alert_configuration_requires_a_recipient() -> None:
    with pytest.raises(ValueError, match="LARK_ALERT_RECIPIENT_OPEN_ID"):
        Settings(lark_alert_enabled=True, lark_alert_recipient_open_id="")
