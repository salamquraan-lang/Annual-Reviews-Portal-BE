# app/routers/assignments.py
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from app.dependencies import DbSession, require_roles
from app.models import Role, User
from app.routers.mappers import assignment_out
from app.schemas.participants import AssignmentListQuery, AssignmentListResponse
from app.schemas.question_bank import MAX_ID
from app.services import assignments

# ASG-8: only admins list every assignment. The super admin is not allowed.
router = APIRouter(
    prefix="/api/cycles", dependencies=[Depends(require_roles(Role.admin))]
)

CycleId = Annotated[int, Path(ge=1, le=MAX_ID)]
Admin = Annotated[User, Depends(require_roles(Role.admin))]


@router.get("/{cycle_id}/assignments")
def list_assignments(
    db: DbSession,
    admin: Admin,
    cycle_id: CycleId,
    query: Annotated[AssignmentListQuery, Query()],
) -> AssignmentListResponse:
    result = assignments.list_assignments(db, cycle_id, admin.id, **query.model_dump())
    return AssignmentListResponse(
        items=[assignment_out(row) for row in result.items],
        page=query.page,
        page_size=query.page_size,
        total=result.total,
    )
