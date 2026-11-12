"""Cycles: the review cycle table

Revision ID: 0003_cycles
Revises: 0002_departments_unique_name
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_cycles"
down_revision: str | None = "0002_departments_unique_name"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_ARGS = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}


def upgrade() -> None:
    op.create_table(
        "cycles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column(
            "type", sa.Enum("annual", "midyear", name="cycle_type"), nullable=False
        ),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("draft", "open", "closed", "finalized", name="cycle_status"),
            nullable=False,
            server_default="draft",
        ),
        # 1 for an open cycle, NULL for any other. A unique index on it allows only one open cycle.
        sa.Column(
            "open_marker",
            sa.Integer(),
            sa.Computed("CASE WHEN status = 'open' THEN 1 END", persisted=True),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("year", "type", name="cycles_year_type_key"),
        sa.UniqueConstraint("open_marker", name="cycles_one_open_key"),
        sa.CheckConstraint("start_date <= end_date", name="cycles_dates_check"),
        **TABLE_ARGS,
    )


def downgrade() -> None:
    op.drop_table("cycles")
