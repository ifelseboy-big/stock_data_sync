from collections import defaultdict
from dataclasses import dataclass
from typing import cast

from app.catalog import ApiSpec
from app.common.errors import ProcessingError, RawAssetError
from app.modules.processing.domain import RawDependencyAsset
from app.storage import RawAssetMetadata, RawAssetStore, schema_fingerprint

type RawRow = dict[str, object]


@dataclass(frozen=True, slots=True)
class RawReadResult:
    rows_by_api: dict[str, tuple[RawRow, ...]]
    row_count: int


def read_raw_assets(
    dependencies: tuple[RawDependencyAsset, ...],
    asset_store: RawAssetStore,
    api_specs: tuple[ApiSpec, ...],
) -> RawReadResult:
    dependencies_by_name: dict[str, list[RawDependencyAsset]] = defaultdict(list)
    for dependency in dependencies:
        dependencies_by_name[dependency.dependency_name].append(dependency)

    rows_by_api: dict[str, tuple[RawRow, ...]] = {}
    total_rows = 0
    for spec in api_specs:
        matching = dependencies_by_name.get(spec.api_name, [])
        if not matching:
            raise ProcessingError(f"missing required raw dependency: {spec.api_name}")

        expected_fingerprint = schema_fingerprint(spec.schema)
        rows: list[RawRow] = []
        keys: set[tuple[object, ...]] = set()
        for dependency in sorted(matching, key=lambda item: item.scope_key):
            if dependency.schema_fingerprint != expected_fingerprint:
                raise ProcessingError(
                    f"{spec.api_name} schema mismatch for asset {dependency.asset_id}"
                )
            try:
                asset_store.verify(
                    RawAssetMetadata(
                        storage_uri=dependency.storage_uri,
                        content_hash=dependency.content_hash,
                        schema_fingerprint=dependency.schema_fingerprint,
                        row_count=dependency.row_count,
                        size_bytes=0,
                    )
                )
            except RawAssetError as exc:
                raise ProcessingError(str(exc), retryable=False) from exc

            for batch in asset_store.iter_batches(dependency.storage_uri):
                for source in batch.to_pylist():
                    row = cast(RawRow, source)
                    key = tuple(row.get(column) for column in spec.natural_key)
                    if any(value is None or value == "" for value in key):
                        raise ProcessingError(
                            f"{spec.api_name} contains an empty natural key: {key}"
                        )
                    if key in keys:
                        raise ProcessingError(
                            f"{spec.api_name} contains duplicate natural key: {key}"
                        )
                    keys.add(key)
                    rows.append(row)

        rows_by_api[spec.api_name] = tuple(rows)
        total_rows += len(rows)

    return RawReadResult(rows_by_api=rows_by_api, row_count=total_rows)
