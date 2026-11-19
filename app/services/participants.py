# app/services/participants.py
"""Reads of the people in a cycle: the list and one person's page."""

from dataclasses import dataclass

from sqlalchemy import Row, func, select
from sqlalchemy.orm import Session, aliased

from app.errors import AppError
from app.models import CycleParticipant, Department, User
from app.services import assignments, cycles

PARTICIPANT_NOT_FOUND = "PARTICIPANT_NOT_FOUND"


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
