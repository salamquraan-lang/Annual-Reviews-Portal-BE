from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response, status

from app.dependencies import DbSession, require_roles
from app.models import Role, User
from app.routers.mappers import assignment_out, participant_out
from app.schemas.participants import (
    ParticipantAdd,
    ParticipantDetail,
    ParticipantListQuery,
    ParticipantListResponse,
)
from app.schemas.question_bank import MAX_ID
from app.services import participants

# PART-2, PART-3 and PART-5: only admins use these. The super admin is not allowed.
router = APIRouter(
    prefix="/api/cycles", dependencies=[Depends(require_roles(Role.admin))]
)

CycleId = Annotated[int, Path(ge=1, le=MAX_ID)]
UserId = Annotated[int, Path(ge=1, le=MAX_ID)]
Admin = Annotated[User, Depends(require_roles(Role.admin))]


def _detail_out(detail: participants.ParticipantDetail) -> ParticipantDetail:
    person = participant_out(detail.participant)
    return ParticipantDetail(
        user=person.user,
        department=person.department,
        manager=person.manager,
        created_at=person.created_at,
        assignments_as_reviewer=[
            assignment_out(row) for row in detail.assignments_as_reviewer
        ],
        assignments_as_reviewee=[
            assignment_out(row) for row in detail.assignments_as_reviewee
        ],
    )


@router.get("/{cycle_id}/participants")
def list_participants(
    db: DbSession,
    cycle_id: CycleId,
    query: Annotated[ParticipantListQuery, Query()],
) -> ParticipantListResponse:
    result = participants.list_participants(db, cycle_id, **query.model_dump())
    return ParticipantListResponse(
        items=[participant_out(row) for row in result.items],
        page=query.page,
        page_size=query.page_size,
        total=result.total,
    )


@router.get("/{cycle_id}/participants/{user_id}")
def get_participant(
    db: DbSession, admin: Admin, cycle_id: CycleId, user_id: UserId
) -> ParticipantDetail:
    return _detail_out(participants.get_participant(db, cycle_id, user_id, admin.id))


@router.post("/{cycle_id}/participants", status_code=status.HTTP_201_CREATED)
def add_participant(
    db: DbSession,
    admin: Admin,
    cycle_id: CycleId,
    body: ParticipantAdd,
    response: Response,
) -> ParticipantDetail:
    detail = participants.add_participant(db, admin.id, cycle_id, body.user_id)
    response.headers["Location"] = f"/api/cycles/{cycle_id}/participants/{body.user_id}"
    return _detail_out(detail)
