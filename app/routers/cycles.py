from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response, status

from app.dependencies import CurrentUser, DbSession, require_roles
from app.models import Cycle, Role, User
from app.schemas.cycles import (
    CycleCreate,
    CycleEndDate,
    CycleListQuery,
    CycleListResponse,
    CycleOut,
    CycleUpdate,
    FinalizeResponse,
    FinalizeWarning,
    ReadinessProblem,
    ReadinessResponse,
)
from app.services import cycles

# C9: managing cycles is for admins only. The super admin is not allowed.
# Two routers share one prefix. Every route on admin_router needs the admin role.
# The one route any signed-in user may call sits on public_router.
# Both are joined into "router" at the end of the file.
public_router = APIRouter(prefix="/api/cycles")
admin_router = APIRouter(prefix="/api/cycles", dependencies=[Depends(require_roles(Role.admin))])
router = APIRouter()

CycleId = Annotated[int, Path(ge=1)]
Admin = Annotated[User, Depends(require_roles(Role.admin))]


def _to_out(cycle: Cycle) -> CycleOut:
    return CycleOut(
        id=cycle.id,
        name=cycle.name,
        type=cycle.type,
        year=cycle.year,
        start_date=cycle.start_date,
        end_date=cycle.end_date,
        status=cycle.status,
    )


# ------------------------------------------------- open to every signed-in user


# Declared before the admin routes, so "current" is not read as an id.
@public_router.get("/current")
def get_current_cycle(db: DbSession, _user: CurrentUser) -> CycleOut:
    return _to_out(cycles.get_open_cycle(db))


# ------------------------------------------------------------------ reads


@admin_router.get("")
def list_cycles(db: DbSession, query: Annotated[CycleListQuery, Query()]) -> CycleListResponse:
    result = cycles.list_cycles(db, **query.model_dump())
    return CycleListResponse(
        items=[_to_out(cycle) for cycle in result.items],
        page=query.page,
        page_size=query.page_size,
        total=result.total,
    )


@admin_router.get("/{cycle_id}")
def get_cycle(db: DbSession, cycle_id: CycleId) -> CycleOut:
    return _to_out(cycles.get_cycle(db, cycle_id))


@admin_router.get("/{cycle_id}/readiness")
def get_readiness(db: DbSession, cycle_id: CycleId) -> ReadinessResponse:
    problems = cycles.check_readiness(db, cycle_id)
    return ReadinessResponse(
        ready=not problems,
        problems=[ReadinessProblem(code=problem.code, message=problem.message) for problem in problems],
    )


# ----------------------------------------------------------------- writes


@admin_router.post("", status_code=status.HTTP_201_CREATED)
def create_cycle(db: DbSession, actor: Admin, body: CycleCreate, response: Response) -> CycleOut:
    cycle = cycles.create_cycle(db, actor.id, cycles.NewCycle(**body.model_dump()))
    response.headers["Location"] = f"/api/cycles/{cycle.id}"
    return _to_out(cycle)


@admin_router.post("/{cycle_id}/copy", status_code=status.HTTP_201_CREATED)
def copy_cycle(db: DbSession, actor: Admin, cycle_id: CycleId, body: CycleCreate, response: Response) -> CycleOut:
    cycle = cycles.copy_cycle(db, actor.id, cycle_id, cycles.NewCycle(**body.model_dump()))
    response.headers["Location"] = f"/api/cycles/{cycle.id}"
    return _to_out(cycle)


@admin_router.patch("/{cycle_id}")
def update_cycle(db: DbSession, actor: Admin, cycle_id: CycleId, body: CycleUpdate) -> CycleOut:
    # exclude_unset keeps only the fields the client sent.
    changes = body.model_dump(exclude_unset=True)
    return _to_out(cycles.update_cycle(db, actor.id, cycle_id, changes))


@admin_router.put("/{cycle_id}/end-date")
def change_end_date(db: DbSession, actor: Admin, cycle_id: CycleId, body: CycleEndDate) -> CycleOut:
    return _to_out(cycles.change_end_date(db, actor.id, cycle_id, body.end_date))


@admin_router.delete("/{cycle_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_cycle(db: DbSession, actor: Admin, cycle_id: CycleId) -> Response:
    cycles.delete_cycle(db, actor.id, cycle_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@admin_router.post("/{cycle_id}/open")
def open_cycle(db: DbSession, actor: Admin, cycle_id: CycleId) -> CycleOut:
    return _to_out(cycles.open_cycle(db, actor.id, cycle_id))


@admin_router.post("/{cycle_id}/close")
def close_cycle(db: DbSession, actor: Admin, cycle_id: CycleId) -> CycleOut:
    return _to_out(cycles.close_cycle(db, actor.id, cycle_id))


@admin_router.post("/{cycle_id}/reopen")
def reopen_cycle(db: DbSession, actor: Admin, cycle_id: CycleId) -> CycleOut:
    return _to_out(cycles.reopen_cycle(db, actor.id, cycle_id))


@admin_router.post("/{cycle_id}/finalize")
def finalize_cycle(db: DbSession, actor: Admin, cycle_id: CycleId) -> FinalizeResponse:
    result = cycles.finalize_cycle(db, actor.id, cycle_id)
    out = _to_out(result.cycle)
    return FinalizeResponse(
        **out.model_dump(),
        warnings=[
            FinalizeWarning(code=warning.code, user_id=warning.user_id, message=warning.message)
            for warning in result.warnings
        ],
    )


# public_router goes first, so "current" is not read as an id by "/{cycle_id}".
router.include_router(public_router)
router.include_router(admin_router)
