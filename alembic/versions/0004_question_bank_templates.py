"""Question bank and templates: criteria, questions, templates, template_questions

Revision ID: 0004_question_bank_templates
Revises: 0003_cycles
Create Date: 2026-11-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision: str = "0004_question_bank_templates"
down_revision: str | None = "0003_cycles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_ARGS = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}

REVIEW_TYPES = ("self", "manager", "peer", "upward")
DEFAULT_TEMPLATE_NAMES = {
    "self": "Self review",
    "manager": "Manager review",
    "peer": "Peer review",
    "upward": "Upward review",
}


def _timestamp(name: str, nullable: bool = False) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime().with_variant(mysql.DATETIME(fsp=3), "mysql"),
        nullable=nullable,
        server_default=None if nullable else sa.text("CURRENT_TIMESTAMP(3)"),
    )


def upgrade() -> None:
    op.create_table(
        "criteria",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        # The name in lower case with all spaces removed (Q1). Lower-casing can triple the length.
        sa.Column("name_key", sa.String(360), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name_key", name="criteria_name_key"),
        **TABLE_ARGS,
    )

    op.create_table(
        "questions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("criterion_id", sa.Integer(), nullable=False),
        sa.Column("text", sa.String(500), nullable=False),
        sa.Column("kind", sa.Enum("rating", "text", name="question_kind"), nullable=False),
        _timestamp("archived_at", nullable=True),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["criterion_id"],
            ["criteria.id"],
            name="questions_criterion_id_fkey",
            ondelete="RESTRICT",
            onupdate="CASCADE",
        ),
        **TABLE_ARGS,
    )
    op.create_index("questions_criterion_id_idx", "questions", ["criterion_id"])

    templates = op.create_table(
        "templates",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("cycle_id", sa.Integer(), nullable=False),
        sa.Column("review_type", sa.Enum(*REVIEW_TYPES, name="review_type"), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("copied_from_id", sa.Integer(), nullable=True),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cycle_id", "review_type", name="templates_cycle_id_review_type_key"),
        # Deleting a draft cycle deletes its templates (CYC-8).
        sa.ForeignKeyConstraint(
            ["cycle_id"], ["cycles.id"], name="templates_cycle_id_fkey", ondelete="CASCADE", onupdate="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["copied_from_id"],
            ["templates.id"],
            name="templates_copied_from_id_fkey",
            ondelete="SET NULL",
            onupdate="CASCADE",
        ),
        **TABLE_ARGS,
    )

    op.create_table(
        "template_questions",
        sa.Column("template_id", sa.Integer(), nullable=False),
        sa.Column("question_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        # NULL for a text question (T5).
        sa.Column("weight", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("template_id", "question_id"),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["templates.id"],
            name="template_questions_template_id_fkey",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["question_id"],
            ["questions.id"],
            name="template_questions_question_id_fkey",
            ondelete="RESTRICT",
            onupdate="CASCADE",
        ),
        sa.CheckConstraint("weight IS NULL OR weight >= 1", name="template_questions_weight_check"),
        sa.CheckConstraint("position >= 1", name="template_questions_position_check"),
        **TABLE_ARGS,
    )
    op.create_index("template_questions_question_id_idx", "template_questions", ["question_id"])

    # T9: every cycle owns four templates. Give the cycles that already exist their empty ones.
    cycle_ids = [row[0] for row in op.get_bind().execute(sa.text("SELECT id FROM cycles ORDER BY id"))]
    if cycle_ids:
        op.bulk_insert(
            templates,
            [
                {"cycle_id": cycle_id, "review_type": review_type, "name": DEFAULT_TEMPLATE_NAMES[review_type]}
                for cycle_id in cycle_ids
                for review_type in REVIEW_TYPES
            ],
        )


def downgrade() -> None:
    op.drop_table("template_questions")
    op.drop_table("templates")
    op.drop_table("questions")
    op.drop_table("criteria")
