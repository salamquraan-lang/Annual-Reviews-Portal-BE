from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response, status

from app.dependencies import DbSession, require_roles
from app.models import Role, User
from app.routers.mappers import assignment_out, participant_out
from app.schemas.participants import (
    DepartmentChange,
    DepartmentRef,
    ManagerChange,
    ParticipantAdd,
    ParticipantDetail,
    ParticipantListQuery,
    ParticipantListResponse,
    RefreshBody,
    RefreshChanges,
    RefreshResponse,
    UserRef,
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


def _department_change_out(
    change: participants.Change | None,
) -> DepartmentChange | None:
    if change is None:
        return None
    return DepartmentChange(
        from_=DepartmentRef(id=change.old_id, name=change.old_name),
        to=DepartmentRef(id=change.new_id, name=change.new_name),
    )


def _manager_change_out(change: participants.Change | None) -> ManagerChange | None:
    if change is None:
        return None
    return ManagerChange(
        from_=(
            None
            if change.old_id is None
            else UserRef(id=change.old_id, name=change.old_name)
        ),
        to=(
            None
            if change.new_id is None
            else UserRef(id=change.new_id, name=change.new_name)
        ),
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


@router.post("/{cycle_id}/participants/{user_id}/refresh")
def refresh_participant(
    db: DbSession,
    admin: Admin,
    cycle_id: CycleId,
    user_id: UserId,
    body: RefreshBody | None = None,
) -> RefreshResponse:
    discard_ids = body.discard_assignment_ids if body else None
    result = participants.refresh_participant(
        db, admin.id, cycle_id, user_id, discard_ids
    )
    detail = _detail_out(result.detail)
    return RefreshResponse(
        user=detail.user,
        department=detail.department,
        manager=detail.manager,
        created_at=detail.created_at,
        assignments_as_reviewer=detail.assignments_as_reviewer,
        assignments_as_reviewee=detail.assignments_as_reviewee,
        changes=RefreshChanges(
            department=_department_change_out(result.department),
            manager=_manager_change_out(result.manager),
            created_assignment_ids=result.created_ids,
            removed_assignment_ids=result.removed_ids,
        ),
    )
