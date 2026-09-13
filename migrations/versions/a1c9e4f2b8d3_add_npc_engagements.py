"""add npc engagements

Revision ID: a1c9e4f2b8d3
Revises: f6a3d8b2c7e5
Create Date: 2026-09-13 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1c9e4f2b8d3"
down_revision: str | None = "f6a3d8b2c7e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "engagement_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("idle_timeout_minutes", sa.Integer(), nullable=False),
    )
    op.execute("INSERT INTO engagement_settings (id, idle_timeout_minutes) VALUES (1, 30)")

    op.add_column("npcs", sa.Column("engagement_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_npcs_engagement_id",
        "npcs",
        "scenes",
        ["engagement_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_npcs_engagement_id", "npcs", type_="foreignkey")
    op.drop_column("npcs", "engagement_id")
    op.drop_table("engagement_settings")
