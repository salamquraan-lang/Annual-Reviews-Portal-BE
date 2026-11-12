import re
from typing import Annotated

from email_validator import EmailNotValidError, validate_email
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationInfo,
    field_validator,
)

from app.models import Role
from app.security import BCRYPT_MAX_BYTES


def _normalize_email(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Enter a valid email address.")
    email = value.strip().lower()
    try:
        validate_email(email, check_deliverability=False, test_environment=True)
    except EmailNotValidError:
        raise ValueError("Enter a valid email address.") from None
    if len(email) > 255:
        raise ValueError("Enter a valid email address.")
    return email


def _check_password(value: str) -> str:
    if len(value) < 8:
        raise ValueError("Password must be at least 8 characters.")
    if len(value.encode()) > BCRYPT_MAX_BYTES:
        raise ValueError("Password must be at most 72 characters.")
    if not re.search(r"[A-Za-z]", value):
        raise ValueError("Password must contain a letter.")
    if not re.search(r"[0-9]", value):
        raise ValueError("Password must contain a digit.")
    return value


def _check_name(value: str) -> str:
    value = value.strip()
    if len(value) < 2:
        raise ValueError("Name must be at least 2 characters.")
    if len(value) > 120:
        raise ValueError("Name must be at most 120 characters.")
    return value


def _required(message: str):
    def check(value: str) -> str:
        if not value:
            raise ValueError(message)
        return value

    return check


Email = Annotated[str, BeforeValidator(_normalize_email)]
NewPassword = Annotated[str, AfterValidator(_check_password)]
Token = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=200),
    AfterValidator(_required("Token is required.")),
]


class SignupRequest(BaseModel):
    name: Annotated[str, AfterValidator(_check_name)]
    email: Email
    password: NewPassword


class LoginRequest(BaseModel):
    email: Email
    password: Annotated[
        str, StringConstraints(max_length=200), AfterValidator(_required("Password is required."))
    ]


class EmailRequest(BaseModel):
    email: Email


class TokenRequest(BaseModel):
    token: Token


class ChangePasswordRequest(BaseModel):
    current_password: Annotated[
        str,
        StringConstraints(max_length=200),
        AfterValidator(_required("Current password is required.")),
        Field(alias="currentPassword"),
    ]
    new_password: Annotated[NewPassword, Field(alias="newPassword")]

    @field_validator("new_password")
    @classmethod
    def _new_differs(cls, value: str, info: ValidationInfo) -> str:
        if info.data.get("current_password") == value:
            raise ValueError("The new password must be different from the current one.")
        return value


class ResetPasswordRequest(BaseModel):
    token: Token
    new_password: Annotated[NewPassword, Field(alias="newPassword")]


class MessageResponse(BaseModel):
    message: str


class SessionResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    access_token: str = Field(serialization_alias="accessToken")
    must_change_password: bool = Field(serialization_alias="mustChangePassword")


class ValidResponse(BaseModel):
    valid: bool


class NamedRef(BaseModel):
    id: int
    name: str


class Profile(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    name: str
    email: str
    role: Role
    department: NamedRef | None
    manager: NamedRef | None
    email_verified: bool = Field(serialization_alias="emailVerified")
    must_change_password: bool = Field(serialization_alias="mustChangePassword")
