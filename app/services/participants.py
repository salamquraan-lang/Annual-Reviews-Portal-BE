"""The people of a cycle: the list, one person's page, and adding a person."""

from dataclasses import dataclass

from sqlalchemy import Row, func, select
from sqlalchemy.orm import Session, aliased

from app.errors import AppError, bad_request
from app.models import CycleParticipant, Department, User
from app.services import assignments, cycles, snapshot

PARTICIPANT_NOT_FOUND = "PARTICIPANT_NOT_FOUND"
USER_NOT_FOUND = "USER_NOT_FOUND"
USER_NOT_ACTIVE = "USER_NOT_ACTIVE"
ALREADY_PARTICIPANT = "ALREADY_PARTICIPANT"
MANAGER_NOT_PARTICIPANT = "MANAGER_NOT_PARTICIPANT"


@dataclass
class ParticipantPage:
    items: list[Row]
    total: int


@dataclass
class ParticipantDetail:
    participant: Row
    assignments_as_reviewer: list[Row]
    assignments_as_reviewee: list[Row]


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
