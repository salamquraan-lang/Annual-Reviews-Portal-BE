# app/services/assignments.py
"""Reads of assignments, and the rule that hides some of them (P11)."""

from dataclasses import dataclass

from sqlalchemy import ColumnElement, Row, and_, func, not_, or_, select
from sqlalchemy.orm import Session, aliased

from app.models import (
    Assignment,
    AssignmentStatus,
    CycleParticipant,
    ReviewType,
    User,
)
from app.services import cycles


@dataclass
class AssignmentPage:
    items: list[Row]
    total: int


def visible_to(caller_id: int) -> ColumnElement[bool]:
    """P11: nobody sees the peer and upward reviews written about themself.

    It applies to every list, count, filter and detail, and to admins as well.
    """
    return not_(
        and_(
            Assignment.reviewee_id == caller_id,
            Assignment.review_type.in_([ReviewType.peer, ReviewType.upward]),
        )
    )


def _query(cycle_id: int, caller_id: int):
    """One row per assignment, with both people's current names."""
    reviewer = aliased(User)
    reviewee = aliased(User)
    return (
        select(
            Assignment.id,
            Assignment.review_type,
            Assignment.status,
            Assignment.submitted_at,
            reviewer.id.label("reviewer_id"),
            reviewer.name.label("reviewer_name"),
            reviewee.id.label("reviewee_id"),
            reviewee.name.label("reviewee_name"),
        )
        .select_from(Assignment)
        .join(reviewer, reviewer.id == Assignment.reviewer_id)
        .join(reviewee, reviewee.id == Assignment.reviewee_id)
        .where(Assignment.cycle_id == cycle_id, visible_to(caller_id))
    )


def list_assignments(
    db: Session,
    cycle_id: int,
    caller_id: int,
    *,
    review_type: ReviewType | None = None,
    status: AssignmentStatus | None = None,
    reviewer_id: int | None = None,
    reviewee_id: int | None = None,
    user_id: int | None = None,
    department_id: int | None = None,
    page: int = 1,
    page_size: int = 20,
) -> AssignmentPage:
    cycles.get_cycle(db, cycle_id)

    query = _query(cycle_id, caller_id)
    if review_type is not None:
        query = query.where(Assignment.review_type == review_type)
    if status is not None:
        query = query.where(Assignment.status == status)
    if reviewer_id is not None:
        query = query.where(Assignment.reviewer_id == reviewer_id)
    if reviewee_id is not None:
        query = query.where(Assignment.reviewee_id == reviewee_id)
    if user_id is not None:
        query = query.where(
            or_(Assignment.reviewer_id == user_id, Assignment.reviewee_id == user_id)
        )
    if department_id is not None:
        # The department of the reviewer as it was when the cycle opened.
        query = query.join(
            CycleParticipant,
            and_(
                CycleParticipant.cycle_id == Assignment.cycle_id,
                CycleParticipant.user_id == Assignment.reviewer_id,
            ),
        ).where(CycleParticipant.department_id == department_id)

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.execute(
        query.order_by(Assignment.id).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return AssignmentPage(items=list(rows), total=total)


def of_participant(
    db: Session, cycle_id: int, user_id: int, caller_id: int
) -> tuple[list[Row], list[Row]]:
    """What a person writes, and what is written about them. Not paged."""
    query = _query(cycle_id, caller_id).order_by(Assignment.id)
    written = db.execute(query.where(Assignment.reviewer_id == user_id)).all()
    received = db.execute(query.where(Assignment.reviewee_id == user_id)).all()
    return list(written), list(received)
