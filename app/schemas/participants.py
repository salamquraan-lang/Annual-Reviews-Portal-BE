# app/schemas/participants.py
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from app.models import AssignmentStatus, ReviewType
from app.schemas.departments import UtcDateTime
from app.schemas.question_bank import MAX_ID, BodyId

# Query strings are text, so ids here are not strict integers.
QueryId = Annotated[int, Field(ge=1, le=MAX_ID)]


def _check_search(value: str) -> str:
    value = value.strip()
    if len(value) > 100:
        raise ValueError("Search must be at most 100 characters.")
    return value


SearchText = Annotated[str, AfterValidator(_check_search)]


# ---------------------------------------------------------------- queries


class ParticipantListQuery(BaseModel):
    """Query string of PART-2."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    department_id: QueryId | None = Field(default=None, alias="departmentId")
    manager_id: QueryId | None = Field(default=None, alias="managerId")
    search: SearchText | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100, alias="pageSize")


class AssignmentListQuery(BaseModel):
    """Query string of ASG-8. The filters combine with AND."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    review_type: ReviewType | None = Field(default=None, alias="reviewType")
    status: AssignmentStatus | None = None
    reviewer_id: QueryId | None = Field(default=None, alias="reviewerId")
    reviewee_id: QueryId | None = Field(default=None, alias="revieweeId")
    # Matches the person as reviewer or as reviewee.
    user_id: QueryId | None = Field(default=None, alias="userId")
    # The reviewer's department as it was when the cycle opened.
    department_id: QueryId | None = Field(default=None, alias="departmentId")
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100, alias="pageSize")


# ------------------------------------------------------------------ body


class ParticipantAdd(BaseModel):
    """The user to add to the open cycle."""

    model_config = ConfigDict(extra="forbid")

    user_id: BodyId = Field(alias="userId")


# ----------------------------------------------------------- shared types


class UserRef(BaseModel):
    id: int
    name: str


class UserState(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    name: str
    is_active: bool = Field(serialization_alias="isActive")


class DepartmentRef(BaseModel):
    id: int
    name: str


class AssignmentOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    review_type: ReviewType = Field(serialization_alias="reviewType")
    status: AssignmentStatus
    # Null unless the status is submitted.
    submitted_at: UtcDateTime | None = Field(serialization_alias="submittedAt")
    reviewer: UserRef
    reviewee: UserRef


class ParticipantOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user: UserState
    department: DepartmentRef
    # Null when the participant had no manager when the cycle opened.
    manager: UserRef | None
    created_at: UtcDateTime = Field(serialization_alias="createdAt")


class ParticipantDetail(ParticipantOut):
    assignments_as_reviewer: list[AssignmentOut] = Field(
        serialization_alias="assignmentsAsReviewer"
    )
    assignments_as_reviewee: list[AssignmentOut] = Field(
        serialization_alias="assignmentsAsReviewee"
    )


# -------------------------------------------------------- paged responses


class ParticipantListResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    items: list[ParticipantOut]
    page: int
    page_size: int = Field(serialization_alias="pageSize")
    total: int


class AssignmentListResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    items: list[AssignmentOut]
    page: int
    page_size: int = Field(serialization_alias="pageSize")
    total: int
