# app/schemas/my_work.py
from pydantic import BaseModel, ConfigDict, Field

from app.models import AssignmentStatus, CycleStatus, ReviewType
from app.schemas.departments import UtcDateTime
from app.schemas.participants import UserRef, UserState

# ------------------------------------------------------- my assignments


class CycleBrief(BaseModel):
    id: int
    name: str
    status: CycleStatus


class MyAssignmentOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    review_type: ReviewType = Field(serialization_alias="reviewType")
    status: AssignmentStatus
    # Null unless the status is submitted.
    submitted_at: UtcDateTime | None = Field(serialization_alias="submittedAt")
    reviewee: UserRef


class MyAssignmentsResponse(BaseModel):
    cycle: CycleBrief
    items: list[MyAssignmentOut]


# ---------------------------------------------------------------- my team


class TeamManagerReview(BaseModel):
    """The review the caller writes about this person."""

    model_config = ConfigDict(populate_by_name=True)

    assignment_id: int = Field(serialization_alias="assignmentId")
    status: AssignmentStatus
    submitted_at: UtcDateTime | None = Field(serialization_alias="submittedAt")


class PeerCounts(BaseModel):
    chosen: int
    submitted: int


class TeamPeerOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    assignment_id: int = Field(serialization_alias="assignmentId")
    peer: UserRef
    status: AssignmentStatus


class PeerCountsWithItems(PeerCounts):
    items: list[TeamPeerOut]


class TeamMemberOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user: UserState
    self_status: AssignmentStatus = Field(serialization_alias="selfStatus")
    manager: TeamManagerReview
    peers: PeerCounts


class TeamListResponse(BaseModel):
    items: list[TeamMemberOut]


class TeamMemberDetail(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user: UserState
    self_status: AssignmentStatus = Field(serialization_alias="selfStatus")
    manager: TeamManagerReview
    peers: PeerCountsWithItems
