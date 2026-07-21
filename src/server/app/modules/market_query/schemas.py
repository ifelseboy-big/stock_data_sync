from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SecurityType(StrEnum):
    STOCK = "stock"
    ETF = "etf"
    INDEX = "index"


class ScreenOperator(StrEnum):
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    BETWEEN = "between"
    IN = "in"
    IS_NULL = "is_null"


ScreenString = Annotated[str, Field(max_length=128)]
ScreenScalar = ScreenString | int | Decimal
ScreenList = Annotated[list[ScreenScalar], Field(max_length=100)]
UniverseString = Annotated[str, Field(min_length=1, max_length=64)]


class ScreenFilter(StrictModel):
    field: str = Field(min_length=1, max_length=64)
    operator: ScreenOperator
    value: ScreenScalar | bool | ScreenList | None = None

    @model_validator(mode="after")
    def validate_value(self) -> "ScreenFilter":
        if self.operator in {ScreenOperator.BETWEEN, ScreenOperator.IN}:
            if not isinstance(self.value, list):
                raise ValueError(f"{self.operator.value} requires a list value")
            if self.operator is ScreenOperator.BETWEEN and len(self.value) != 2:
                raise ValueError("between requires exactly two values")
            if self.operator is ScreenOperator.IN and not self.value:
                raise ValueError("in requires at least one value")
            if len(self.value) > 100:
                raise ValueError("filter list cannot contain more than 100 values")
        elif self.operator is ScreenOperator.IS_NULL:
            if not isinstance(self.value, bool):
                raise ValueError("is_null requires a boolean value")
        elif self.value is None or isinstance(self.value, list):
            raise ValueError(f"{self.operator.value} requires one scalar value")
        values = self.value if isinstance(self.value, list) else [self.value]
        if any(isinstance(value, str) and len(value) > 128 for value in values):
            raise ValueError("filter string values cannot exceed 128 characters")
        return self


class ScreenSort(StrictModel):
    field: str = Field(min_length=1, max_length=64)
    direction: Literal["asc", "desc"] = "desc"


def _default_list_status() -> list[Literal["L", "D", "P", "G"]]:
    return ["L"]


class ScreenUniverse(StrictModel):
    exchanges: list[UniverseString] | None = Field(default=None, max_length=100)
    markets: list[UniverseString] | None = Field(default=None, max_length=100)
    industries: list[UniverseString] | None = Field(default=None, max_length=100)
    list_status: list[Literal["L", "D", "P", "G"]] = Field(
        default_factory=_default_list_status, max_length=4
    )
    is_hs: list[Literal["N", "H", "S"]] | None = Field(default=None, max_length=3)
    exclude_suspended: bool = True

    @model_validator(mode="after")
    def validate_strings(self) -> "ScreenUniverse":
        values = (self.exchanges or []) + (self.markets or []) + (self.industries or [])
        if any(not value or len(value) > 64 for value in values):
            raise ValueError("universe values must contain 1-64 characters")
        return self


class DateRange(StrictModel):
    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def validate_range(self) -> "DateRange":
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date cannot be later than end_date")
        return self


class QueryErrorPayload(StrictModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class QueryEnvelope(StrictModel):
    ok: bool
    data: Any = None
    meta: dict[str, Any] = Field(default_factory=dict)
    error: QueryErrorPayload | None = None
