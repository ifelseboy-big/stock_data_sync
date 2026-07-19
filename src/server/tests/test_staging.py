from app.modules.processing.staging import _stage_table_name


def test_stage_table_name_fits_postgresql_identifier_limit() -> None:
    name = _stage_table_name("a_very_long_business_dataset_table_name")

    assert len(name.encode()) <= 63
    assert name.startswith("_stage_a_very_long_business_da")
