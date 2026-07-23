from app.catalog import (
    DatasetDependencySpec,
    DatasetSpec,
    DependencyKind,
    QualityRuleSpec,
    ReleaseScope,
    WriteStrategy,
)
from app.modules.processing.repository import _affected_dataset_specs


def _dataset(
    name: str,
    raw_name: str,
    *dependencies: DatasetDependencySpec,
) -> DatasetSpec:
    return DatasetSpec(
        dataset_name=name,
        processor=name,
        processor_version="1",
        dependencies=(
            DatasetDependencySpec(DependencyKind.RAW_ASSET, raw_name, ReleaseScope.GLOBAL),
            *dependencies,
        ),
        write_strategy=WriteStrategy.MASTER_MERGE,
        release_scope=ReleaseScope.GLOBAL,
        quality_rules=(QualityRuleSpec("test"),),
    )


def test_non_triggering_release_dependency_does_not_plan_stale_raw_recompute() -> None:
    master = _dataset("theme_index", "ths_index")
    members = _dataset(
        "theme_index_member",
        "ths_member",
        DatasetDependencySpec(
            DependencyKind.DATASET_RELEASE,
            "theme_index",
            ReleaseScope.GLOBAL,
            triggers_recompute=False,
        ),
    )

    selected = _affected_dataset_specs((master, members), {"ths_index"})

    assert tuple(spec.dataset_name for spec in selected) == ("theme_index",)


def test_triggering_release_dependency_remains_transitive() -> None:
    upstream = _dataset("upstream", "raw_upstream")
    downstream = _dataset(
        "downstream",
        "raw_downstream",
        DatasetDependencySpec(
            DependencyKind.DATASET_RELEASE,
            "upstream",
            ReleaseScope.GLOBAL,
        ),
    )

    selected = _affected_dataset_specs((upstream, downstream), {"raw_upstream"})

    assert tuple(spec.dataset_name for spec in selected) == ("upstream", "downstream")
