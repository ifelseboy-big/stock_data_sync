from collections import namedtuple
from pathlib import Path

from app.core.config import Settings
from app.modules.acquisition.capacity import CapacityLevel, RawStorageCapacityGate
from app.modules.acquisition.models import BatchType

DiskUsage = namedtuple("DiskUsage", "total used free")


def _settings() -> Settings:
    return Settings(
        raw_storage_warning_used_percent=80,
        raw_storage_protect_used_percent=90,
        raw_storage_warning_free_bytes=200,
        raw_storage_protect_free_bytes=100,
    )


def test_capacity_warning_pauses_only_backfill(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "app.modules.acquisition.capacity.disk_usage",
        lambda path: DiskUsage(total=1_000, used=850, free=150),
    )

    snapshot = RawStorageCapacityGate(tmp_path, _settings()).snapshot()

    assert snapshot.level == CapacityLevel.WARNING
    assert BatchType.BACKFILL not in snapshot.allowed_batch_types()
    assert BatchType.DAILY in snapshot.allowed_batch_types()


def test_capacity_protect_allows_only_repair(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "app.modules.acquisition.capacity.disk_usage",
        lambda path: DiskUsage(total=1_000, used=950, free=50),
    )

    snapshot = RawStorageCapacityGate(tmp_path, _settings()).snapshot()

    assert snapshot.level == CapacityLevel.PROTECT
    assert snapshot.allowed_batch_types() == frozenset({BatchType.REPAIR})
