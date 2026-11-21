from pydantic import BaseModel, ConfigDict, Field

from app.models import AssignmentStatus
from app.schemas.participants import DepartmentRef, QueryId, SearchText, UserRef
from app.schemas.question_bank import BodyId

# ---------------------------------------------------------------- queries


class CandidateListQuery(BaseModel):
    """Query string of ASG-4."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    search: SearchText | None = None
    # The candidate's department as it was when the cycle opened.
    department_id: QueryId | None = Field(default=None, alias="departmentId")
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100, alias="pageSize")


class PeerGapListQuery(BaseModel):
    """Query string of ASG-7."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    # The reviewee's department and manager as they were when the cycle opened.
    department_id: QueryId | None = Field(default=None, alias="departmentId")
    manager_id: QueryId | None = Field(default=None, alias="managerId")
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100, alias="pageSize")


# ------------------------------------------------------------------ body


class PeerCreate(BaseModel):
    """Body of ASG-5."""

    model_config = ConfigDict(extra="forbid")

    peer_id: BodyId = Field(alias="peerId")


# -------------------------------------------------------------- responses


class PeerOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    assignment_id: int = Field(serialization_alias="assignmentId")
    peer: UserRef
    status: AssignmentStatus


class PeerListResponse(BaseModel):
    """The answer of ASG-5 and ASG-6: the whole list after the change."""

    reviewee: UserRef
    count: int
    items: list[PeerOut]


class CandidateOut(BaseModel):
    user: UserRef
    department: DepartmentRef


class CandidateListResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    items: list[CandidateOut]
    page: int
    page_size: int = Field(serialization_alias="pageSize")
    total: int


class PeerGapOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user: UserRef
    department: DepartmentRef
    manager: UserRef
    peer_count: int = Field(serialization_alias="peerCount")
    # How many peers are still needed to reach three.
    missing: int


class PeerGapListResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    items: list[PeerGapOut]
    page: int
    page_size: int = Field(serialization_alias="pageSize")
    total: int
