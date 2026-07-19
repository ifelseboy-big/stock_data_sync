import pytest

from app.common.errors import ProcessingError
from app.modules.processing.processors.transforms import optional_text, required_text


def test_text_transforms_remove_postgresql_invalid_nul() -> None:
    assert required_text("  题材\x00说明  ", "reason") == "题材说明"
    assert optional_text("\x00  ") is None


def test_required_text_rejects_value_that_only_contains_nul() -> None:
    with pytest.raises(ProcessingError, match="field reason is required"):
        required_text("\x00", "reason")
