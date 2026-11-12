from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response, status

from app.dependencies import DbSession, require_roles
from app.models import Department, Role, User
from app.schemas.departments import (
    DepartmentChildOut,
    DepartmentCreate,
    DepartmentDetail,
    DepartmentListQuery,
    DepartmentListResponse,
    DepartmentMove,
    DepartmentMoveUsers,
    DepartmentOut,
    DepartmentRename,
    DepartmentTreeNode,
    MoveUsersResponse,
)
from app.services import departments

# Every route here is open to admins and super admins. Write routes add a stricter guard.
router = APIRouter(
    prefix="/api/departments",
    dependencies=[Depends(require_roles(Role.super_admin, Role.admin))],
)

DepartmentId = Annotated[int, Path(ge=1)]
SuperAdmin = Annotated[User, Depends(require_roles(Role.super_admin))]


def _to_out(department: Department) -> DepartmentOut:
    return DepartmentOut(
        id=department.id,
        name=department.name,
        parent_id=department.parent_id,
        is_active=department.is_active,
        created_at=department.created_at,
        updated_at=department.updated_at,
    )


def _to_tree(node: departments.DepartmentNode) -> DepartmentTreeNode:
    return DepartmentTreeNode(
        id=node.department.id,
        name=node.department.name,
        is_active=node.department.is_active,
        children=[_to_tree(child) for child in node.children],
    )


# ------------------------------------------------------------------ reads


@router.get("")
def list_departments(
    db: DbSession, query: Annotated[DepartmentListQuery, Query()]
) -> DepartmentListResponse:
    result = departments.list_departments(db, **query.model_dump())
    return DepartmentListResponse(
        items=[_to_out(department) for department in result.items],
        page=query.page,
        page_size=query.page_size,
        total=result.total,
    )


# Declared before "/{department_id}", so "tree" is not read as an id.
@router.get("/tree")
def get_department_tree(db: DbSession) -> list[DepartmentTreeNode]:
    return [_to_tree(node) for node in departments.get_department_tree(db)]


@router.get("/{department_id}")
def get_department(db: DbSession, department_id: DepartmentId) -> DepartmentDetail:
    data = departments.get_department_detail(db, department_id)
    department = data.department
    return DepartmentDetail(
        id=department.id,
        name=department.name,
        parent_id=department.parent_id,
        is_active=department.is_active,
        created_at=department.created_at,
        updated_at=department.updated_at,
        children=[
            DepartmentChildOut(id=child.id, name=child.name, is_active=child.is_active)
            for child in data.children
        ],
        active_user_count=data.active_user_count,
    )


# ----------------------------------------------------------------- writes


@router.post("", status_code=status.HTTP_201_CREATED)
def create_department(
    db: DbSession, actor: SuperAdmin, body: DepartmentCreate, response: Response
) -> DepartmentOut:
    department = departments.create_department(db, actor.id, body.name, body.parent_id)
    response.headers["Location"] = f"/api/departments/{department.id}"
    return _to_out(department)


@router.patch("/{department_id}")
def rename_department(
    db: DbSession,
    actor: SuperAdmin,
    department_id: DepartmentId,
    body: DepartmentRename,
) -> DepartmentOut:
    return _to_out(
        departments.rename_department(db, actor.id, department_id, body.name)
    )


@router.put("/{department_id}/parent")
def move_department(
    db: DbSession, actor: SuperAdmin, department_id: DepartmentId, body: DepartmentMove
) -> DepartmentOut:
    return _to_out(
        departments.move_department(db, actor.id, department_id, body.parent_id)
    )


@router.post("/{department_id}/deactivate")
def deactivate_department(
    db: DbSession, actor: SuperAdmin, department_id: DepartmentId
) -> DepartmentOut:
    return _to_out(departments.deactivate_department(db, actor.id, department_id))


@router.post("/{department_id}/reactivate")
def reactivate_department(
    db: DbSession, actor: SuperAdmin, department_id: DepartmentId
) -> DepartmentOut:
    return _to_out(departments.reactivate_department(db, actor.id, department_id))


@router.post("/{department_id}/move-users")
def move_department_users(
    db: DbSession,
    actor: SuperAdmin,
    department_id: DepartmentId,
    body: DepartmentMoveUsers,
) -> MoveUsersResponse:
    moved = departments.move_users(
        db, actor.id, department_id, body.target_department_id
    )
    return MoveUsersResponse(moved_count=moved)
