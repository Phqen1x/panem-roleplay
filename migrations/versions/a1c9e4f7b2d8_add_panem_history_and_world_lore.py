"""add panem history entries and world lore settings

Revision ID: a1c9e4f7b2d8
Revises: 4826400ed79f
Create Date: 2026-09-24 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a1c9e4f7b2d8"
down_revision: str | None = "4826400ed79f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "panem_history_entries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "keywords",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_by_staff_discord_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.alter_column("panem_history_entries", "keywords", server_default=None)

    op.create_table(
        "world_lore_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("alternate_universe_notes", sa.Text(), nullable=False, server_default=""),
        sa.PrimaryKeyConstraint("id"),
    )
    op.alter_column("world_lore_settings", "alternate_universe_notes", server_default=None)


def downgrade() -> None:
    op.drop_table("world_lore_settings")
    op.drop_table("panem_history_entries")
