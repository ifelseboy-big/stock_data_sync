from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from shutil import disk_usage

from app.core.config import Settings
from app.modules.acquisition.models import BatchType


class CapacityLevel(StrEnum):
    NORMAL = "NORMAL"
    WARNING = "WARNING"
    PROTECT = "PROTECT"


@dataclass(frozen=True, slots=True)
class CapacitySnapshot:
    level: CapacityLevel
    total_bytes: int
    used_bytes: int
    free_bytes: int
    used_percent: float

    def allowed_batch_types(self) -> frozenset[BatchType]:
        if self.level == CapacityLevel.PROTECT:
            return frozenset({BatchType.REPAIR})
        if self.level == CapacityLevel.WARNING:
            return frozenset(set(BatchType) - {BatchType.BACKFILL})
        return frozenset(BatchType)


class RawStorageCapacityGate:
    def __init__(self, root: Path, settings: Settings) -> None:
        self._root = root
        self._settings = settings

    def snapshot(self) -> CapacitySnapshot:
        self._root.mkdir(parents=True, exist_ok=True)
        usage = disk_usage(self._root)
        used_percent = usage.used / usage.total * 100 if usage.total else 100.0
        if (
            used_percent >= self._settings.raw_storage_protect_used_percent
            or usage.free <= self._settings.raw_storage_protect_free_bytes
        ):
            level = CapacityLevel.PROTECT
        elif (
            used_percent >= self._settings.raw_storage_warning_used_percent
            or usage.free <= self._settings.raw_storage_warning_free_bytes
        ):
            level = CapacityLevel.WARNING
        else:
            level = CapacityLevel.NORMAL
        return CapacitySnapshot(
            level=level,
            total_bytes=usage.total,
            used_bytes=usage.used,
            free_bytes=usage.free,
            used_percent=used_percent,
        )
