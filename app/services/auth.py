from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import quote

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError, bad_request, forbidden, unauthorized
from app.mailer import Mail, Mailer
from app.models import AuthToken, AuthTokenType, RefreshToken, Role, User, utcnow
from app.security import create_access_token, hash_password, hash_token, random_token, verify_password

INVALID_LOGIN = AppError(401, "INVALID_CREDENTIALS", "Email or password is incorrect.")
SESSION_EXPIRED = "Session expired. Please log in again."


def invalid_link() -> AppError:
    return bad_request("INVALID_OR_EXPIRED_TOKEN", "This link is invalid or has expired.")


@dataclass
class AuthSession:
    access_token: str
    refresh_token: str
    refresh_expires_at: datetime
    must_change_password: bool


def _find_user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == email))


def _create_session(db: Session, user: User, user_agent: str | None) -> AuthSession:
    settings = get_settings()
    refresh_token = random_token()
    expires_at = utcnow() + timedelta(days=settings.refresh_token_ttl_days)
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=hash_token(refresh_token),
            user_agent=(user_agent or "")[:255] or None,
            expires_at=expires_at,
        )
    )
    db.commit()
    return AuthSession(
        access_token=create_access_token(user.id, user.role),
        refresh_token=refresh_token,
        refresh_expires_at=expires_at,
        must_change_password=user.must_change_password,
    )


def revoke_all_sessions(db: Session, user_id: int, except_refresh_token: str | None = None) -> None:
    stmt = delete(RefreshToken).where(RefreshToken.user_id == user_id)
    if except_refresh_token:
        stmt = stmt.where(RefreshToken.token_hash != hash_token(except_refresh_token))
    db.execute(stmt)
    db.commit()


def _issue_email_token(db: Session, user_id: int, token_type: AuthTokenType, expires_at: datetime) -> str:
    token = random_token()
    db.execute(
        delete(AuthToken).where(
            AuthToken.user_id == user_id, AuthToken.type == token_type, AuthToken.used_at.is_(None)
        )
    )
    db.add(AuthToken(user_id=user_id, type=token_type, token_hash=hash_token(token), expires_at=expires_at))
    db.commit()
    return token


def _consume_email_token(db: Session, token: str, token_type: AuthTokenType) -> int:
    row = db.scalar(select(AuthToken).where(AuthToken.token_hash == hash_token(token)))
    if row is None or row.type != token_type:
        raise invalid_link()

    now = utcnow()
    result = db.execute(
        update(AuthToken)
        .where(AuthToken.id == row.id, AuthToken.used_at.is_(None), AuthToken.expires_at > now)
        .values(used_at=now)
    )
    db.commit()
    if result.rowcount == 0:
        raise invalid_link()
    return row.user_id


def _send_verification_email(db: Session, mailer: Mailer, user: User) -> None:
    settings = get_settings()
    expires_at = utcnow() + timedelta(hours=settings.verify_email_ttl_hours)
    token = _issue_email_token(db, user.id, AuthTokenType.verify_email, expires_at)
    link = f"{settings.app_url}/verify-email?token={quote(token)}"
    mailer.send(
        Mail(
            to=user.email,
            subject="Verify your email address",
            text=(
                f"Hello {user.name},\n\n"
                f"Please verify your email address by opening this link:\n{link}\n\n"
                f"The link expires in {settings.verify_email_ttl_hours} hours. "
                "If you did not sign up, ignore this email."
            ),
        )
    )


def signup(db: Session, mailer: Mailer, name: str, email: str, password: str) -> None:
    # Hash before looking anything up, so a taken email answers as slowly as a new one.
    password_hash = hash_password(password)

    existing = _find_user_by_email(db, email)
    if existing is None:
        user = User(name=name, email=email, password_hash=password_hash, role=Role.employee)
        db.add(user)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return
        _send_verification_email(db, mailer, user)
    elif existing.is_active and existing.email_verified_at is None:
        _send_verification_email(db, mailer, existing)


def verify_email(db: Session, token: str) -> None:
    user_id = _consume_email_token(db, token, AuthTokenType.verify_email)
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise invalid_link()
    if user.email_verified_at is None:
        user.email_verified_at = utcnow()
        db.commit()


def resend_verification(db: Session, mailer: Mailer, email: str) -> None:
    user = _find_user_by_email(db, email)
    if user is not None and user.is_active and user.email_verified_at is None:
        _send_verification_email(db, mailer, user)


def login(db: Session, email: str, password: str, user_agent: str | None) -> AuthSession:
    user = _find_user_by_email(db, email)
    ok = verify_password(password, user.password_hash if user else None)

    if user is None or not ok or not user.is_active:
        raise INVALID_LOGIN
    if user.email_verified_at is None:
        raise forbidden("EMAIL_NOT_VERIFIED", "Please verify your email address before logging in.")
    return _create_session(db, user, user_agent)


def refresh(db: Session, refresh_token: str | None, user_agent: str | None) -> AuthSession:
    """Refresh tokens are single use: each refresh replaces the token with a new one."""
    if not refresh_token:
        raise unauthorized(SESSION_EXPIRED)

    row = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(refresh_token)))
    if row is None:
        raise unauthorized(SESSION_EXPIRED)

    user = row.user
    expired = row.expires_at <= utcnow()
    db.delete(row)
    db.commit()

    if expired or not user.is_active:
        raise unauthorized(SESSION_EXPIRED)
    return _create_session(db, user, user_agent)


def logout(db: Session, refresh_token: str | None) -> None:
    if refresh_token:
        db.execute(delete(RefreshToken).where(RefreshToken.token_hash == hash_token(refresh_token)))
        db.commit()


def change_password(
    db: Session, user: User, current_password: str, new_password: str, current_refresh_token: str | None
) -> None:
    if not verify_password(current_password, user.password_hash):
        raise bad_request("WRONG_PASSWORD", "Your current password is incorrect.")

    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    db.commit()
    revoke_all_sessions(db, user.id, except_refresh_token=current_refresh_token)


def forgot_password(db: Session, mailer: Mailer, email: str) -> None:
    user = _find_user_by_email(db, email)
    if user is None or not user.is_active:
        return

    settings = get_settings()
    expires_at = utcnow() + timedelta(minutes=settings.reset_password_ttl_minutes)
    token = _issue_email_token(db, user.id, AuthTokenType.reset_password, expires_at)
    link = f"{settings.app_url}/reset-password?token={quote(token)}"
    mailer.send(
        Mail(
            to=user.email,
            subject="Reset your password",
            text=(
                f"Hello {user.name},\n\n"
                "Someone asked to reset the password of your account. "
                f"To choose a new password, open:\n{link}\n\n"
                f"The link works once and expires in {settings.reset_password_ttl_minutes} minutes. "
                "If you did not ask for this, ignore this email."
            ),
        )
    )


def is_reset_token_valid(db: Session, token: str) -> bool:
    row = db.scalar(select(AuthToken).where(AuthToken.token_hash == hash_token(token)))
    return bool(
        row
        and row.type == AuthTokenType.reset_password
        and row.used_at is None
        and row.expires_at > utcnow()
        and row.user.is_active
    )


def reset_password(db: Session, token: str, new_password: str) -> None:
    user_id = _consume_email_token(db, token, AuthTokenType.reset_password)
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise invalid_link()

    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    if user.email_verified_at is None:
        user.email_verified_at = utcnow()
    db.commit()
    revoke_all_sessions(db, user.id)
