"""Initial tables: departments, users, refresh_tokens, auth_tokens

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_ARGS = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}


def _timestamp(name: str, nullable: bool = False, default_now: bool = False) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime().with_variant(mysql.DATETIME(fsp=3), "mysql"),
        nullable=nullable,
        server_default=sa.text("CURRENT_TIMESTAMP(3)") if default_now else None,
    )


def upgrade() -> None:
    op.create_table(
        "departments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        _timestamp("created_at", default_now=True),
        _timestamp("updated_at"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("parent_id", "name", name="departments_parent_id_name_key"),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["departments.id"],
            name="departments_parent_id_fkey",
            ondelete="SET NULL",
            onupdate="CASCADE",
        ),
        **TABLE_ARGS,
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column(
            "role",
            sa.Enum("employee", "manager", "admin", "super_admin", name="role"),
            nullable=False,
            server_default="employee",
        ),
        sa.Column("department_id", sa.Integer(), nullable=True),
        sa.Column("manager_id", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        _timestamp("email_verified_at", nullable=True),
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        _timestamp("created_at", default_now=True),
        _timestamp("updated_at"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="users_email_key"),
        sa.ForeignKeyConstraint(
            ["department_id"],
            ["departments.id"],
            name="users_department_id_fkey",
            ondelete="SET NULL",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["manager_id"],
            ["users.id"],
            name="users_manager_id_fkey",
            ondelete="SET NULL",
            onupdate="CASCADE",
        ),
        **TABLE_ARGS,
    )

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.CHAR(64), nullable=False),
        sa.Column("user_agent", sa.String(255), nullable=True),
        _timestamp("expires_at"),
        _timestamp("created_at", default_now=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="refresh_tokens_token_hash_key"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="refresh_tokens_user_id_fkey",
            ondelete="RESTRICT",
            onupdate="CASCADE",
        ),
        **TABLE_ARGS,
    )
    op.create_index("refresh_tokens_user_id_idx", "refresh_tokens", ["user_id"])

    op.create_table(
        "auth_tokens",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("type", sa.Enum("verify_email", "reset_password", name="auth_token_type"), nullable=False),
        sa.Column("token_hash", sa.CHAR(64), nullable=False),
        _timestamp("expires_at"),
        _timestamp("used_at", nullable=True),
        _timestamp("created_at", default_now=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="auth_tokens_token_hash_key"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="auth_tokens_user_id_fkey",
            ondelete="RESTRICT",
            onupdate="CASCADE",
        ),
        **TABLE_ARGS,
    )
    op.create_index("auth_tokens_user_id_type_idx", "auth_tokens", ["user_id", "type"])


def downgrade() -> None:
    op.drop_table("auth_tokens")
    op.drop_table("refresh_tokens")
    op.drop_table("users")
    op.drop_table("departments")
