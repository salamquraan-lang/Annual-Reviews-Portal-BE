# app/routers/participants.py
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from app.dependencies import DbSession, require_roles
from app.models import Role, User
from app.routers.mappers import assignment_out, participant_out
from app.schemas.participants import (
    ParticipantDetail,
    ParticipantListQuery,
    ParticipantListResponse,
)
from app.schemas.question_bank import MAX_ID
from app.services import participants

# PART-2 and PART-3: only admins read participants. The super admin is not allowed.
router = APIRouter(
    prefix="/api/cycles", dependencies=[Depends(require_roles(Role.admin))]
)

CycleId = Annotated[int, Path(ge=1, le=MAX_ID)]
UserId = Annotated[int, Path(ge=1, le=MAX_ID)]
Admin = Annotated[User, Depends(require_roles(Role.admin))]


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
    detail = participants.get_participant(db, cycle_id, user_id, admin.id)
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
