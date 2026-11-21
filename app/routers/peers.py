from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status

from app.dependencies import CurrentUser, DbSession, require_auth, require_roles
from app.models import Role, User
from app.schemas.participants import DepartmentRef, UserRef
from app.schemas.peers import (
    CandidateListQuery,
    CandidateListResponse,
    CandidateOut,
    PeerCreate,
    PeerGapListQuery,
    PeerGapListResponse,
    PeerGapOut,
    PeerListResponse,
    PeerOut,
)
from app.schemas.question_bank import MAX_ID
from app.services import peers

# Any signed-in role may call these routes. The service decides by relationship: the
# reviewee's manager or an admin. Everybody else gets 404. Only peer gaps is admin-only.
router = APIRouter(prefix="/api/cycles", dependencies=[Depends(require_auth())])

CycleId = Annotated[int, Path(ge=1, le=MAX_ID)]
UserId = Annotated[int, Path(ge=1, le=MAX_ID)]
PeerId = Annotated[int, Path(ge=1, le=MAX_ID)]
Admin = Annotated[User, Depends(require_roles(Role.admin))]


# ------------------------------------------------------------------ mapping


def _peer_list_out(result: peers.PeerList) -> PeerListResponse:
    return PeerListResponse(
        reviewee=UserRef(id=result.reviewee_id, name=result.reviewee_name),
        count=len(result.items),
        items=[
            PeerOut(
                assignment_id=row.assignment_id,
                peer=UserRef(id=row.peer_id, name=row.peer_name),
                status=row.status,
            )
            for row in result.items
        ],
    )


def _candidate_page_out(
    result: peers.CandidatePage, query: CandidateListQuery
) -> CandidateListResponse:
    return CandidateListResponse(
        items=[
            CandidateOut(
                user=UserRef(id=row.user_id, name=row.user_name),
                department=DepartmentRef(
                    id=row.department_id, name=row.department_name
                ),
            )
            for row in result.items
        ],
        page=query.page,
        page_size=query.page_size,
        total=result.total,
    )


def _gap_page_out(
    result: peers.GapPage, query: PeerGapListQuery
) -> PeerGapListResponse:
    return PeerGapListResponse(
        items=[
            PeerGapOut(
                user=UserRef(id=row.user_id, name=row.user_name),
                department=DepartmentRef(
                    id=row.department_id, name=row.department_name
                ),
                manager=UserRef(id=row.manager_id, name=row.manager_name),
                peer_count=row.peer_count,
                missing=max(peers.MIN_PEERS - row.peer_count, 0),
            )
            for row in result.items
        ],
        page=query.page,
        page_size=query.page_size,
        total=result.total,
    )


# ------------------------------------------------------------------- routes


@router.get("/{cycle_id}/participants/{user_id}/peer-candidates")
def list_peer_candidates(
    db: DbSession,
    caller: CurrentUser,
    cycle_id: CycleId,
    user_id: UserId,
    query: Annotated[CandidateListQuery, Query()],
) -> CandidateListResponse:
    result = peers.list_candidates(db, caller, cycle_id, user_id, **query.model_dump())
    return _candidate_page_out(result, query)


@router.post(
    "/{cycle_id}/participants/{user_id}/peers", status_code=status.HTTP_201_CREATED
)
def add_peer(
    db: DbSession,
    caller: CurrentUser,
    cycle_id: CycleId,
    user_id: UserId,
    body: PeerCreate,
) -> PeerListResponse:
    return _peer_list_out(peers.add_peer(db, caller, cycle_id, user_id, body.peer_id))


@router.delete("/{cycle_id}/participants/{user_id}/peers/{peer_id}")
def remove_peer(
    db: DbSession,
    caller: CurrentUser,
    cycle_id: CycleId,
    user_id: UserId,
    peer_id: PeerId,
) -> PeerListResponse:
    return _peer_list_out(peers.remove_peer(db, caller, cycle_id, user_id, peer_id))


@router.get("/{cycle_id}/peer-gaps")
def list_peer_gaps(
    db: DbSession,
    admin: Admin,
    cycle_id: CycleId,
    query: Annotated[PeerGapListQuery, Query()],
) -> PeerGapListResponse:
    result = peers.list_gaps(db, admin.id, cycle_id, **query.model_dump())
    return _gap_page_out(result, query)
