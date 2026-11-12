"""Departments: name unique among active siblings, top level included

Revision ID: 0002_departments_unique_name
Revises: 0001_initial
Create Date: 2026-11-09
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002_departments_unique_name"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The foreign key on parent_id needs an index that starts with parent_id.
    # The old unique constraint provided it, so add a plain index before dropping it.
    op.create_index("departments_parent_id_idx", "departments", ["parent_id"])
    op.drop_constraint("departments_parent_id_name_key", "departments", type_="unique")
    # A NULL parent counts as 0. An inactive row gets a NULL name part, so it never collides.
    op.execute(
        "ALTER TABLE departments ADD UNIQUE INDEX departments_active_name_uq "
        "((IFNULL(parent_id, 0)), (CASE WHEN is_active THEN name END))"
    )


def downgrade() -> None:
    op.drop_index("departments_active_name_uq", table_name="departments")
    op.create_unique_constraint(
        "departments_parent_id_name_key", "departments", ["parent_id", "name"]
    )
    op.drop_index("departments_parent_id_idx", table_name="departments")
