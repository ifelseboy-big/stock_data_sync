from datetime import datetime
from typing import Any

import pytest

from app.modules.acquisition.planner import StagePlan
from app.scheduler import jobs


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, timezone: Any = None) -> "FrozenDateTime":
        return cls(2026, 7, 19, 7, 30, tzinfo=timezone)


@pytest.mark.parametrize(
    ("planner_name", "hour", "minute"),
    (
        ("plan_stock_master", 8, 30),
        ("plan_etf_master", 8, 35),
        ("plan_special_master", 8, 40),
    ),
)
def test_master_planner_creates_a_daily_batch(
    monkeypatch: pytest.MonkeyPatch,
    planner_name: str,
    hour: int,
    minute: int,
) -> None:
    captured: list[StagePlan] = []

    def capture_stage(stage: StagePlan, **_: Any) -> None:
        captured.append(stage)

    monkeypatch.setattr(jobs, "datetime", FrozenDateTime)
    monkeypatch.setattr(jobs, "_plan_stage", capture_stage)

    getattr(jobs, planner_name)()

    assert len(captured) == 1
    assert captured[0].business_date == FrozenDateTime(2026, 7, 19).date()
    assert captured[0].scheduled_at.date() == FrozenDateTime(2026, 7, 19).date()
    assert captured[0].scheduled_at.hour == hour
    assert captured[0].scheduled_at.minute == minute


def test_trade_calendar_planner_creates_a_daily_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[StagePlan] = []

    class ApiSpecs:
        @staticmethod
        def get(api_name: str) -> object:
            assert api_name == "trade_cal"
            return object()

    def capture_stage(stage: StagePlan, **_: Any) -> None:
        captured.append(stage)

    monkeypatch.setattr(jobs, "datetime", FrozenDateTime)
    monkeypatch.setattr(jobs, "get_api_specs", ApiSpecs)
    monkeypatch.setattr(jobs, "_plan_stage", capture_stage)

    jobs.plan_trade_calendar()

    assert len(captured) == 1
    assert captured[0].business_date == FrozenDateTime(2026, 7, 19).date()
    assert captured[0].scheduled_at.date() == FrozenDateTime(2026, 7, 19).date()
    assert (captured[0].scheduled_at.hour, captured[0].scheduled_at.minute) == (8, 20)


def test_board_member_planner_creates_a_daily_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[StagePlan] = []

    class Session:
        scalar_call_count = 0

        def __enter__(self) -> "Session":
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def scalars(self, _: object) -> tuple[str, ...]:
            self.scalar_call_count += 1
            return ("885001.TI",) if self.scalar_call_count == 1 else ("885002.TI",)

    def capture_stage(stage: StagePlan, **_: Any) -> None:
        captured.append(stage)

    monkeypatch.setattr(jobs, "datetime", FrozenDateTime)
    monkeypatch.setattr(jobs, "SyncSessionFactory", Session)
    monkeypatch.setattr(jobs, "_plan_stage", capture_stage)

    jobs.plan_ths_board_members()

    assert len(captured) == 1
    assert captured[0].business_date == FrozenDateTime(2026, 7, 19).date()
    assert captured[0].scheduled_at.date() == FrozenDateTime(2026, 7, 19).date()
    assert (captured[0].scheduled_at.hour, captured[0].scheduled_at.minute) == (10, 0)
