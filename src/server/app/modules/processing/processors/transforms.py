from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from app.common.errors import ProcessingError


def required_text(value: object, field: str) -> str:
    text = _clean_text(value)
    if not text:
        raise ProcessingError(f"field {field} is required")
    return text


def optional_text(value: object) -> str | None:
    return _clean_text(value) or None


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    # PostgreSQL text cannot contain NUL. Preserve every other character and
    # remove only the transport-invalid byte before normal whitespace cleanup.
    return str(value).replace("\x00", "").strip()


def yyyymmdd(value: object, field: str) -> date:
    text = required_text(value, field)
    try:
        return datetime.strptime(text, "%Y%m%d").date()
    except ValueError as exc:
        raise ProcessingError(f"invalid {field}: {text}") from exc


def optional_yyyymmdd(value: object, field: str) -> date | None:
    if optional_text(value) is None:
        return None
    return yyyymmdd(value, field)


def decimal_value(value: object, field: str, *, required: bool = False) -> Decimal | None:
    if value is None or value == "":
        if required:
            raise ProcessingError(f"field {field} is required")
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ProcessingError(f"invalid decimal {field}: {value!r}") from exc
    if not result.is_finite():
        raise ProcessingError(f"non-finite decimal {field}: {value!r}")
    return result


def scaled_decimal(
    value: object,
    field: str,
    factor: int,
    *,
    required: bool = False,
) -> Decimal | None:
    result = decimal_value(value, field, required=required)
    return result * factor if result is not None else None


def integer_value(value: object, field: str) -> int | None:
    decimal = decimal_value(value, field)
    if decimal is None:
        return None
    if decimal != decimal.to_integral_value():
        raise ProcessingError(f"field {field} must be an integer: {value!r}")
    return int(decimal)


def require_business_date(actual: date, expected: date | None, dataset: str) -> None:
    if expected is None:
        raise ProcessingError(f"{dataset} requires a task business date")
    if actual != expected:
        raise ProcessingError(
            f"{dataset} contains date {actual.isoformat()}, expected {expected.isoformat()}"
        )
