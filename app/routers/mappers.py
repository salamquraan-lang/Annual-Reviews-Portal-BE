# app/routers/mapping.py
"""Turn the rows the services return into response models."""

from sqlalchemy import Row

from app.schemas.participants import (
    AssignmentOut,
    DepartmentRef,
    ParticipantOut,
    UserRef,
    UserState,
)


def assignment_out(row: Row) -> AssignmentOut:
    return AssignmentOut(
        id=row.id,
        review_type=row.review_type,
        status=row.status,
        submitted_at=row.submitted_at,
        reviewer=UserRef(id=row.reviewer_id, name=row.reviewer_name),
        reviewee=UserRef(id=row.reviewee_id, name=row.reviewee_name),
    )


def participant_out(row: Row) -> ParticipantOut:
    return ParticipantOut(
        user=UserState(
            id=row.user_id, name=row.user_name, is_active=row.user_is_active
        ),
        department=DepartmentRef(id=row.department_id, name=row.department_name),
        manager=(
            None
            if row.manager_id is None
            else UserRef(id=row.manager_id, name=row.manager_name)
        ),
        created_at=row.created_at,
    )
