import os
import re
from collections.abc import Collection, Iterable, Iterator
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from pathlib import Path
from typing import Protocol
from urllib.parse import unquote, urlsplit
from uuid import UUID, uuid4

import pyarrow as pa
import pyarrow.parquet as pq

from app.common.errors import (
    RawAssetAlreadyExistsError,
    RawAssetError,
    RawAssetVerificationError,
)

SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")
FINAL_FILE_NAME = "asset.parquet"
TEMP_FILE_PATTERN = ".asset.*.tmp.parquet"
LOCK_FILE_NAME = ".seal.lock"


@dataclass(frozen=True, slots=True)
class RawAssetContext:
    provider: str
    api_name: str
    business_date: date | None
    batch_id: UUID
    task_id: UUID
    execution_token: UUID | None = None


@dataclass(frozen=True, slots=True)
class RawAssetMetadata:
    storage_uri: str
    content_hash: str
    schema_fingerprint: str
    row_count: int
    size_bytes: int


@dataclass(frozen=True, slots=True)
class OrphanReport:
    temporary_files: tuple[Path, ...]
    unregistered_assets: tuple[Path, ...]


class RawAssetStore(Protocol):
    def seal(
        self,
        context: RawAssetContext,
        schema: pa.Schema,
        batches: Iterable[pa.RecordBatch | pa.Table],
    ) -> RawAssetMetadata: ...

    def iter_batches(
        self, storage_uri: str, *, batch_size: int = 65_536
    ) -> Iterator[pa.RecordBatch]: ...

    def verify(self, metadata: RawAssetMetadata) -> None: ...

    def expected_uri(self, context: RawAssetContext) -> str: ...

    def inspect(self, storage_uri: str) -> RawAssetMetadata: ...

    def exists(self, storage_uri: str) -> bool: ...

    def find_orphans(self, known_storage_uris: Collection[str]) -> OrphanReport: ...

    def remove_temporary_file(self, path: Path) -> None: ...


class LocalRawAssetStore:
    def __init__(self, root: Path, *, row_group_size: int = 100_000) -> None:
        if row_group_size <= 0:
            raise ValueError("row_group_size must be positive")
        self._root = root.expanduser().resolve()
        self._row_group_size = row_group_size
        self._root.mkdir(parents=True, exist_ok=True)

    def seal(
        self,
        context: RawAssetContext,
        schema: pa.Schema,
        batches: Iterable[pa.RecordBatch | pa.Table],
    ) -> RawAssetMetadata:
        final_path = self._asset_path(context)
        final_path.parent.mkdir(parents=True, exist_ok=True)
        self._assert_within_root(final_path.parent.resolve())

        lock_path = final_path.parent / LOCK_FILE_NAME
        lock_fd = self._acquire_lock(lock_path)
        temp_path = final_path.parent / f".asset.{uuid4().hex}.tmp.parquet"
        renamed = False
        try:
            if os.path.lexists(final_path):
                raise RawAssetAlreadyExistsError(f"raw asset already exists: {final_path}")

            row_count = self._write_parquet(temp_path, schema, batches)
            self._fsync_file(temp_path)
            self._verify_parquet(temp_path, schema, row_count)
            content_hash = _file_sha256(temp_path)
            schema_hash = schema_fingerprint(schema)

            os.rename(temp_path, final_path)
            renamed = True
            self._fsync_directory(final_path.parent)
            return RawAssetMetadata(
                storage_uri=final_path.as_uri(),
                content_hash=content_hash,
                schema_fingerprint=schema_hash,
                row_count=row_count,
                size_bytes=final_path.stat().st_size,
            )
        finally:
            if not renamed:
                temp_path.unlink(missing_ok=True)
            os.close(lock_fd)
            lock_path.unlink(missing_ok=True)

    def iter_batches(
        self,
        storage_uri: str,
        *,
        batch_size: int = 65_536,
    ) -> Iterator[pa.RecordBatch]:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        path = self._path_from_uri(storage_uri)
        yield from pq.ParquetFile(path).iter_batches(batch_size=batch_size)

    def verify(self, metadata: RawAssetMetadata) -> None:
        path = self._path_from_uri(metadata.storage_uri)
        if not path.is_file():
            raise RawAssetVerificationError(f"raw asset is missing: {path}")

        actual_hash = _file_sha256(path)
        if actual_hash != metadata.content_hash:
            raise RawAssetVerificationError(f"raw asset content hash does not match: {path}")

        try:
            parquet_file = pq.ParquetFile(path)
        except Exception as exc:
            raise RawAssetVerificationError(f"raw asset is not valid Parquet: {path}") from exc
        actual_fingerprint = schema_fingerprint(parquet_file.schema_arrow)
        actual_rows = parquet_file.metadata.num_rows
        if actual_fingerprint != metadata.schema_fingerprint or actual_rows != metadata.row_count:
            raise RawAssetVerificationError(f"raw asset verification failed: {path}")

    def expected_uri(self, context: RawAssetContext) -> str:
        return self._asset_path(context).as_uri()

    def inspect(self, storage_uri: str) -> RawAssetMetadata:
        path = self._path_from_uri(storage_uri)
        if not path.is_file():
            raise RawAssetVerificationError(f"raw asset is missing: {path}")
        try:
            parquet_file = pq.ParquetFile(path)
        except Exception as exc:
            raise RawAssetVerificationError(f"raw asset is not valid Parquet: {path}") from exc
        return RawAssetMetadata(
            storage_uri=path.as_uri(),
            content_hash=_file_sha256(path),
            schema_fingerprint=schema_fingerprint(parquet_file.schema_arrow),
            row_count=parquet_file.metadata.num_rows,
            size_bytes=path.stat().st_size,
        )

    def exists(self, storage_uri: str) -> bool:
        return self._path_from_uri(storage_uri).is_file()

    def find_orphans(self, known_storage_uris: Collection[str]) -> OrphanReport:
        known_paths = {self._path_from_uri(uri) for uri in known_storage_uris}
        temporary_files = tuple(
            sorted((*self._root.rglob(TEMP_FILE_PATTERN), *self._root.rglob(LOCK_FILE_NAME)))
        )
        final_files = set(self._root.rglob(FINAL_FILE_NAME))
        return OrphanReport(
            temporary_files=temporary_files,
            unregistered_assets=tuple(sorted(final_files - known_paths)),
        )

    def remove_temporary_file(self, path: Path) -> None:
        resolved_path = path.resolve(strict=False)
        self._assert_within_root(resolved_path)
        if not (
            path.name == LOCK_FILE_NAME
            or (path.name.startswith(".asset.") and path.name.endswith(".tmp.parquet"))
        ):
            raise RawAssetError(f"refusing to remove non-temporary path: {path}")
        path.unlink(missing_ok=True)
        self._fsync_directory(path.parent)

    def _asset_path(self, context: RawAssetContext) -> Path:
        for segment_name, value in (
            ("provider", context.provider),
            ("api_name", context.api_name),
        ):
            if not SAFE_SEGMENT.fullmatch(value):
                raise RawAssetError(f"unsafe {segment_name}: {value!r}")

        business_date = context.business_date.isoformat() if context.business_date else "_GLOBAL"
        path = (
            self._root
            / context.provider.lower()
            / context.api_name
            / f"business_date={business_date}"
            / f"batch_id={context.batch_id}"
            / f"task_id={context.task_id}"
        )
        if context.execution_token is not None:
            path /= f"execution_token={context.execution_token}"
        path /= FINAL_FILE_NAME
        self._assert_within_root(path)
        return path

    def _path_from_uri(self, storage_uri: str) -> Path:
        parsed = urlsplit(storage_uri)
        if parsed.scheme != "file" or parsed.netloc or parsed.query or parsed.fragment:
            raise RawAssetError("only local file URIs without host, query, or fragment are allowed")
        path = Path(unquote(parsed.path)).resolve()
        self._assert_within_root(path)
        return path

    def _assert_within_root(self, path: Path) -> None:
        try:
            path.relative_to(self._root)
        except ValueError as exc:
            raise RawAssetError(f"raw asset path escapes storage root: {path}") from exc

    def _write_parquet(
        self,
        temp_path: Path,
        schema: pa.Schema,
        batches: Iterable[pa.RecordBatch | pa.Table],
    ) -> int:
        row_count = 0
        with pq.ParquetWriter(temp_path, schema, compression="zstd") as writer:
            for item in batches:
                record_batches = (
                    item.to_batches(max_chunksize=self._row_group_size)
                    if isinstance(item, pa.Table)
                    else (item,)
                )
                for batch in record_batches:
                    if not batch.schema.equals(schema, check_metadata=True):
                        raise RawAssetError("record batch schema does not match expected schema")
                    writer.write_batch(batch, row_group_size=self._row_group_size)
                    row_count += batch.num_rows
        return row_count

    @staticmethod
    def _verify_parquet(path: Path, schema: pa.Schema, row_count: int) -> None:
        parquet_file = pq.ParquetFile(path)
        if not parquet_file.schema_arrow.equals(schema, check_metadata=True):
            raise RawAssetVerificationError("written Parquet schema does not match expected schema")
        if parquet_file.metadata.num_rows != row_count:
            raise RawAssetVerificationError("written Parquet row count does not match input")

    @staticmethod
    def _acquire_lock(lock_path: Path) -> int:
        try:
            return os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise RawAssetError(f"raw asset is already being sealed: {lock_path.parent}") from exc

    @staticmethod
    def _fsync_file(path: Path) -> None:
        with path.open("rb") as file_object:
            os.fsync(file_object.fileno())

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        directory_fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as file_object:
        for chunk in iter(lambda: file_object.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def schema_fingerprint(schema: pa.Schema) -> str:
    return sha256(schema.serialize().to_pybytes()).hexdigest()
