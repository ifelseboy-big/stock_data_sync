import argparse
import json
import os
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

BACKUP_PREFIX = "stock-data-sync-backup-"
MANIFEST_NAME = "manifest.json"
MANIFEST_HASH_NAME = "manifest.json.sha256"


@dataclass(frozen=True, slots=True)
class BackupConfig:
    install_dir: Path
    raw_data_dir: Path
    postgres_bin_dir: Path
    postgres_port: int
    postgres_db: str
    postgres_user: str
    postgres_password: str
    app_version: str

    @classmethod
    def from_environment(cls) -> "BackupConfig":
        required = {
            name: os.environ.get(name, "")
            for name in (
                "INSTALL_DIR",
                "RAW_DATA_DIR",
                "POSTGRES_BIN_DIR",
                "POSTGRES_PORT",
                "POSTGRES_DB",
                "POSTGRES_USER",
                "POSTGRES_PASSWORD",
            )
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise RuntimeError(f"缺少备份配置：{', '.join(missing)}")
        return cls(
            install_dir=Path(required["INSTALL_DIR"]),
            raw_data_dir=Path(required["RAW_DATA_DIR"]),
            postgres_bin_dir=Path(required["POSTGRES_BIN_DIR"]),
            postgres_port=int(required["POSTGRES_PORT"]),
            postgres_db=required["POSTGRES_DB"],
            postgres_user=required["POSTGRES_USER"],
            postgres_password=required["POSTGRES_PASSWORD"],
            app_version=os.environ.get("APP_VERSION", "unknown"),
        )


@dataclass(frozen=True, slots=True)
class RestoreResult:
    previous_raw_dir: Path | None
    previous_database: str | None


def create_backup(
    config: BackupConfig,
    target_dir: Path,
    *,
    now: datetime | None = None,
    full: bool = False,
    database_dumper: Callable[[Path], None] | None = None,
) -> Path:
    target_root = _validated_target(config.install_dir, target_dir)
    target_root.mkdir(parents=True, exist_ok=True)
    created_at = (now or datetime.now(UTC)).astimezone(UTC)
    backup_id = f"{BACKUP_PREFIX}{created_at:%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}"
    staging = target_root / f".{backup_id}.incomplete"
    final_dir = target_root / backup_id
    staging.mkdir(mode=0o700)
    try:
        database_path = staging / "postgres.dump"
        (database_dumper or (lambda path: _dump_database(config, path)))(database_path)
        database_hash = _sha256_file(database_path)
        previous = {} if full else _latest_manifest(target_root)
        previous_files = {
            str(item["path"]): item
            for item in previous.get("rawFiles", [])
            if isinstance(item, dict) and "path" in item
        }
        raw_files: list[dict[str, object]] = []
        raw_root = config.raw_data_dir.resolve()
        if not raw_root.is_dir():
            raise RuntimeError(f"原始数据目录不存在：{raw_root}")
        for source in sorted(raw_root.rglob("*.parquet")):
            if source.is_symlink() or not source.is_file():
                continue
            relative = source.resolve().relative_to(raw_root).as_posix()
            file_hash = _sha256_file(source)
            size = source.stat().st_size
            previous_item = previous_files.get(relative)
            if (
                previous_item
                and previous_item.get("sha256") == file_hash
                and previous_item.get("size") == size
                and _stored_raw_file(target_root, previous_item).is_file()
            ):
                stored_in = str(previous_item["storedIn"])
            else:
                destination = staging / "raw" / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                stored_in = backup_id
            raw_files.append(
                {
                    "path": relative,
                    "size": size,
                    "sha256": file_hash,
                    "storedIn": stored_in,
                }
            )
        manifest: dict[str, object] = {
            "formatVersion": 1,
            "backupId": backup_id,
            "createdAt": created_at.isoformat().replace("+00:00", "Z"),
            "appVersion": config.app_version,
            "backupType": "full" if full else "incremental",
            "rawRoot": raw_root.as_uri(),
            "database": {
                "path": "postgres.dump",
                "size": database_path.stat().st_size,
                "sha256": database_hash,
                "format": "postgresql-custom",
            },
            "rawFiles": raw_files,
        }
        manifest_path = staging / MANIFEST_NAME
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (staging / MANIFEST_HASH_NAME).write_text(
            f"{_sha256_file(manifest_path)}  {MANIFEST_NAME}\n",
            encoding="ascii",
        )
        staging.rename(final_dir)
        verify_backup(final_dir)
        return final_dir
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def verify_backup(backup_dir: Path) -> dict[str, int]:
    backup_path = backup_dir.resolve()
    manifest_path = backup_path / MANIFEST_NAME
    expected_manifest_hash = _read_hash_file(backup_path / MANIFEST_HASH_NAME)
    if _sha256_file(manifest_path) != expected_manifest_hash:
        raise RuntimeError("备份清单 SHA-256 校验失败")
    manifest = _load_manifest(manifest_path)
    if manifest.get("backupId") != backup_path.name:
        raise RuntimeError("备份目录与清单 backupId 不一致")
    database = manifest.get("database")
    if not isinstance(database, dict):
        raise RuntimeError("备份清单缺少数据库记录")
    database_path = backup_path / _safe_relative(str(database.get("path", "")))
    if database_path.stat().st_size != int(database.get("size", -1)):
        raise RuntimeError("数据库备份大小校验失败")
    if _sha256_file(database_path) != database.get("sha256"):
        raise RuntimeError("数据库备份 SHA-256 校验失败")
    raw_files = manifest.get("rawFiles")
    if not isinstance(raw_files, list):
        raise RuntimeError("备份清单缺少原始资产记录")
    total_bytes = 0
    target_root = backup_path.parent
    for item in raw_files:
        if not isinstance(item, dict):
            raise RuntimeError("原始资产清单格式无效")
        stored = _stored_raw_file(target_root, item)
        expected_size = int(item.get("size", -1))
        if stored.stat().st_size != expected_size:
            raise RuntimeError(f"原始资产大小校验失败：{item.get('path')}")
        if _sha256_file(stored) != item.get("sha256"):
            raise RuntimeError(f"原始资产 SHA-256 校验失败：{item.get('path')}")
        total_bytes += expected_size
    return {
        "databaseBytes": database_path.stat().st_size,
        "rawFileCount": len(raw_files),
        "rawBytes": total_bytes,
    }


def restore_backup(
    config: BackupConfig,
    backup_dir: Path,
    *,
    database_restorer: Callable[[Path], None] | None = None,
    uri_rewriter: Callable[[str, str], None] | None = None,
) -> RestoreResult:
    stats = verify_backup(backup_dir)
    backup_path = backup_dir.resolve()
    manifest = _load_manifest(backup_path / MANIFEST_NAME)
    raw_files = manifest.get("rawFiles")
    if not isinstance(raw_files, list):
        raise RuntimeError("备份清单缺少原始资产记录")
    raw_root = config.raw_data_dir.resolve()
    install_root = config.install_dir.resolve()
    if not raw_root.is_relative_to(install_root):
        raise RuntimeError("RAW_DATA_DIR 必须位于安装目录内部才能执行原子恢复")
    raw_parent = raw_root.parent
    raw_parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(raw_parent).free < stats["rawBytes"]:
        raise RuntimeError("安装磁盘剩余空间不足，无法准备原始资产恢复副本")
    staging = raw_parent / f".raw-restore-{uuid4().hex}"
    staging.mkdir(mode=0o700)
    previous_raw: Path | None = None
    previous_database: str | None = None
    staging_database: str | None = None
    try:
        for item in raw_files:
            if not isinstance(item, dict):
                raise RuntimeError("原始资产清单格式无效")
            source = _stored_raw_file(backup_path.parent, item)
            destination = staging / _safe_relative(str(item.get("path", "")))
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            if _sha256_file(destination) != item.get("sha256"):
                raise RuntimeError(f"恢复副本 SHA-256 校验失败：{item.get('path')}")

        database = manifest.get("database")
        if not isinstance(database, dict):
            raise RuntimeError("备份清单缺少数据库记录")
        database_path = backup_path / _safe_relative(str(database.get("path", "")))
        old_raw_root = str(manifest.get("rawRoot", ""))
        if not old_raw_root.startswith("file://"):
            raise RuntimeError("备份清单缺少有效 rawRoot")
        old_prefix = old_raw_root.rstrip("/") + "/"
        new_prefix = raw_root.as_uri().rstrip("/") + "/"
        if database_restorer is not None:
            database_restorer(database_path)
            (uri_rewriter or (lambda _old, _new: None))(old_prefix, new_prefix)
        else:
            staging_database = _restore_database_to_staging(config, database_path)
            _rewrite_storage_uris(
                config,
                old_prefix,
                new_prefix,
                database_name=staging_database,
            )
            previous_database = _activate_restored_database(config, staging_database)
            staging_database = None

        if raw_root.exists():
            previous_raw = raw_parent / (
                f"raw.before-restore-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}"
            )
            raw_root.rename(previous_raw)
        try:
            staging.rename(raw_root)
        except Exception:
            if previous_raw is not None and previous_raw.exists() and not raw_root.exists():
                previous_raw.rename(raw_root)
            raise
        return RestoreResult(
            previous_raw_dir=previous_raw,
            previous_database=previous_database,
        )
    except Exception:
        if staging_database is not None:
            _drop_database(config, staging_database)
        if previous_database is not None:
            _rollback_activated_database(config, previous_database)
        if staging.exists():
            shutil.rmtree(staging)
        raise


def _validated_target(install_dir: Path, target_dir: Path) -> Path:
    if not target_dir.is_absolute():
        raise RuntimeError("备份目标必须是绝对路径")
    install_root = install_dir.resolve()
    target_root = target_dir.resolve()
    if target_root == install_root or target_root.is_relative_to(install_root):
        raise RuntimeError("备份目标不能位于应用安装目录内部")
    return target_root


def _dump_database(config: BackupConfig, output_path: Path) -> None:
    pg_dump = config.postgres_bin_dir / "pg_dump"
    if not pg_dump.is_file():
        raise RuntimeError(f"找不到 pg_dump：{pg_dump}")
    subprocess.run(
        (
            str(pg_dump),
            "--host=127.0.0.1",
            f"--port={config.postgres_port}",
            f"--username={config.postgres_user}",
            "--format=custom",
            "--compress=6",
            f"--file={output_path}",
            config.postgres_db,
        ),
        check=True,
        env=os.environ.copy(),
    )


def _restore_database_to_staging(config: BackupConfig, database_path: Path) -> str:
    import psycopg
    from psycopg import sql

    pg_restore = config.postgres_bin_dir / "pg_restore"
    if not pg_restore.is_file():
        raise RuntimeError(f"找不到 pg_restore：{pg_restore}")
    staging_database = f"{config.postgres_db[:38]}_restore_{uuid4().hex[:12]}"
    with psycopg.connect(
        host="127.0.0.1",
        port=config.postgres_port,
        dbname="postgres",
        user=config.postgres_user,
        password=config.postgres_password,
        autocommit=True,
    ) as connection:
        connection.execute(
            sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(staging_database),
                sql.Identifier(config.postgres_user),
            )
        )
    try:
        subprocess.run(
            (
                str(pg_restore),
                "--host=127.0.0.1",
                f"--port={config.postgres_port}",
                f"--username={config.postgres_user}",
                f"--dbname={staging_database}",
                "--exit-on-error",
                "--single-transaction",
                str(database_path),
            ),
            check=True,
            env=os.environ.copy(),
        )
    except Exception:
        _drop_database(config, staging_database)
        raise
    return staging_database


def _rewrite_storage_uris(
    config: BackupConfig,
    old_prefix: str,
    new_prefix: str,
    *,
    database_name: str,
) -> None:
    import psycopg

    with psycopg.connect(
        host="127.0.0.1",
        port=config.postgres_port,
        dbname=database_name,
        user=config.postgres_user,
        password=config.postgres_password,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE raw_data_asset "
                "SET storage_uri = %s || substring(storage_uri FROM %s) "
                "WHERE starts_with(storage_uri, %s)",
                (new_prefix, len(old_prefix) + 1, old_prefix),
            )


def _activate_restored_database(config: BackupConfig, staging_database: str) -> str:
    import psycopg
    from psycopg import sql

    previous_database = (
        f"{config.postgres_db[:24]}_before_restore_"
        f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}_{uuid4().hex[:6]}"
    )
    with psycopg.connect(
        host="127.0.0.1",
        port=config.postgres_port,
        dbname="postgres",
        user=config.postgres_user,
        password=config.postgres_password,
        autocommit=True,
    ) as connection:
        _terminate_database_connections(connection, config.postgres_db)
        connection.execute(
            sql.SQL("ALTER DATABASE {} RENAME TO {}").format(
                sql.Identifier(config.postgres_db),
                sql.Identifier(previous_database),
            )
        )
        try:
            connection.execute(
                sql.SQL("ALTER DATABASE {} RENAME TO {}").format(
                    sql.Identifier(staging_database),
                    sql.Identifier(config.postgres_db),
                )
            )
        except Exception:
            connection.execute(
                sql.SQL("ALTER DATABASE {} RENAME TO {}").format(
                    sql.Identifier(previous_database),
                    sql.Identifier(config.postgres_db),
                )
            )
            raise
    return previous_database


def _rollback_activated_database(config: BackupConfig, previous_database: str) -> None:
    import psycopg
    from psycopg import sql

    failed_database = f"{config.postgres_db[:31]}_failed_restore_{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    with psycopg.connect(
        host="127.0.0.1",
        port=config.postgres_port,
        dbname="postgres",
        user=config.postgres_user,
        password=config.postgres_password,
        autocommit=True,
    ) as connection:
        _terminate_database_connections(connection, config.postgres_db)
        connection.execute(
            sql.SQL("ALTER DATABASE {} RENAME TO {}").format(
                sql.Identifier(config.postgres_db),
                sql.Identifier(failed_database),
            )
        )
        connection.execute(
            sql.SQL("ALTER DATABASE {} RENAME TO {}").format(
                sql.Identifier(previous_database),
                sql.Identifier(config.postgres_db),
            )
        )


def _drop_database(config: BackupConfig, database_name: str) -> None:
    import psycopg
    from psycopg import sql

    with psycopg.connect(
        host="127.0.0.1",
        port=config.postgres_port,
        dbname="postgres",
        user=config.postgres_user,
        password=config.postgres_password,
        autocommit=True,
    ) as connection:
        connection.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(database_name))
        )


def _terminate_database_connections(connection: Any, database_name: str) -> None:
    connection.execute(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        "WHERE datname = %s AND pid <> pg_backend_pid()",
        (database_name,),
    )


def _latest_manifest(target_root: Path) -> dict[str, Any]:
    candidates = sorted(
        (
            path
            for path in target_root.glob(f"{BACKUP_PREFIX}*")
            if path.is_dir() and (path / MANIFEST_NAME).is_file()
        ),
        reverse=True,
    )
    if not candidates:
        return {}
    verify_backup(candidates[0])
    return _load_manifest(candidates[0] / MANIFEST_NAME)


def _stored_raw_file(target_root: Path, item: dict[str, Any]) -> Path:
    stored_in = str(item.get("storedIn", ""))
    if not stored_in.startswith(BACKUP_PREFIX) or "/" in stored_in or "\\" in stored_in:
        raise RuntimeError("原始资产清单包含无效 storedIn")
    return target_root / stored_in / "raw" / _safe_relative(str(item.get("path", "")))


def _safe_relative(value: str) -> Path:
    path = Path(value)
    if not value or path.is_absolute() or ".." in path.parts:
        raise RuntimeError("备份清单包含不安全路径")
    return path


def _load_manifest(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("formatVersion") != 1:
        raise RuntimeError("不支持的备份清单版本")
    return data


def _read_hash_file(path: Path) -> str:
    value = path.read_text(encoding="ascii").strip().split(maxsplit=1)
    if len(value) != 2 or value[1] != MANIFEST_NAME or len(value[0]) != 64:
        raise RuntimeError("备份清单哈希文件格式无效")
    return value[0]


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stock Data Sync 备份与校验")
    subparsers = parser.add_subparsers(dest="command", required=True)
    backup_parser = subparsers.add_parser("create")
    backup_parser.add_argument("--target-dir", required=True, type=Path)
    backup_parser.add_argument("--full", action="store_true")
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--backup-dir", required=True, type=Path)
    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("--backup-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.command == "create":
        backup_dir = create_backup(BackupConfig.from_environment(), args.target_dir, full=args.full)
        result = verify_backup(backup_dir)
        print(json.dumps({"backupDir": str(backup_dir), **result}, ensure_ascii=False))
    elif args.command == "verify":
        result = verify_backup(args.backup_dir)
        print(
            json.dumps({"backupDir": str(args.backup_dir.resolve()), **result}, ensure_ascii=False)
        )
    else:
        restore_result = restore_backup(BackupConfig.from_environment(), args.backup_dir)
        print(
            json.dumps(
                {
                    "backupDir": str(args.backup_dir.resolve()),
                    "previousRawDir": (
                        str(restore_result.previous_raw_dir)
                        if restore_result.previous_raw_dir
                        else None
                    ),
                    "previousDatabase": restore_result.previous_database,
                },
                ensure_ascii=False,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
