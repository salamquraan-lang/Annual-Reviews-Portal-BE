from collections.abc import Iterator
from dataclasses import dataclass, field

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import AppError, bad_request
from app.models import Department, User

# Error codes. Message text lives next to the line that raises it.
NOT_FOUND = "DEPARTMENT_NOT_FOUND"
PARENT_NOT_FOUND = "PARENT_NOT_FOUND"
TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
SAME_DEPARTMENT = "SAME_DEPARTMENT"
NAME_TAKEN = "DEPARTMENT_NAME_TAKEN"
LOOP = "DEPARTMENT_LOOP"
INACTIVE = "DEPARTMENT_INACTIVE"
PARENT_INACTIVE = "DEPARTMENT_PARENT_INACTIVE"
HAS_ACTIVE_USERS = "DEPARTMENT_HAS_ACTIVE_USERS"
HAS_ACTIVE_CHILDREN = "DEPARTMENT_HAS_ACTIVE_CHILDREN"
ALREADY_INACTIVE = "DEPARTMENT_ALREADY_INACTIVE"
ALREADY_ACTIVE = "DEPARTMENT_ALREADY_ACTIVE"

MYSQL_DUPLICATE_ENTRY = 1062


@dataclass
class DepartmentPage:
    items: list[Department]
    total: int


@dataclass
class DepartmentDetailData:
    department: Department
    children: list[Department]
    active_user_count: int


@dataclass
class DepartmentNode:
    department: Department
    children: list["DepartmentNode"] = field(default_factory=list)


# ---------------------------------------------------------------- helpers


def _not_found() -> AppError:
    return AppError(404, NOT_FOUND, "Department not found.")


def _name_taken() -> AppError:
    return AppError(409, NAME_TAKEN, "A department with this name already exists here.")


def _lock(db: Session, *ids: int) -> dict[int, Department]:
    """Load the rows and lock them until the transaction ends.

    Ids are locked in ascending order, so two requests never wait on each other in a circle.
    Locking comes first, so every plain read after it sees the latest committed data.
    """
    rows = db.scalars(
        select(Department)
        .where(Department.id.in_(set(ids)))
        .order_by(Department.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    return {row.id: row for row in rows}


def _name_is_taken(db: Session, name: str, parent_id: int | None, exclude_id: int | None = None) -> bool:
    # Only active departments hold a name. A deactivated department's name can be reused.
    conditions = [
        Department.name == name,
        Department.is_active.is_(True),
        Department.parent_id.is_(None) if parent_id is None else Department.parent_id == parent_id,
    ]
    if exclude_id is not None:
        conditions.append(Department.id != exclude_id)
    return db.scalar(select(select(Department.id).where(*conditions).exists())) is True


def _flush(db: Session) -> None:
    """Send pending changes to the database. A duplicate name from a race becomes a 409.

    The check before this is for a friendly early answer. The unique index is the real guard.
    """
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        if exc.orig is not None and exc.orig.args and exc.orig.args[0] == MYSQL_DUPLICATE_ENTRY:
            raise _name_taken() from None
        raise


def _would_loop(db: Session, department_id: int, new_parent_id: int) -> bool:
    """True if department_id is new_parent_id or one of its ancestors."""
    seen: set[int] = set()
    current: int | None = new_parent_id
    while current is not None:
        if current == department_id or current in seen:
            return True
        seen.add(current)
        current = db.scalar(select(Department.parent_id).where(Department.id == current))
    return False


def _audit(db: Session, actor_id: int, action: str, department_id: int, details: dict | None = None) -> None:
    # TODO(D10): write the audit_log row here, inside the caller's transaction (before commit).
    # Do not merge this branch until this is done.
    return None


# ------------------------------------------------------------------ reads


def list_departments(
    db: Session,
    *,
    is_active: bool | None = None,
    parent_id: int | None = None,
    top_level: bool = False,
    search: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> DepartmentPage:
    if parent_id is not None and top_level:
        raise bad_request("VALIDATION_ERROR", "Use either parentId or topLevel, not both.")

    conditions = []
    if is_active is not None:
        conditions.append(Department.is_active == is_active)
    if parent_id is not None:
        conditions.append(Department.parent_id == parent_id)
    if top_level:
        conditions.append(Department.parent_id.is_(None))
    if search and search.strip():
        conditions.append(Department.name.contains(search.strip(), autoescape=True))

    total = db.scalar(select(func.count()).select_from(Department).where(*conditions)) or 0
    items = db.scalars(
        select(Department)
        .where(*conditions)
        .order_by(Department.name, Department.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return DepartmentPage(items=list(items), total=total)


def get_department(db: Session, department_id: int) -> Department:
    department = db.get(Department, department_id)
    if department is None:
        raise _not_found()
    return department


def get_department_detail(db: Session, department_id: int) -> DepartmentDetailData:
    department = get_department(db, department_id)
    children = db.scalars(
        select(Department).where(Department.parent_id == department_id).order_by(Department.name, Department.id)
    ).all()
    active_user_count = db.scalar(
        select(func.count()).select_from(User).where(User.department_id == department_id, User.is_active.is_(True))
    )
    return DepartmentDetailData(department, list(children), active_user_count or 0)


def get_department_tree(db: Session) -> list[DepartmentNode]:
    rows = db.scalars(select(Department).order_by(Department.name, Department.id)).all()
    nodes = {row.id: DepartmentNode(row) for row in rows}

    roots: list[DepartmentNode] = []
    for row in rows:
        if row.parent_id is None:
            roots.append(nodes[row.id])
        elif row.parent_id in nodes:
            nodes[row.parent_id].children.append(nodes[row.id])

    # A department that no root can reach sits in a loop. Rule D2 should make that impossible.
    reached = {node.department.id for node in _walk(roots)}
    if len(reached) != len(rows):
        raise RuntimeError(f"Department tree is corrupt. Unreachable department ids: {sorted(set(nodes) - reached)}")
    return roots


def _walk(roots: list[DepartmentNode]) -> Iterator[DepartmentNode]:
    stack = list(roots)
    while stack:
        node = stack.pop()
        yield node
        stack.extend(node.children)


# ----------------------------------------------------------------- writes


def create_department(db: Session, actor_id: int, name: str, parent_id: int | None) -> Department:
    if parent_id is not None:
        parent = _lock(db, parent_id).get(parent_id)
        if parent is None:
            raise bad_request(PARENT_NOT_FOUND, "Parent department not found.")
        if not parent.is_active:
            raise AppError(409, INACTIVE, "The parent department is inactive.")
    if _name_is_taken(db, name, parent_id):
        raise _name_taken()

    department = Department(name=name, parent_id=parent_id)
    db.add(department)
    _flush(db)
    _audit(db, actor_id, "create", department.id, {"name": name, "parent_id": parent_id})
    db.commit()
    return department


def rename_department(db: Session, actor_id: int, department_id: int, name: str) -> Department:
    department = _lock(db, department_id).get(department_id)
    if department is None:
        raise _not_found()
    if department.name == name:
        return department
    if department.is_active and _name_is_taken(db, name, department.parent_id, exclude_id=department.id):
        raise _name_taken()

    old_name = department.name
    department.name = name
    _flush(db)
    _audit(db, actor_id, "rename", department.id, {"from": old_name, "to": name})
    db.commit()
    return department


def move_department(db: Session, actor_id: int, department_id: int, parent_id: int | None) -> Department:
    ids = [department_id] if parent_id is None else [department_id, parent_id]
    locked = _lock(db, *ids)
    department = locked.get(department_id)
    if department is None:
        raise _not_found()
    if department.parent_id == parent_id:
        return department

    if parent_id is not None:
        parent = locked.get(parent_id)
        if parent is None:
            raise bad_request(PARENT_NOT_FOUND, "Parent department not found.")
        if not parent.is_active:
            raise AppError(409, INACTIVE, "The parent department is inactive.")
        if _would_loop(db, department_id, parent_id):
            raise AppError(409, LOOP, "A department cannot be moved under itself or one of its descendants.")
    if department.is_active and _name_is_taken(db, department.name, parent_id, exclude_id=department.id):
        raise _name_taken()

    old_parent_id = department.parent_id
    department.parent_id = parent_id
    _flush(db)
    _audit(db, actor_id, "move", department.id, {"from_parent_id": old_parent_id, "to_parent_id": parent_id})
    db.commit()
    return department


def deactivate_department(db: Session, actor_id: int, department_id: int) -> Department:
    department = _lock(db, department_id).get(department_id)
    if department is None:
        raise _not_found()
    if not department.is_active:
        raise AppError(409, ALREADY_INACTIVE, "The department is already inactive.")

    user_count = db.scalar(
        select(func.count()).select_from(User).where(User.department_id == department_id, User.is_active.is_(True))
    )
    if user_count:
        raise AppError(
            409, HAS_ACTIVE_USERS, f"The department still has {user_count} active user(s). Move them first."
        )
    child_count = db.scalar(
        select(func.count())
        .select_from(Department)
        .where(Department.parent_id == department_id, Department.is_active.is_(True))
    )
    if child_count:
        raise AppError(
            409, HAS_ACTIVE_CHILDREN, f"The department still has {child_count} active child department(s)."
        )

    department.is_active = False
    _flush(db)
    _audit(db, actor_id, "deactivate", department.id)
    db.commit()
    return department


def reactivate_department(db: Session, actor_id: int, department_id: int) -> Department:
    department = _lock(db, department_id).get(department_id)
    if department is None:
        raise _not_found()
    if department.is_active:
        raise AppError(409, ALREADY_ACTIVE, "The department is already active.")

    if department.parent_id is not None:
        parent = _lock(db, department.parent_id).get(department.parent_id)
        if parent is None or not parent.is_active:
            raise AppError(409, PARENT_INACTIVE, "The parent department is inactive. Reactivate it first.")
    if _name_is_taken(db, department.name, department.parent_id, exclude_id=department.id):
        raise _name_taken()

    department.is_active = True
    _flush(db)
    _audit(db, actor_id, "reactivate", department.id)
    db.commit()
    return department


def move_users(db: Session, actor_id: int, department_id: int, target_id: int) -> int:
    if department_id == target_id:
        raise bad_request(SAME_DEPARTMENT, "The target must be a different department.")
    locked = _lock(db, department_id, target_id)
    if department_id not in locked:
        raise _not_found()
    target = locked.get(target_id)
    if target is None:
        raise bad_request(TARGET_NOT_FOUND, "Target department not found.")
    if not target.is_active:
        raise AppError(409, INACTIVE, "The target department is inactive.")

    result = db.execute(
        update(User)
        .where(User.department_id == department_id, User.is_active.is_(True))
        .values(department_id=target_id)
    )
    moved = result.rowcount
    if moved:
        _audit(db, actor_id, "move_users", department_id, {"to_department_id": target_id, "count": moved})
    db.commit()
    return moved
