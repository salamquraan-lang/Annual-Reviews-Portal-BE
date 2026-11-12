from datetime import UTC, datetime
from typing import Annotated

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)


def _as_utc_iso(value: datetime) -> str:
    # The database stores naive UTC times. Add the Z so clients don't read them as local time.
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return (
        value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    )


UtcDateTime = Annotated[datetime, PlainSerializer(_as_utc_iso, return_type=str)]


def _check_department_name(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("Name is required.")
    if len(value) > 120:
        raise ValueError("Name must be at most 120 characters.")
    return value


DepartmentName = Annotated[str, AfterValidator(_check_department_name)]


class DepartmentChildOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    name: str
    is_active: bool = Field(serialization_alias="isActive")


class DepartmentOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    name: str
    parent_id: int | None = Field(serialization_alias="parentId")
    is_active: bool = Field(serialization_alias="isActive")
    created_at: UtcDateTime = Field(serialization_alias="createdAt")
    updated_at: UtcDateTime = Field(serialization_alias="updatedAt")


class DepartmentDetail(DepartmentOut):
    children: list[DepartmentChildOut]
    active_user_count: int = Field(serialization_alias="activeUserCount")


class DepartmentTreeNode(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    name: str
    is_active: bool = Field(serialization_alias="isActive")
    children: list["DepartmentTreeNode"]


class DepartmentListResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    items: list[DepartmentOut]
    page: int
    page_size: int = Field(serialization_alias="pageSize")
    total: int


class DepartmentListQuery(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    is_active: bool | None = Field(default=None, alias="isActive")
    parent_id: int | None = Field(default=None, alias="parentId", ge=1)
    top_level: bool = Field(default=False, alias="topLevel")
    search: str | None = Field(default=None, max_length=100)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100, alias="pageSize")

    @model_validator(mode="after")
    def _parent_or_top_level(self) -> "DepartmentListQuery":
        if self.parent_id is not None and self.top_level:
            raise ValueError("Use either parentId or topLevel, not both.")
        return self


class DepartmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: DepartmentName
    parent_id: StrictInt | None = Field(default=None, alias="parentId", ge=1)


class DepartmentUpdate(BaseModel):
    """Body of PATCH /api/departments/{id}. Only the fields that are sent are changed."""

    model_config = ConfigDict(extra="forbid")

    name: DepartmentName | None = None
    is_active: StrictBool | None = Field(default=None, alias="isActive")

    @field_validator("name", "is_active", mode="before")
    @classmethod
    def _not_null(cls, value: object) -> object:
        # Runs only for fields that were sent. Neither field can be empty.
        if value is None:
            raise ValueError("This field cannot be empty.")
        return value

    @model_validator(mode="after")
    def _check_sent_fields(self) -> "DepartmentUpdate":
        if not self.model_fields_set:
            raise ValueError("Send at least one field to change.")
        return self


class DepartmentChangeParent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Required, but it may be null. Null means top level.
    parent_id: StrictInt | None = Field(alias="parentId", ge=1)


class DepartmentMoveUsers(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_department_id: StrictInt = Field(alias="targetDepartmentId", ge=1)


class MoveUsersResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    moved_count: int = Field(serialization_alias="movedCount")
