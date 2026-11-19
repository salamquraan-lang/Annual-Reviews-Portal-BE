"""The people of a cycle: who is ready to take part, and the snapshot taken at open.

Opening a cycle freezes who takes part and who reviews whom (C6, P1, PART-1).
Later changes to a user's department or manager do not touch an opened cycle.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import Select, func, insert, literal, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased
from sqlalchemy.sql.elements import ColumnElement

from app.errors import AppError
from app.models import Assignment, Cycle, CycleParticipant, ReviewType, User

USER_WITHOUT_DEPARTMENT = "USER_WITHOUT_DEPARTMENT"
MANAGER_NOT_ACTIVE = "MANAGER_NOT_ACTIVE"
TRY_AGAIN = "TRY_AGAIN"

MYSQL_BAD_NULL = 1048
MYSQL_FOREIGN_KEY_FAILS = 1452

# A cycle with 5,000 broken users must not produce a 5,000-line answer.
LISTED_PER_CODE = 20


@dataclass
class SnapshotProblem:
    code: str
    message: str
    user_id: int | None = None


# -------------------------------------------------------------- readiness


def readiness_problems(db: Session) -> list[SnapshotProblem]:
    """What would stop the snapshot. Each code lists at most 20 users, then a count."""
    manager = aliased(User)
    active = User.is_active.is_(True)

    no_department = select(User.id, User.name).where(
        active, User.department_id.is_(None)
    )
    inactive_manager = (
        select(User.id, User.name, manager.name.label("manager_name"))
        .join(manager, manager.id == User.manager_id)
        .where(active, manager.is_active.is_(False))
    )

    return [
        *_problems(
            db,
            no_department,
            USER_WITHOUT_DEPARTMENT,
            lambda row: f"{row.name} has no department.",
            "more users have no department.",
        ),
        *_problems(
            db,
            inactive_manager,
            MANAGER_NOT_ACTIVE,
            lambda row: f"{row.name}'s manager, {row.manager_name}, is not active.",
            "more users have a manager who is not active.",
        ),
    ]


def _problems(
    db: Session, query: Select, code: str, describe, more: str
) -> list[SnapshotProblem]:
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.execute(query.order_by(User.id).limit(LISTED_PER_CODE)).all()
    problems = [SnapshotProblem(code, describe(row), user_id=row.id) for row in rows]
    if total > LISTED_PER_CODE:
        problems.append(SnapshotProblem(code, f"{total - LISTED_PER_CODE} {more}"))
    return problems


# --------------------------------------------------------------- snapshot


def take_snapshot(
    db: Session, cycle: Cycle, user_ids: Sequence[int] | None = None
) -> None:
    """Make active users participants, then create their self, manager and upward reviews.

    Runs inside the caller's transaction and never commits. With user_ids, only those users
    are handled. Add person and refresh use that later.
    """
    people: list[ColumnElement[bool]] = [User.is_active.is_(True)]
    if user_ids is not None:
        people.append(User.id.in_(user_ids))
    has_manager = [*people, User.manager_id.is_not(None)]

    try:
        db.execute(_insert_participants(cycle.id, people))
        db.execute(
            _insert_assignments(cycle.id, people, ReviewType.self_, User.id, User.id)
        )
        # A user with no manager has no manager review and no upward review.
        db.execute(
            _insert_assignments(
                cycle.id, has_manager, ReviewType.manager, User.manager_id, User.id
            )
        )
        db.execute(
            _insert_assignments(
                cycle.id, has_manager, ReviewType.upward, User.id, User.manager_id
            )
        )
    except IntegrityError as exc:
        db.rollback()
        if _data_changed(exc):
            raise AppError(
                409, TRY_AGAIN, "Users changed while the cycle was opening. Try again."
            ) from None
        raise


def _insert_participants(cycle_id: int, conditions: list[ColumnElement[bool]]):
    return insert(CycleParticipant).from_select(
        [
            CycleParticipant.cycle_id,
            CycleParticipant.user_id,
            CycleParticipant.department_id,
            CycleParticipant.manager_id,
        ],
        select(literal(cycle_id), User.id, User.department_id, User.manager_id).where(
            *conditions
        ),
    )


def _insert_assignments(
    cycle_id: int,
    conditions: list[ColumnElement[bool]],
    review_type: ReviewType,
    reviewer: ColumnElement,
    reviewee: ColumnElement,
):
    return insert(Assignment).from_select(
        [
            Assignment.cycle_id,
            Assignment.reviewer_id,
            Assignment.reviewee_id,
            Assignment.review_type,
        ],
        select(
            literal(cycle_id),
            reviewer,
            reviewee,
            literal(review_type, Assignment.review_type.type),
        ).where(*conditions),
    )


def _data_changed(exc: IntegrityError) -> bool:
    """True for the two errors that mean a user changed after the readiness check."""
    orig = exc.orig
    return (
        orig is not None
        and bool(orig.args)
        and orig.args[0] in (MYSQL_BAD_NULL, MYSQL_FOREIGN_KEY_FAILS)
    )
