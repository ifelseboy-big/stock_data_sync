from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.maintenance.backup import (
    BackupConfig,
    create_backup,
    restore_backup,
    verify_backup,
)


def _config(tmp_path: Path) -> BackupConfig:
    install_dir = tmp_path / "installation"
    raw_dir = install_dir / "data" / "raw"
    raw_dir.mkdir(parents=True)
    return BackupConfig(
        install_dir=install_dir,
        raw_data_dir=raw_dir,
        postgres_bin_dir=Path("/not-used"),
        postgres_port=5432,
        postgres_db="stock_data_sync",
        postgres_user="stock_sync",
        postgres_password="test-password",
        app_version="test",
    )


def _fake_dump(path: Path) -> None:
    path.write_bytes(b"postgres-custom-dump")


def test_incremental_backup_reuses_unchanged_raw_assets(tmp_path: Path) -> None:
    config = _config(tmp_path)
    target = tmp_path / "external-backups"
    first_asset = config.raw_data_dir / "TUSHARE" / "daily" / "first.parquet"
    first_asset.parent.mkdir(parents=True)
    first_asset.write_bytes(b"first")
    first = create_backup(
        config,
        target,
        now=datetime(2026, 7, 19, tzinfo=UTC),
        database_dumper=_fake_dump,
    )
    second_asset = first_asset.with_name("second.parquet")
    second_asset.write_bytes(b"second")
    second = create_backup(
        config,
        target,
        now=datetime(2026, 7, 20, tzinfo=UTC),
        database_dumper=_fake_dump,
    )

    assert verify_backup(first)["rawFileCount"] == 1
    assert verify_backup(second)["rawFileCount"] == 2
    assert not (second / "raw" / first_asset.relative_to(config.raw_data_dir)).exists()
    assert (second / "raw" / second_asset.relative_to(config.raw_data_dir)).is_file()


def test_backup_rejects_target_inside_installation(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with pytest.raises(RuntimeError, match="不能位于应用安装目录内部"):
        create_backup(
            config,
            config.install_dir / "backups",
            now=datetime.now(UTC) + timedelta(seconds=1),
            database_dumper=_fake_dump,
        )


def test_restore_rebuilds_incremental_chain_and_keeps_previous_raw(tmp_path: Path) -> None:
    config = _config(tmp_path)
    target = tmp_path / "external-backups"
    first_asset = config.raw_data_dir / "daily" / "first.parquet"
    first_asset.parent.mkdir(parents=True)
    first_asset.write_bytes(b"first")
    create_backup(
        config,
        target,
        now=datetime(2026, 7, 19, tzinfo=UTC),
        database_dumper=_fake_dump,
    )
    second_asset = first_asset.with_name("second.parquet")
    second_asset.write_bytes(b"second")
    latest = create_backup(
        config,
        target,
        now=datetime(2026, 7, 20, tzinfo=UTC),
        database_dumper=_fake_dump,
    )
    first_asset.write_bytes(b"current-data-to-keep")
    restored_database: list[Path] = []
    rewritten_prefixes: list[tuple[str, str]] = []

    restore_result = restore_backup(
        config,
        latest,
        database_restorer=restored_database.append,
        uri_rewriter=lambda old, new: rewritten_prefixes.append((old, new)),
    )

    assert first_asset.read_bytes() == b"first"
    assert second_asset.read_bytes() == b"second"
    assert restore_result.previous_raw_dir is not None
    assert (
        restore_result.previous_raw_dir / "daily" / "first.parquet"
    ).read_bytes() == b"current-data-to-keep"
    assert restore_result.previous_database is None
    assert restored_database == [latest / "postgres.dump"]
    assert rewritten_prefixes[0][1] == config.raw_data_dir.resolve().as_uri() + "/"
