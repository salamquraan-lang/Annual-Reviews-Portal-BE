from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.database import get_db
from app.errors import forbidden, unauthorized
from app.mailer import Mailer
from app.models import Role, User
from app.security import decode_access_token

DbSession = Annotated[Session, Depends(get_db)]

_bearer = HTTPBearer(auto_error=False)


def get_mailer(request: Request) -> Mailer:
    return request.app.state.mailer


Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]


def optional_auth(db: DbSession, credentials: Credentials) -> User | None:
    # The user is read from the database on every request, so deactivation
    # and role changes apply immediately instead of when the token expires.
    payload = decode_access_token(credentials.credentials) if credentials else None
    if payload is None:
        return None
    user = db.get(User, int(payload["sub"]))
    return user if user is not None and user.is_active else None


def require_auth(allow_pending_password_change: bool = False) -> Callable[..., User]:
    def dependency(user: Annotated[User | None, Depends(optional_auth)]) -> User:
        if user is None:
            raise unauthorized()
        if user.must_change_password and not allow_pending_password_change:
            raise forbidden("PASSWORD_CHANGE_REQUIRED", "You must change your password first.")
        return user

    return dependency


def require_roles(*roles: Role) -> Callable[..., User]:
    def dependency(user: Annotated[User, Depends(require_auth())]) -> User:
        if user.role not in roles:
            raise forbidden()
        return user

    return dependency


CurrentUser = Annotated[User, Depends(require_auth())]
CurrentUserAllowPending = Annotated[User, Depends(require_auth(allow_pending_password_change=True))]
OptionalUser = Annotated[User | None, Depends(optional_auth)]
