# app/routers/my_work.py
from typing import Annotated

from fastapi import APIRouter, Depends, Path

from app.dependencies import CurrentUser, DbSession, require_auth
from app.schemas.my_work import (
    CycleBrief,
    MyAssignmentOut,
    MyAssignmentsResponse,
    PeerCounts,
    PeerCountsWithItems,
    TeamListResponse,
    TeamManagerReview,
    TeamMemberDetail,
    TeamMemberOut,
    TeamPeerOut,
)
from app.schemas.participants import UserRef, UserState
from app.schemas.question_bank import MAX_ID
from app.services import my_work

# Any signed-in role passes. The relationship to the data decides what comes back.
router = APIRouter(prefix="/api", dependencies=[Depends(require_auth())])

CycleId = Annotated[int, Path(ge=1, le=MAX_ID)]
UserId = Annotated[int, Path(ge=1, le=MAX_ID)]


def _manager_review(member: my_work.TeamMember) -> TeamManagerReview:
    review = member.manager_review
    return TeamManagerReview(
        assignment_id=review.assignment_id,
        status=review.status,
        submitted_at=review.submitted_at,
    )


def _user_state(member: my_work.TeamMember) -> UserState:
    return UserState(id=member.user_id, name=member.name, is_active=member.is_active)


def _member_out(member: my_work.TeamMember) -> TeamMemberOut:
    return TeamMemberOut(
        user=_user_state(member),
        self_status=member.self_status,
        manager=_manager_review(member),
        peers=PeerCounts(chosen=member.peers_chosen, submitted=member.peers_submitted),
    )


def _member_detail(member: my_work.TeamMember) -> TeamMemberDetail:
    return TeamMemberDetail(
        user=_user_state(member),
        self_status=member.self_status,
        manager=_manager_review(member),
        peers=PeerCountsWithItems(
            chosen=member.peers_chosen,
            submitted=member.peers_submitted,
            items=[
                TeamPeerOut(
                    assignment_id=peer.assignment_id,
                    peer=UserRef(id=peer.peer_id, name=peer.peer_name),
                    status=peer.status,
                )
                for peer in member.peers
            ],
        ),
    )


@router.get("/users/me/assignments")
def my_assignments(db: DbSession, user: CurrentUser) -> MyAssignmentsResponse:
    result = my_work.my_assignments(db, user.id)
    return MyAssignmentsResponse(
        cycle=CycleBrief(
            id=result.cycle.id, name=result.cycle.name, status=result.cycle.status
        ),
        items=[
            MyAssignmentOut(
                id=row.id,
                review_type=row.review_type,
                status=row.status,
                submitted_at=row.submitted_at,
                reviewee=UserRef(id=row.reviewee_id, name=row.reviewee_name),
            )
            for row in result.items
        ],
    )


@router.get("/cycles/{cycle_id}/team")
def get_team(db: DbSession, user: CurrentUser, cycle_id: CycleId) -> TeamListResponse:
    members = my_work.team(db, cycle_id, user.id)
    return TeamListResponse(items=[_member_out(member) for member in members])


@router.get("/cycles/{cycle_id}/team/{user_id}")
def get_team_member(
    db: DbSession, user: CurrentUser, cycle_id: CycleId, user_id: UserId
) -> TeamMemberDetail:
    return _member_detail(my_work.team_member(db, cycle_id, user.id, user_id))
