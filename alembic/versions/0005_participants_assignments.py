"""Participants, assignments and answers: cycle_participants, assignments, answers

Revision ID: 0005_participants_assignments
Revises: 0004_question_bank_templates
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision: str = "0005_participants_assignments"
down_revision: str | None = "0004_question_bank_templates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_ARGS = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}

# Same list and order as templates.review_type (0004). MySQL refuses a foreign key between
# two ENUM columns whose lists differ.
REVIEW_TYPES = ("self", "manager", "peer", "upward")


def _timestamp(name: str, nullable: bool = False) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime().with_variant(mysql.DATETIME(fsp=3), "mysql"),
        nullable=nullable,
        server_default=None if nullable else sa.text("CURRENT_TIMESTAMP(3)"),
    )


def upgrade() -> None:
    # The snapshot of one cycle: where each person worked and who managed them when it opened (C6).
    # There is no foreign key from manager_id to this table. A bulk insert would fail row by row,
    # because a manager's row may not exist yet. The service checks "the manager is a participant".
    op.create_table(
        "cycle_participants",
        sa.Column("cycle_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("department_id", sa.Integer(), nullable=False),
        sa.Column("manager_id", sa.Integer(), nullable=True),
        _timestamp("created_at"),
        sa.PrimaryKeyConstraint("cycle_id", "user_id"),
        sa.ForeignKeyConstraint(
            ["cycle_id"],
            ["cycles.id"],
            name="cycle_participants_cycle_id_fkey",
            ondelete="RESTRICT",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="cycle_participants_user_id_fkey",
            ondelete="RESTRICT",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["department_id"],
            ["departments.id"],
            name="cycle_participants_department_id_fkey",
            ondelete="RESTRICT",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["manager_id"],
            ["users.id"],
            name="cycle_participants_manager_id_fkey",
            ondelete="RESTRICT",
            onupdate="CASCADE",
        ),
        **TABLE_ARGS,
    )
    # Each foreign key column leads one index. The two composite ones also serve the list filters.
    op.create_index("cycle_participants_user_id_idx", "cycle_participants", ["user_id"])
    op.create_index(
        "cycle_participants_department_id_idx",
        "cycle_participants",
        ["department_id", "cycle_id"],
    )
    op.create_index(
        "cycle_participants_manager_id_idx",
        "cycle_participants",
        ["manager_id", "cycle_id"],
    )

    # One review to write: reviewer writes about reviewee, in one cycle, of one type.
    # The foreign keys use RESTRICT for both actions. MySQL refuses a CHECK on a column that
    # takes part in a CASCADE action, and reviewer_id, reviewee_id and review_type are in checks.
    op.create_table(
        "assignments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("cycle_id", sa.Integer(), nullable=False),
        sa.Column("reviewer_id", sa.Integer(), nullable=False),
        sa.Column("reviewee_id", sa.Integer(), nullable=False),
        sa.Column(
            "review_type", sa.Enum(*REVIEW_TYPES, name="review_type"), nullable=False
        ),
        sa.Column(
            "status",
            sa.Enum("pending", "draft", "submitted", name="assignment_status"),
            nullable=False,
            server_default="pending",
        ),
        _timestamp("submitted_at", nullable=True),
        _timestamp("created_at"),
        sa.PrimaryKeyConstraint("id"),
        # P3: the same review is never assigned twice. Its index also serves the reviewer key below.
        sa.UniqueConstraint(
            "cycle_id",
            "reviewer_id",
            "reviewee_id",
            "review_type",
            name="assignments_cycle_reviewer_reviewee_type_key",
        ),
        sa.ForeignKeyConstraint(
            ["cycle_id", "reviewer_id"],
            ["cycle_participants.cycle_id", "cycle_participants.user_id"],
            name="assignments_reviewer_fkey",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["cycle_id", "reviewee_id"],
            ["cycle_participants.cycle_id", "cycle_participants.user_id"],
            name="assignments_reviewee_fkey",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        # The cycle must have a template of this type. There is no template_id column.
        sa.ForeignKeyConstraint(
            ["cycle_id", "review_type"],
            ["templates.cycle_id", "templates.review_type"],
            name="assignments_template_fkey",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        # A self review is about yourself. Every other type is about someone else.
        sa.CheckConstraint(
            "(review_type = 'self' AND reviewer_id = reviewee_id)"
            " OR (review_type <> 'self' AND reviewer_id <> reviewee_id)",
            name="assignments_people_check",
        ),
        # submitted_at is set exactly when the status is submitted.
        sa.CheckConstraint(
            "(status = 'submitted' AND submitted_at IS NOT NULL) OR (status <> 'submitted' AND submitted_at IS NULL)",
            name="assignments_submitted_at_check",
        ),
        **TABLE_ARGS,
    )
    op.create_index(
        "assignments_reviewee_idx",
        "assignments",
        ["cycle_id", "reviewee_id", "review_type"],
    )
    op.create_index(
        "assignments_type_status_idx",
        "assignments",
        ["cycle_id", "review_type", "status"],
    )

    # The saved answers of one assignment. This step creates the table only. Cluster 8 uses it
    # and may adjust the columns.
    op.create_table(
        "answers",
        sa.Column("assignment_id", sa.Integer(), nullable=False),
        sa.Column("question_id", sa.Integer(), nullable=False),
        sa.Column("rating", sa.SmallInteger(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.PrimaryKeyConstraint("assignment_id", "question_id"),
        # Removing an assignment removes its answers (a removed peer, a refreshed snapshot).
        sa.ForeignKeyConstraint(
            ["assignment_id"],
            ["assignments.id"],
            name="answers_assignment_id_fkey",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["question_id"],
            ["questions.id"],
            name="answers_question_id_fkey",
            ondelete="RESTRICT",
            onupdate="CASCADE",
        ),
        sa.CheckConstraint(
            "rating IS NULL OR (rating >= 1 AND rating <= 5)",
            name="answers_rating_check",
        ),
        **TABLE_ARGS,
    )
    op.create_index("answers_question_id_idx", "answers", ["question_id"])


def downgrade() -> None:
    op.drop_table("answers")
    op.drop_table("assignments")
    op.drop_table("cycle_participants")
