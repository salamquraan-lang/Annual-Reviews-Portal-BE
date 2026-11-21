"""Peers: who writes a peer review about whom (ASG-4 to ASG-7).

A manager chooses the peers of their direct reports. An admin may do it for anyone
except themself, so nobody picks the people who review them.
"""

from dataclasses import dataclass

from sqlalchemy import Row, and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from app.errors import AppError, bad_request, forbidden
from app.models import (
    Assignment,
    AssignmentStatus,
    CycleParticipant,
    Department,
    ReviewType,
    Role,
    User,
)
from app.services import cycles

PARTICIPANT_NOT_FOUND = "PARTICIPANT_NOT_FOUND"
TEAM_MEMBER_NOT_FOUND = "TEAM_MEMBER_NOT_FOUND"
OWN_PEERS_NOT_ALLOWED = "OWN_PEERS_NOT_ALLOWED"
PEER_NOT_FOUND = "PEER_NOT_FOUND"
PEER_NOT_ACTIVE = "PEER_NOT_ACTIVE"
PEER_IS_REVIEWEE = "PEER_IS_REVIEWEE"
PEER_IS_MANAGER = "PEER_IS_MANAGER"
PEER_IS_DIRECT_REPORT = "PEER_IS_DIRECT_REPORT"
PEER_ALREADY_CHOSEN = "PEER_ALREADY_CHOSEN"
PEER_NOT_CHOSEN = "PEER_NOT_CHOSEN"
PEER_REVIEW_SUBMITTED = "PEER_REVIEW_SUBMITTED"

# P5: three peers are expected. Nothing enforces it. The gaps list shows who is short.
MIN_PEERS = 3

MYSQL_DUPLICATE_ENTRY = 1062


@dataclass
class PeerList:
    reviewee_id: int
    reviewee_name: str
    items: list[Row]


@dataclass
class CandidatePage:
    items: list[Row]
    total: int


@dataclass
class GapPage:
    items: list[Row]
    total: int


# ----------------------------------------------------------------- access


def _reviewee(
    db: Session, cycle_id: int, user_id: int, caller: User
) -> CycleParticipant:
    """The participant the caller may manage peers for.

    A manager may act only for their own direct reports. For anybody else the answer is
    the same 404, so it shows nothing about who is in the cycle. An admin may act for
    anyone, except themself.
    """
    participant = db.get(CycleParticipant, (cycle_id, user_id))
    if caller.role == Role.admin:
        if participant is None:
            raise AppError(404, PARTICIPANT_NOT_FOUND, "Participant not found.")
        if user_id == caller.id:
            raise forbidden(OWN_PEERS_NOT_ALLOWED, "You cannot choose your own peers.")
        return participant
    if participant is None or participant.manager_id != caller.id:
        raise AppError(404, TEAM_MEMBER_NOT_FOUND, "Team member not found.")
    return participant


def _audit(
    db: Session,
    actor_id: int,
    action: str,
    table: str,
    record_id: int,
    details: dict | None = None,
) -> None:
    # TODO(P12): write the audit_log row here, inside the caller's transaction (before
    # commit). The audit log is cluster 11. Same stub as in services/templates.py.
    return None


def _audit_if_admin(
    db: Session,
    caller: User,
    action: str,
    record_id: int,
    details: dict,
) -> None:
    # P12: only an admin's change is audited. A manager's is not.
    if caller.role == Role.admin:
        _audit(db, caller.id, action, "assignments", record_id, details)


# ------------------------------------------------------------------ reads


def _peer_list(db: Session, cycle_id: int, user_id: int) -> PeerList:
    name = db.scalar(select(User.name).where(User.id == user_id)) or ""
    rows = db.execute(
        select(
            Assignment.id.label("assignment_id"),
            User.id.label("peer_id"),
            User.name.label("peer_name"),
            Assignment.status,
        )
        .select_from(Assignment)
        .join(User, User.id == Assignment.reviewer_id)
        .where(
            Assignment.cycle_id == cycle_id,
            Assignment.reviewee_id == user_id,
            Assignment.review_type == ReviewType.peer,
        )
        .order_by(User.name, User.id)
    ).all()
    return PeerList(reviewee_id=user_id, reviewee_name=name, items=list(rows))


def list_candidates(
    db: Session,
    caller: User,
    cycle_id: int,
    user_id: int,
    *,
    search: str | None = None,
    department_id: int | None = None,
    page: int = 1,
    page_size: int = 20,
) -> CandidatePage:
    """Active participants who can still be chosen as a peer of this person."""
    cycles.get_cycle(db, cycle_id)
    reviewee = _reviewee(db, cycle_id, user_id, caller)

    already_chosen = select(Assignment.reviewer_id).where(
        Assignment.cycle_id == cycle_id,
        Assignment.reviewee_id == user_id,
        Assignment.review_type == ReviewType.peer,
    )
    direct_reports = select(CycleParticipant.user_id).where(
        CycleParticipant.cycle_id == cycle_id,
        CycleParticipant.manager_id == user_id,
    )
    query = (
        select(
            User.id.label("user_id"),
            User.name.label("user_name"),
            Department.id.label("department_id"),
            Department.name.label("department_name"),
        )
        .select_from(CycleParticipant)
        .join(User, User.id == CycleParticipant.user_id)
        .join(Department, Department.id == CycleParticipant.department_id)
        .where(
            CycleParticipant.cycle_id == cycle_id,
            User.is_active.is_(True),
            User.id != user_id,
            User.id.not_in(already_chosen),
            User.id.not_in(direct_reports),
        )
    )
    if reviewee.manager_id is not None:
        query = query.where(User.id != reviewee.manager_id)
    if department_id is not None:
        query = query.where(CycleParticipant.department_id == department_id)
    if search:
        # autoescape makes % and _ plain characters.
        query = query.where(User.name.icontains(search, autoescape=True))

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.execute(
        query.order_by(User.name, User.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return CandidatePage(items=list(rows), total=total)


def list_gaps(
    db: Session,
    caller_id: int,
    cycle_id: int,
    *,
    department_id: int | None = None,
    manager_id: int | None = None,
    page: int = 1,
    page_size: int = 20,
) -> GapPage:
    """Active participants with a manager and fewer than three peers. Not their own row."""
    cycles.get_cycle(db, cycle_id)

    manager = aliased(User)
    peer_count = func.count(Assignment.id)
    query = (
        select(
            User.id.label("user_id"),
            User.name.label("user_name"),
            Department.id.label("department_id"),
            Department.name.label("department_name"),
            manager.id.label("manager_id"),
            manager.name.label("manager_name"),
            peer_count.label("peer_count"),
        )
        .select_from(CycleParticipant)
        .join(User, User.id == CycleParticipant.user_id)
        .join(Department, Department.id == CycleParticipant.department_id)
        # An inner join: a person with no manager has no peers to choose.
        .join(manager, manager.id == CycleParticipant.manager_id)
        .outerjoin(
            Assignment,
            and_(
                Assignment.cycle_id == CycleParticipant.cycle_id,
                Assignment.reviewee_id == CycleParticipant.user_id,
                Assignment.review_type == ReviewType.peer,
            ),
        )
        .where(
            CycleParticipant.cycle_id == cycle_id,
            CycleParticipant.user_id != caller_id,
            User.is_active.is_(True),
        )
        .group_by(
            User.id,
            User.name,
            Department.id,
            Department.name,
            manager.id,
            manager.name,
        )
        .having(peer_count < MIN_PEERS)
    )
    if department_id is not None:
        query = query.where(CycleParticipant.department_id == department_id)
    if manager_id is not None:
        query = query.where(CycleParticipant.manager_id == manager_id)

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.execute(
        query.order_by(peer_count, User.name, User.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return GapPage(items=list(rows), total=total)


# ----------------------------------------------------------------- writes


def add_peer(
    db: Session, caller: User, cycle_id: int, user_id: int, peer_id: int
) -> PeerList:
    cycle = cycles.lock_cycle(db, cycle_id)
    reviewee = _reviewee(db, cycle_id, user_id, caller)
    cycles.require_open(cycle)

    peer = db.execute(
        select(CycleParticipant.manager_id, User.is_active)
        .join(User, User.id == CycleParticipant.user_id)
        .where(
            CycleParticipant.cycle_id == cycle_id,
            CycleParticipant.user_id == peer_id,
        )
    ).first()
    if peer is None:
        raise bad_request(PEER_NOT_FOUND, "This person is not in the cycle.")
    if not peer.is_active:
        raise AppError(409, PEER_NOT_ACTIVE, "This person is not active.")
    if peer_id == user_id:
        raise bad_request(PEER_IS_REVIEWEE, "A person cannot be their own peer.")
    if peer_id == reviewee.manager_id:
        raise bad_request(PEER_IS_MANAGER, "The manager cannot be a peer.")
    if peer.manager_id == user_id:
        raise bad_request(PEER_IS_DIRECT_REPORT, "A direct report cannot be a peer.")

    assignment = Assignment(
        cycle_id=cycle_id,
        reviewer_id=peer_id,
        reviewee_id=user_id,
        review_type=ReviewType.peer,
        status=AssignmentStatus.pending,
    )
    db.add(assignment)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        orig = exc.orig
        if orig is not None and orig.args and orig.args[0] == MYSQL_DUPLICATE_ENTRY:
            raise AppError(
                409, PEER_ALREADY_CHOSEN, "This person is already a peer."
            ) from None
        raise

    _audit_if_admin(
        db,
        caller,
        "add_peer",
        assignment.id,
        {"cycle_id": cycle_id, "reviewee_id": user_id, "peer_id": peer_id},
    )
    db.commit()
    return _peer_list(db, cycle_id, user_id)


def remove_peer(
    db: Session, caller: User, cycle_id: int, user_id: int, peer_id: int
) -> PeerList:
    cycle = cycles.lock_cycle(db, cycle_id)
    _reviewee(db, cycle_id, user_id, caller)
    cycles.require_open(cycle)

    assignment = db.scalar(
        select(Assignment)
        .where(
            Assignment.cycle_id == cycle_id,
            Assignment.reviewee_id == user_id,
            Assignment.reviewer_id == peer_id,
            Assignment.review_type == ReviewType.peer,
        )
        .with_for_update()
    )
    if assignment is None:
        raise AppError(404, PEER_NOT_CHOSEN, "This person is not a peer.")
    # P6: a submitted review is never removed.
    if assignment.status == AssignmentStatus.submitted:
        raise AppError(409, PEER_REVIEW_SUBMITTED, "This peer has already submitted.")

    assignment_id = assignment.id
    discarded_draft = assignment.status == AssignmentStatus.draft
    # The database removes the answers of the assignment (ON DELETE CASCADE).
    db.delete(assignment)
    db.flush()

    _audit_if_admin(
        db,
        caller,
        "remove_peer",
        assignment_id,
        {
            "cycle_id": cycle_id,
            "reviewee_id": user_id,
            "peer_id": peer_id,
            "discarded_draft": discarded_draft,
        },
    )
    db.commit()
    return _peer_list(db, cycle_id, user_id)
