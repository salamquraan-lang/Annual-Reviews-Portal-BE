from datetime import date
from typing import Annotated

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.models import CycleStatus, CycleType


def _check_cycle_name(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("Name is required.")
    if len(value) > 120:
        raise ValueError("Name must be at most 120 characters.")
    return value


def _require_date_text(value: object) -> object:
    # Without this, pydantic would read a number such as 20260101 as a timestamp.
    if not isinstance(value, str):
        raise ValueError("Enter a date as YYYY-MM-DD.")
    return value.strip()


CycleName = Annotated[str, AfterValidator(_check_cycle_name)]
CycleYear = Annotated[int, Field(ge=2000, le=2100)]
IsoDate = Annotated[date, BeforeValidator(_require_date_text)]


# ---------------------------------------------------------------- requests


class CycleCreate(BaseModel):
    """Body of CYC-1 (create) and CYC-2 (copy)."""

    model_config = ConfigDict(extra="forbid")

    name: CycleName
    type: CycleType
    year: CycleYear
    start_date: IsoDate = Field(alias="startDate")
    end_date: IsoDate = Field(alias="endDate")

    @model_validator(mode="after")
    def _dates_in_order(self) -> "CycleCreate":
        if self.start_date > self.end_date:
            raise ValueError("Start date must not be after end date.")
        return self


class CycleUpdate(BaseModel):
    """Body of CYC-6. Only the fields that are sent are changed."""

    model_config = ConfigDict(extra="forbid")

    name: CycleName | None = None
    type: CycleType | None = None
    year: CycleYear | None = None
    start_date: IsoDate | None = Field(default=None, alias="startDate")
    end_date: IsoDate | None = Field(default=None, alias="endDate")

    @field_validator("name", "type", "year", "start_date", "end_date", mode="before")
    @classmethod
    def _not_null(cls, value: object) -> object:
        # Runs only for fields that were sent. No column of a cycle can be empty.
        if value is None:
            raise ValueError("This field cannot be empty.")
        return value

    @model_validator(mode="after")
    def _check_sent_fields(self) -> "CycleUpdate":
        if not self.model_fields_set:
            raise ValueError("Send at least one field to change.")
        # With only one date sent, the service compares it with the stored one.
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("Start date must not be after end date.")
        return self


class CycleEndDate(BaseModel):
    """Body of CYC-7."""

    model_config = ConfigDict(extra="forbid")

    end_date: IsoDate = Field(alias="endDate")


class CycleListQuery(BaseModel):
    """Query string of CYC-3."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    status: CycleStatus | None = None
    year: CycleYear | None = None
    type: CycleType | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100, alias="pageSize")


# --------------------------------------------------------------- responses


class CycleOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    name: str
    type: CycleType
    year: int
    start_date: date = Field(serialization_alias="startDate")
    end_date: date = Field(serialization_alias="endDate")
    status: CycleStatus


class CycleListResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    items: list[CycleOut]
    page: int
    page_size: int = Field(serialization_alias="pageSize")
    total: int


class ReadinessProblem(BaseModel):
    code: str
    message: str


class ReadinessResponse(BaseModel):
    ready: bool
    problems: list[ReadinessProblem]


class FinalizeWarning(BaseModel):
    """A participant who has no result and gets no reward proposal (S13)."""

    model_config = ConfigDict(populate_by_name=True)

    code: str
    user_id: int = Field(serialization_alias="userId")
    message: str


class FinalizeResponse(CycleOut):
    warnings: list[FinalizeWarning]
