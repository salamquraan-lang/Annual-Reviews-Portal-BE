from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Query, Request, Response, status
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.dependencies import CurrentUserAllowPending, DbSession, get_mailer
from app.errors import AppError, error_response
from app.mailer import Mailer
from app.rate_limit import limiter, too_many_requests
from app.schemas import (
    ChangePasswordRequest,
    EmailRequest,
    LoginRequest,
    MessageResponse,
    ResetPasswordRequest,
    SessionResponse,
    SignupRequest,
    TokenRequest,
    ValidResponse,
)
from app.services import auth

router = APIRouter(prefix="/api/auth")

REFRESH_COOKIE = "refresh_token"
COOKIE_PATH = "/api/auth"

MailerDep = Annotated[Mailer, Depends(get_mailer)]
RefreshCookie = Annotated[str | None, Cookie(alias=REFRESH_COOKIE)]


def _set_refresh_cookie(response: Response, session: auth.AuthSession) -> None:
    response.set_cookie(
        REFRESH_COOKIE,
        session.refresh_token,
        httponly=True,
        secure=get_settings().is_production,
        samesite="strict",
        path=COOKIE_PATH,
        max_age=get_settings().refresh_token_ttl_days * 24 * 3600,
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(REFRESH_COOKIE, path=COOKIE_PATH)


def _session_body(session: auth.AuthSession) -> SessionResponse:
    return SessionResponse(
        access_token=session.access_token, must_change_password=session.must_change_password
    )


@router.post("/signup", status_code=status.HTTP_202_ACCEPTED)
def signup(body: SignupRequest, db: DbSession, mailer: MailerDep) -> MessageResponse:
    auth.signup(db, mailer, body.name, body.email, body.password)
    return MessageResponse(
        message="If this email can be used, we have sent a verification link to it. Check your inbox."
    )


@router.post("/verify-email")
def verify_email(body: TokenRequest, db: DbSession) -> MessageResponse:
    auth.verify_email(db, body.token)
    return MessageResponse(message="Your email is verified. You can log in now.")


@router.post("/resend-verification", status_code=status.HTTP_202_ACCEPTED)
def resend_verification(body: EmailRequest, db: DbSession, mailer: MailerDep) -> MessageResponse:
    auth.resend_verification(db, mailer, body.email)
    return MessageResponse(message="If this account needs verification, we have sent a new link.")


@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response, db: DbSession) -> SessionResponse:
    settings = get_settings()
    window = settings.rate_limit_window_minutes * 60
    email_key = f"login:{body.email}"
    wait = limiter.retry_after(email_key, settings.auth_rate_limit_max, window)
    if wait:
        raise too_many_requests(wait)
    try:
        session = auth.login(db, body.email, body.password, request.headers.get("user-agent"))
    except AppError:
        limiter.hit(email_key, window)
        raise
    _set_refresh_cookie(response, session)
    return _session_body(session)


@router.post(
    "/refresh",
)
def refresh(request: Request, db: DbSession, refresh_token: RefreshCookie = None) -> Response:
    try:
        session = auth.refresh(db, refresh_token, request.headers.get("user-agent"))
    except AppError as err:
        failed = error_response(err)
        _clear_refresh_cookie(failed)
        return failed
    response = JSONResponse(_session_body(session).model_dump(by_alias=True))
    _set_refresh_cookie(response, session)
    return response


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response, db: DbSession, refresh_token: RefreshCookie = None) -> None:
    auth.logout(db, refresh_token)
    _clear_refresh_cookie(response)


@router.post(
    "/logout-all",
    status_code=status.HTTP_204_NO_CONTENT,
)
def logout_all(response: Response, user: CurrentUserAllowPending, db: DbSession) -> None:
    auth.revoke_all_sessions(db, user.id)
    _clear_refresh_cookie(response)


@router.post("/change-password")
def change_password(
    body: ChangePasswordRequest,
    user: CurrentUserAllowPending,
    db: DbSession,
    refresh_token: RefreshCookie = None,
) -> MessageResponse:
    auth.change_password(db, user, body.current_password, body.new_password, refresh_token)
    return MessageResponse(message="Your password has been changed. Your other devices were logged out.")


@router.post("/forgot-password", status_code=status.HTTP_202_ACCEPTED)
def forgot_password(body: EmailRequest, db: DbSession, mailer: MailerDep) -> MessageResponse:
    auth.forgot_password(db, mailer, body.email)
    return MessageResponse(message="If this email is registered, we have sent a link to reset the password.")


@router.get("/reset-password/validate")
def validate_reset_token(db: DbSession, token: Annotated[str, Query()] = "") -> ValidResponse:
    return ValidResponse(valid=bool(token) and auth.is_reset_token_valid(db, token))


@router.post("/reset-password")
def reset_password(body: ResetPasswordRequest, response: Response, db: DbSession) -> MessageResponse:
    auth.reset_password(db, body.token, body.new_password)
    _clear_refresh_cookie(response)
    return MessageResponse(message="Your password has been reset. You can log in with the new password.")
