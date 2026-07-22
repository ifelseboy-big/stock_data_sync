from dataclasses import replace
from datetime import date
from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import uuid4

import pyarrow as pa
import pytest

from app.common.errors import (
    RawAssetAlreadyExistsError,
    RawAssetError,
    RawAssetVerificationError,
)
from app.storage import LocalRawAssetStore, RawAssetContext, RawAssetMetadata


def _context(*, api_name: str = "daily") -> RawAssetContext:
    return RawAssetContext(
        provider="tushare",
        api_name=api_name,
        business_date=date(2026, 7, 19),
        batch_id=uuid4(),
        task_id=uuid4(),
    )


def _schema() -> pa.Schema:
    return pa.schema((pa.field("ts_code", pa.string()), pa.field("close", pa.float64())))


def _uri_path(storage_uri: str) -> Path:
    return Path(unquote(urlsplit(storage_uri).path))


def test_seal_streams_batches_and_verifies_asset(tmp_path: Path) -> None:
    store = LocalRawAssetStore(tmp_path, row_group_size=2)
    schema = _schema()
    batches = (
        pa.record_batch([["000001.SZ", "000002.SZ"], [10.1, 20.2]], schema=schema),
        pa.record_batch([["000003.SZ"], [30.3]], schema=schema),
    )

    metadata = store.seal(_context(), schema, iter(batches))

    assert metadata.row_count == 3
    assert metadata.size_bytes > 0
    assert len(metadata.content_hash) == 64
    assert len(metadata.schema_fingerprint) == 64
    assert [batch.num_rows for batch in store.iter_batches(metadata.storage_uri, batch_size=2)] == [
        2,
        1,
    ]
    store.verify(metadata)


def test_zero_row_asset_keeps_expected_schema(tmp_path: Path) -> None:
    store = LocalRawAssetStore(tmp_path)

    metadata = store.seal(_context(), _schema(), ())
    batches = tuple(store.iter_batches(metadata.storage_uri))

    assert metadata.row_count == 0
    assert batches == ()
    store.verify(metadata)


def test_schema_fingerprint_is_stable_and_order_sensitive(tmp_path: Path) -> None:
    store = LocalRawAssetStore(tmp_path)
    first = store.seal(_context(), _schema(), ())
    second = store.seal(_context(), _schema(), ())
    reversed_schema = pa.schema((pa.field("close", pa.float64()), pa.field("ts_code", pa.string())))
    reordered = store.seal(_context(), reversed_schema, ())

    assert first.schema_fingerprint == second.schema_fingerprint
    assert first.schema_fingerprint != reordered.schema_fingerprint


def test_sealed_asset_cannot_be_overwritten(tmp_path: Path) -> None:
    store = LocalRawAssetStore(tmp_path)
    context = _context()
    store.seal(context, _schema(), ())

    with pytest.raises(RawAssetAlreadyExistsError):
        store.seal(context, _schema(), ())


def test_execution_tokens_use_isolated_asset_paths(tmp_path: Path) -> None:
    store = LocalRawAssetStore(tmp_path)
    context = _context()
    first = replace(context, execution_token=uuid4())
    second = replace(context, execution_token=uuid4())

    first_metadata = store.seal(first, _schema(), ())
    second_metadata = store.seal(second, _schema(), ())

    assert first_metadata.storage_uri != second_metadata.storage_uri
    assert f"execution_token={first.execution_token}" in str(_uri_path(first_metadata.storage_uri))
    assert f"execution_token={second.execution_token}" in str(
        _uri_path(second_metadata.storage_uri)
    )


def test_failed_write_leaves_no_ready_or_temporary_file(tmp_path: Path) -> None:
    store = LocalRawAssetStore(tmp_path)
    wrong_batch = pa.record_batch([[1]], names=["unexpected"])

    with pytest.raises(RawAssetError, match="schema does not match"):
        store.seal(_context(), _schema(), (wrong_batch,))

    assert tuple(tmp_path.rglob("asset.parquet")) == ()
    assert tuple(tmp_path.rglob("*.tmp.parquet")) == ()
    assert tuple(tmp_path.rglob(".seal.lock")) == ()


def test_verify_detects_content_tampering(tmp_path: Path) -> None:
    store = LocalRawAssetStore(tmp_path)
    metadata = store.seal(_context(), _schema(), ())
    path = _uri_path(metadata.storage_uri)
    path.write_bytes(path.read_bytes() + b"tampered")

    with pytest.raises(RawAssetVerificationError):
        store.verify(metadata)


def test_find_orphans_reports_unregistered_assets_and_temporary_files(tmp_path: Path) -> None:
    store = LocalRawAssetStore(tmp_path)
    registered = store.seal(_context(), _schema(), ())
    orphan = store.seal(_context(), _schema(), ())
    temporary = tmp_path / ".asset.interrupted.tmp.parquet"
    temporary.touch()

    report = store.find_orphans((registered.storage_uri,))

    assert report.unregistered_assets == (_uri_path(orphan.storage_uri),)
    assert report.temporary_files == (temporary,)


def test_storage_rejects_unsafe_api_name_and_uri_escape(tmp_path: Path) -> None:
    store = LocalRawAssetStore(tmp_path)

    with pytest.raises(RawAssetError, match="unsafe api_name"):
        store.seal(_context(api_name="../daily"), _schema(), ())

    outside = RawAssetMetadata(
        storage_uri=(tmp_path.parent / "outside.parquet").as_uri(),
        content_hash="0" * 64,
        schema_fingerprint="0" * 64,
        row_count=0,
        size_bytes=0,
    )
    with pytest.raises(RawAssetError, match="escapes storage root"):
        store.verify(outside)
