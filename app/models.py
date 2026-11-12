import enum
from datetime import UTC, datetime

from sqlalchemy import CHAR, Boolean, DateTime, Enum, ForeignKey, Index, Integer, String
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# DATETIME(3) on MySQL, like the existing tables. Values are stored as naive UTC.
Timestamp = DateTime().with_variant(mysql.DATETIME(fsp=3), "mysql")


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Role(enum.StrEnum):
    employee = "employee"
    manager = "manager"
    admin = "admin"
    super_admin = "super_admin"


class AuthTokenType(enum.StrEnum):
    verify_email = "verify_email"
    reset_password = "reset_password"


class Department(Base):
    __tablename__ = "departments"
    __table_args__ = (Index("departments_parent_id_idx", "parent_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "departments.id", name="departments_parent_id_fkey", ondelete="SET NULL", onupdate="CASCADE"
        )
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow, onupdate=utcnow)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(
        Enum(Role, name="role", values_callable=lambda e: [m.value for m in e]),
        default=Role.employee,
        server_default=Role.employee.value,
    )
    department_id: Mapped[int | None] = mapped_column(
        ForeignKey("departments.id", name="users_department_id_fkey", ondelete="SET NULL", onupdate="CASCADE")
    )
    manager_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", name="users_manager_id_fkey", ondelete="SET NULL", onupdate="CASCADE")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    email_verified_at: Mapped[datetime | None] = mapped_column(Timestamp)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow, onupdate=utcnow)

    department: Mapped[Department | None] = relationship(lazy="joined")
    manager: Mapped["User | None"] = relationship(remote_side="User.id", lazy="joined", join_depth=1)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    __table_args__ = (Index("refresh_tokens_user_id_idx", "user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", name="refresh_tokens_user_id_fkey", ondelete="RESTRICT", onupdate="CASCADE")
    )
    token_hash: Mapped[str] = mapped_column(CHAR(64), unique=True)
    user_agent: Mapped[str | None] = mapped_column(String(255))
    expires_at: Mapped[datetime] = mapped_column(Timestamp)
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)

    user: Mapped[User] = relationship(lazy="joined")


class AuthToken(Base):
    __tablename__ = "auth_tokens"
    __table_args__ = (Index("auth_tokens_user_id_type_idx", "user_id", "type"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", name="auth_tokens_user_id_fkey", ondelete="RESTRICT", onupdate="CASCADE")
    )
    type: Mapped[AuthTokenType] = mapped_column(
        Enum(AuthTokenType, name="auth_token_type", values_callable=lambda e: [m.value for m in e])
    )
    token_hash: Mapped[str] = mapped_column(CHAR(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(Timestamp)
    used_at: Mapped[datetime | None] = mapped_column(Timestamp)
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)

    user: Mapped[User] = relationship(lazy="joined")
