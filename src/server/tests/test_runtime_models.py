from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from app.db.base import Base
from app.models import import_all_models
from app.modules.acquisition.models import (
    BatchStatus,
    BatchType,
    CollectionTaskStatus,
)
from app.modules.processing.models import (
    DependencyStatus,
    DependencyType,
    ProcessingTaskStatus,
    ReleaseScopeType,
)

RUNTIME_TABLES = {
    "collection_batch",
    "collection_task",
    "raw_data_asset",
    "processing_task",
    "processing_dependency",
    "dataset_release",
    "deferred_collection_stage",
}


def test_runtime_tables_are_registered() -> None:
    import_all_models()

    assert RUNTIME_TABLES <= set(Base.metadata.tables)


def test_runtime_indexes_match_queue_access_paths() -> None:
    import_all_models()

    index_names = {
        index.name
        for table_name in RUNTIME_TABLES
        for index in Base.metadata.tables[table_name].indexes
    }

    assert {
        "uq_collection_batch_slot",
        "idx_batch_active_schedule",
        "idx_task_batch_status",
        "idx_task_retry_due",
        "idx_collection_task_recovery",
        "idx_raw_asset_api_date",
        "uq_processing_output_version",
        "idx_process_batch_status",
        "idx_process_queue",
        "idx_processing_retry_due",
        "idx_dependency_asset",
        "idx_dependency_release_process",
        "idx_dependency_waiting",
        "idx_release_process",
        "idx_release_business_date",
        "idx_deferred_collection_stage_pending",
    } <= index_names


def test_dependency_targets_are_mutually_exclusive() -> None:
    import_all_models()
    table = Base.metadata.tables["processing_dependency"]
    check_names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert "ck_processing_dependency_dependency_target" in check_names


def test_runtime_enums_use_database_values() -> None:
    assert [item.value for item in BatchType] == [
        "MASTER",
        "DAILY",
        "HOT",
        "DELAYED",
        "BACKFILL",
        "REPAIR",
    ]
    assert "CLOSED" in BatchStatus
    assert "EMPTY_VALID" in CollectionTaskStatus
    assert "WAITING_DEPENDENCY" in ProcessingTaskStatus
    assert "DATASET_RELEASE" in DependencyType
    assert "MISSING" in DependencyStatus
    assert "MONTH" in ReleaseScopeType


def test_alembic_has_one_head() -> None:
    server_dir = Path(__file__).resolve().parents[1]
    config = Config(server_dir / "alembic.ini")
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == ["20260720_0010"]


def test_runtime_schema_compiles_for_postgresql() -> None:
    import_all_models()
    dialect = postgresql.dialect()

    for table_name in RUNTIME_TABLES:
        table = Base.metadata.tables[table_name]
        assert str(CreateTable(table).compile(dialect=dialect))
        for index in table.indexes:
            assert str(CreateIndex(index).compile(dialect=dialect))
