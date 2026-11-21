# app/services/my_work.py
"""What the signed-in person works on: their reviews to write and their team."""

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import Row, and_, case, or_, select
from sqlalchemy.orm import Session, aliased

from app.errors import AppError
from app.models import (
    Assignment,
    AssignmentStatus,
    Cycle,
    CycleParticipant,
    ReviewType,
    User,
)
from app.services import cycles
from app.services.assignments import visible_to

TEAM_MEMBER_NOT_FOUND = "TEAM_MEMBER_NOT_FOUND"

# Self first, then manager, peer and upward. Written out, so it does not depend on
# the order of the ENUM in the database.
REVIEW_TYPE_ORDER = case(
    {
        ReviewType.self_: 0,
        ReviewType.manager: 1,
        ReviewType.peer: 2,
        ReviewType.upward: 3,
    },
    value=Assignment.review_type,
)


# ------------------------------------------------------- my assignments


@dataclass
class MyAssignments:
    cycle: Cycle
    items: list[Row]


def my_assignments(db: Session, user_id: int) -> MyAssignments:
    """The reviews I write in the open cycle (ASG-1)."""
    cycle = cycles.get_open_cycle(db)
    reviewee = aliased(User)
    rows = db.execute(
        select(
            Assignment.id,
            Assignment.review_type,
            Assignment.status,
            Assignment.submitted_at,
            reviewee.id.label("reviewee_id"),
            reviewee.name.label("reviewee_name"),
        )
        .select_from(Assignment)
        .join(reviewee, reviewee.id == Assignment.reviewee_id)
        .where(
            Assignment.cycle_id == cycle.id,
            Assignment.reviewer_id == user_id,
            visible_to(user_id),
        )
        .order_by(REVIEW_TYPE_ORDER, reviewee.name, Assignment.id)
    ).all()
    return MyAssignments(cycle=cycle, items=list(rows))


# ---------------------------------------------------------------- my team


@dataclass
class ManagerReview:
    assignment_id: int
    status: AssignmentStatus
    submitted_at: datetime | None


@dataclass
class PeerItem:
    assignment_id: int
    peer_id: int
    peer_name: str
    status: AssignmentStatus


@dataclass
class TeamMember:
    user_id: int
    name: str
    is_active: bool
    self_status: AssignmentStatus
    # The review the caller writes about this person.
    manager_review: ManagerReview
    peers: list[PeerItem] = field(default_factory=list)

    @property
    def peers_chosen(self) -> int:
        return len(self.peers)

    @property
    def peers_submitted(self) -> int:
        return sum(
            1 for peer in self.peers if peer.status == AssignmentStatus.submitted
        )


def team(db: Session, cycle_id: int, caller_id: int) -> list[TeamMember]:
    """The caller's direct reports (ASG-2). Empty when there are none."""
    cycles.get_cycle(db, cycle_id)
    reports = db.execute(
        _reports_query(cycle_id, caller_id).order_by(User.name, User.id)
    ).all()
    return _members(db, cycle_id, caller_id, reports)


def team_member(db: Session, cycle_id: int, caller_id: int, user_id: int) -> TeamMember:
    """One direct report (ASG-3). Every failure is the same 404, so it reveals nothing."""
    cycles.get_cycle(db, cycle_id)
    reports = db.execute(
        _reports_query(cycle_id, caller_id).where(CycleParticipant.user_id == user_id)
    ).all()
    if not reports:
        raise AppError(404, TEAM_MEMBER_NOT_FOUND, "Team member not found.")
    return _members(db, cycle_id, caller_id, reports)[0]


def _reports_query(cycle_id: int, caller_id: int):
    return (
        select(User.id, User.name, User.is_active)
        .select_from(CycleParticipant)
        .join(User, User.id == CycleParticipant.user_id)
        .where(
            CycleParticipant.cycle_id == cycle_id,
            CycleParticipant.manager_id == caller_id,
        )
    )


def _members(
    db: Session, cycle_id: int, caller_id: int, reports: list[Row]
) -> list[TeamMember]:
    if not reports:
        return []
    ids = [report.id for report in reports]

    # Every participant has a self review and one manager review from their manager,
    # created when the cycle opened or when the person was added.
    reviews = db.execute(
        select(
            Assignment.id,
            Assignment.reviewee_id,
            Assignment.review_type,
            Assignment.status,
            Assignment.submitted_at,
        ).where(
            Assignment.cycle_id == cycle_id,
            Assignment.reviewee_id.in_(ids),
            or_(
                Assignment.review_type == ReviewType.self_,
                and_(
                    Assignment.review_type == ReviewType.manager,
                    Assignment.reviewer_id == caller_id,
                ),
            ),
        )
    ).all()
    self_status = {
        row.reviewee_id: row.status
        for row in reviews
        if row.review_type == ReviewType.self_
    }
    manager_review = {
        row.reviewee_id: ManagerReview(row.id, row.status, row.submitted_at)
        for row in reviews
        if row.review_type == ReviewType.manager
    }

    peer_rows = db.execute(
        select(
            Assignment.id,
            Assignment.reviewee_id,
            Assignment.status,
            User.id.label("peer_id"),
            User.name.label("peer_name"),
        )
        .select_from(Assignment)
        .join(User, User.id == Assignment.reviewer_id)
        .where(
            Assignment.cycle_id == cycle_id,
            Assignment.review_type == ReviewType.peer,
            Assignment.reviewee_id.in_(ids),
            visible_to(caller_id),
        )
        .order_by(User.name, Assignment.id)
    ).all()
    peers: dict[int, list[PeerItem]] = {user_id: [] for user_id in ids}
    for row in peer_rows:
        peers[row.reviewee_id].append(
            PeerItem(row.id, row.peer_id, row.peer_name, row.status)
        )

    return [
        TeamMember(
            user_id=report.id,
            name=report.name,
            is_active=report.is_active,
            self_status=self_status[report.id],
            manager_review=manager_review[report.id],
            peers=peers[report.id],
        )
        for report in reports
    ]
