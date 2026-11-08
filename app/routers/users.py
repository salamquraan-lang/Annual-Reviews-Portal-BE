from fastapi import APIRouter

from app.dependencies import CurrentUserAllowPending
from app.schemas import NamedRef, Profile

router = APIRouter(prefix="/api/users")


@router.get("/me")
def me(user: CurrentUserAllowPending) -> Profile:
    return Profile(
        id=user.id,
        name=user.name,
        email=user.email,
        role=user.role,
        department=NamedRef(id=user.department.id, name=user.department.name) if user.department else None,
        manager=NamedRef(id=user.manager.id, name=user.manager.name) if user.manager else None,
        email_verified=user.email_verified_at is not None,
        must_change_password=user.must_change_password,
    )
