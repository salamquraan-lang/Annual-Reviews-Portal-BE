"""The people of a cycle: the list, one person's page, and adding a person."""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import Row, and_, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.errors import AppError, bad_request, forbidden
from app.models import (
    Assignment,
    AssignmentStatus,
    CycleParticipant,
    Department,
    ReviewType,
    User,
)

PARTICIPANT_NOT_FOUND = "PARTICIPANT_NOT_FOUND"
USER_NOT_FOUND = "USER_NOT_FOUND"
USER_NOT_ACTIVE = "USER_NOT_ACTIVE"
ALREADY_PARTICIPANT = "ALREADY_PARTICIPANT"
MANAGER_NOT_PARTICIPANT = "MANAGER_NOT_PARTICIPANT"
REFRESH_NOT_ALLOWED = "REFRESH_NOT_ALLOWED"
DISCARD_NOT_CONFIRMED = "DISCARD_NOT_CONFIRMED"


@dataclass
class ParticipantPage:
    items: list[Row]
    total: int


@dataclass
class ParticipantDetail:
    participant: Row
    assignments_as_reviewer: list[Row]
    assignments_as_reviewee: list[Row]


@dataclass
class Change:
    """One value before and after a refresh. Either side can be missing for a manager."""

    old_id: int | None
    old_name: str | None
    new_id: int | None
    new_name: str | None


@dataclass
class RefreshResult:
    detail: ParticipantDetail
    department: Change | None
    manager: Change | None
    created_ids: list[int]
    removed_ids: list[int]

def _query(cycle_id: int):
    """One row per participant. Ids are the snapshot values. Names are the current ones."""
    manager = aliased(User)
    return (
        select(
            User.id.label("user_id"),
            User.name.label("user_name"),
            User.is_active.label("user_is_active"),
            Department.id.label("department_id"),
            Department.name.label("department_name"),
            manager.id.label("manager_id"),
            manager.name.label("manager_name"),
            CycleParticipant.created_at,
        )
        .select_from(CycleParticipant)
        .join(User, User.id == CycleParticipant.user_id)
        .join(Department, Department.id == CycleParticipant.department_id)
        .outerjoin(manager, manager.id == CycleParticipant.manager_id)
        .where(CycleParticipant.cycle_id == cycle_id)
    )


def list_participants(
    db: Session,
    cycle_id: int,
    *,
    department_id: int | None = None,
    manager_id: int | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> ParticipantPage:
    cycles.get_cycle(db, cycle_id)

    query = _query(cycle_id)
    if department_id is not None:
        query = query.where(CycleParticipant.department_id == department_id)
    if manager_id is not None:
        query = query.where(CycleParticipant.manager_id == manager_id)
    if search:
        # autoescape makes % and _ plain characters.
        query = query.where(User.name.icontains(search, autoescape=True))

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.execute(
        query.order_by(User.name, User.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return ParticipantPage(items=list(rows), total=total)


def get_participant(
    db: Session, cycle_id: int, user_id: int, caller_id: int
) -> ParticipantDetail:
    cycles.get_cycle(db, cycle_id)

    row = db.execute(
        _query(cycle_id).where(CycleParticipant.user_id == user_id)
    ).first()
    if row is None:
        raise AppError(404, PARTICIPANT_NOT_FOUND, "Participant not found.")

    written, received = assignments.of_participant(db, cycle_id, user_id, caller_id)
    return ParticipantDetail(
        participant=row,
        assignments_as_reviewer=written,
        assignments_as_reviewee=received,
    )


def _audit(
    db: Session,
    actor_id: int,
    action: str,
    table: str,
    record_id: int,
    details: dict | None = None,
) -> None:
    # TODO(P12): write the audit_log row here, inside the caller's transaction (before
    # commit). The audit log is cluster 11. Same stub as in services/peers.py.
    return None


def add_participant(
    db: Session, actor_id: int, cycle_id: int, user_id: int
) -> ParticipantDetail:
    """A person who joined after the cycle opened takes part from now on (PART-5).

    The snapshot row comes from the user's current department and manager. Nobody else's
    snapshot changes. The checks run in the order of the design, and the first failure wins.
    """
    cycle = cycles.lock_cycle(db, cycle_id)
    cycles.require_open(cycle)

    user = db.scalar(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None:
        raise bad_request(USER_NOT_FOUND, "No user has this id.")
    if not user.is_active:
        raise AppError(409, USER_NOT_ACTIVE, "This user is not active.")
    if db.get(CycleParticipant, (cycle_id, user_id)) is not None:
        raise AppError(
            409, ALREADY_PARTICIPANT, "This user already takes part in the cycle."
        )
    if user.department_id is None:
        raise AppError(
            409, snapshot.USER_WITHOUT_DEPARTMENT, "This user has no department."
        )
    if user.manager_id is not None:
        if db.get(CycleParticipant, (cycle_id, user.manager_id)) is None:
            raise AppError(
                409,
                MANAGER_NOT_PARTICIPANT,
                "This user's manager does not take part in the cycle.",
            )
        manager = db.get(User, user.manager_id, populate_existing=True)
        if manager is None or not manager.is_active:
            raise AppError(
                409, snapshot.MANAGER_NOT_ACTIVE, "This user's manager is not active."
            )

    snapshot.take_snapshot(db, cycle, [user_id])

    # A self review, and a manager and an upward review when there is a manager.
    created = 1 if user.manager_id is None else 3
    _audit(
        db,
        actor_id,
        "add_participant",
        "cycle_participants",
        user_id,
        {
            "cycle_id": cycle_id,
            "department_id": user.department_id,
            "manager_id": user.manager_id,
            "assignments": created,
        },
    )
    db.commit()
    return get_participant(db, cycle_id, user_id, actor_id)

# ---------------------------------------------------------------- refresh


def refresh_participant(
    db: Session,
    actor_id: int,
    cycle_id: int,
    user_id: int,
    discard_ids: Sequence[int] | None = None,
) -> RefreshResult:
    """Copy a person's current department and manager into the cycle (PART-4).

    A new manager means new manager and upward reviews. The old ones are removed, and so
    are peer reviews that the new manager relationship makes impossible. Reviews that hold
    work are removed only after the admin has seen them and sent their ids back.
    """
    cycle = cycles.lock_cycle(db, cycle_id)
    cycles.require_open(cycle)

    participant = db.get(
        CycleParticipant, (cycle_id, user_id), populate_existing=True
    )
    if participant is None:
        raise AppError(404, PARTICIPANT_NOT_FOUND, "Participant not found.")

    user = db.execute(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    if not user.is_active:
        raise AppError(409, USER_NOT_ACTIVE, "This user is not active.")
    if user.department_id is None:
        raise AppError(
            409, snapshot.USER_WITHOUT_DEPARTMENT, "This user has no department."
        )

    old_department_id = participant.department_id
    old_manager_id = participant.manager_id
    new_department_id = user.department_id
    new_manager_id = user.manager_id
    department_changed = new_department_id != old_department_id
    manager_changed = new_manager_id != old_manager_id

    # A manager who did not change was checked when the snapshot was taken. Only a new
    # manager has to be able to write the reviews.
    if manager_changed and new_manager_id is not None:
        if db.get(CycleParticipant, (cycle_id, new_manager_id)) is None:
            raise AppError(
                409,
                MANAGER_NOT_PARTICIPANT,
                "This user's new manager does not take part in the cycle.",
            )
        manager = db.get(User, new_manager_id, populate_existing=True)
        if manager is None or not manager.is_active:
            raise AppError(
                409,
                snapshot.MANAGER_NOT_ACTIVE,
                "This user's new manager is not active.",
            )

    if not department_changed and not manager_changed:
        return RefreshResult(
            detail=get_participant(db, cycle_id, user_id, actor_id),
            department=None,
            manager=None,
            created_ids=[],
            removed_ids=[],
        )

    to_remove: list[Assignment] = []
    if manager_changed:
        # The list below names reviewers. Anyone who is part of the change could learn who
        # reviews them, so another admin has to do it. This check comes first (P11).
        involved = {user_id, old_manager_id, new_manager_id} - {None}
        if actor_id in involved:
            raise forbidden(
                REFRESH_NOT_ALLOWED,
                "You are part of this change. Another admin must refresh this person.",
            )
        to_remove = _assignments_to_remove(
            db, cycle_id, user_id, old_manager_id, new_manager_id
        )
        _require_discard_confirmed(db, to_remove, discard_ids)

    removed = [_describe(db, assignment) for assignment in to_remove]
    for assignment in to_remove:
        db.delete(assignment)
    db.flush()

    participant.department_id = new_department_id
    participant.manager_id = new_manager_id

    created: list[Assignment] = []
    if manager_changed and new_manager_id is not None:
        created = [
            Assignment(
                cycle_id=cycle_id,
                reviewer_id=new_manager_id,
                reviewee_id=user_id,
                review_type=ReviewType.manager,
                status=AssignmentStatus.pending,
            ),
            Assignment(
                cycle_id=cycle_id,
                reviewer_id=user_id,
                reviewee_id=new_manager_id,
                review_type=ReviewType.upward,
                status=AssignmentStatus.pending,
            ),
        ]
        db.add_all(created)
    db.flush()

    department = (
        _change(db, Department, old_department_id, new_department_id)
        if department_changed
        else None
    )
    manager = (
        _change(db, User, old_manager_id, new_manager_id) if manager_changed else None
    )
    created_ids = [assignment.id for assignment in created]
    removed_ids = [row["assignment_id"] for row in removed]

    _audit(
        db,
        actor_id,
        "refresh_participant",
        "cycle_participants",
        user_id,
        {
            "cycle_id": cycle_id,
            "department": [old_department_id, new_department_id],
            "manager": [old_manager_id, new_manager_id],
            "created_assignment_ids": created_ids,
            "removed": removed,
        },
    )
    db.commit()
    return RefreshResult(
        detail=get_participant(db, cycle_id, user_id, actor_id),
        department=department,
        manager=manager,
        created_ids=created_ids,
        removed_ids=removed_ids,
    )


def _assignments_to_remove(
    db: Session,
    cycle_id: int,
    user_id: int,
    old_manager_id: int | None,
    new_manager_id: int | None,
) -> list[Assignment]:
    """The reviews that stop making sense when the manager changes, locked by id."""
    pairs: list[tuple[int, int, ReviewType]] = []
    if old_manager_id is not None:
        pairs.append((old_manager_id, user_id, ReviewType.manager))
        pairs.append((user_id, old_manager_id, ReviewType.upward))
    if new_manager_id is not None:
        # The new manager cannot be a peer of the person, and the person cannot be a peer
        # of the new manager (a manager and a direct report are never peers).
        pairs.append((new_manager_id, user_id, ReviewType.peer))
        pairs.append((user_id, new_manager_id, ReviewType.peer))

    matches = [
        and_(
            Assignment.reviewer_id == reviewer,
            Assignment.reviewee_id == reviewee,
            Assignment.review_type == review_type,
        )
        for reviewer, reviewee, review_type in pairs
    ]
    return list(
        db.scalars(
            select(Assignment)
            .where(Assignment.cycle_id == cycle_id, or_(*matches))
            .order_by(Assignment.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )


def _user_names(db: Session, user_ids: set[int]) -> dict[int, str]:
    rows = db.execute(select(User.id, User.name).where(User.id.in_(user_ids))).all()
    return {row.id: row.name for row in rows}


def _describe(db: Session, assignment: Assignment) -> dict:
    names = _user_names(db, {assignment.reviewer_id, assignment.reviewee_id})
    return {
        "assignment_id": assignment.id,
        "review_type": assignment.review_type.value,
        "status": assignment.status.value,
        "reviewer": {
            "id": assignment.reviewer_id,
            "name": names[assignment.reviewer_id],
        },
        "reviewee": {
            "id": assignment.reviewee_id,
            "name": names[assignment.reviewee_id],
        },
    }


def _require_discard_confirmed(
    db: Session, to_remove: list[Assignment], discard_ids: Sequence[int] | None
) -> None:
    """Pending reviews go without asking. Drafts and submitted reviews need the admin's yes.

    The yes is the exact set of their ids. If the data changed since the admin looked, the
    sets differ, and the admin gets the new list.
    """
    at_risk = [a for a in to_remove if a.status != AssignmentStatus.pending]
    if not at_risk or set(discard_ids or []) == {a.id for a in at_risk}:
        return
    raise AppError(
        409,
        DISCARD_NOT_CONFIRMED,
        "Some reviews will be discarded. Confirm to continue.",
        details=[
            {
                "assignmentId": row["assignment_id"],
                "reviewType": row["review_type"],
                "status": row["status"],
                "reviewer": row["reviewer"],
                "reviewee": row["reviewee"],
            }
            for row in (_describe(db, assignment) for assignment in at_risk)
        ],
    )


def _change(db: Session, model, old_id: int | None, new_id: int | None) -> Change:
    ids = {i for i in (old_id, new_id) if i is not None}
    names = {
        row.id: row.name
        for row in db.execute(select(model.id, model.name).where(model.id.in_(ids)))
    }
    return Change(
        old_id=old_id,
        old_name=names.get(old_id),
        new_id=new_id,
        new_name=names.get(new_id),
    )
