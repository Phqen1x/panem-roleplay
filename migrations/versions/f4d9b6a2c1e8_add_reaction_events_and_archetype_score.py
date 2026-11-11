"""add reaction event log and character archetype_score

Revision ID: f4d9b6a2c1e8
Revises: e1c8a3f6b9d4
Create Date: 2026-11-11 00:00:03.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f4d9b6a2c1e8"
down_revision: str | None = "e1c8a3f6b9d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "characters",
        sa.Column("archetype_score", sa.Float(), nullable=False, server_default="0"),
    )
    op.alter_column("characters", "archetype_score", server_default=None)

    op.create_table(
        "reaction_event_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "character_id", sa.Integer(), sa.ForeignKey("characters.id"), nullable=False
        ),
        sa.Column("scenario_id", sa.String(length=64), nullable=False),
        sa.Column("choice", sa.String(length=16), nullable=True),
        sa.Column("tick", sa.Integer(), nullable=False),
        sa.Column("archetype_score_after", sa.Float(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_reaction_event_log_character_id", "reaction_event_log", ["character_id"])
    op.alter_column("reaction_event_log", "archetype_score_after", server_default=None)


def downgrade() -> None:
    op.drop_table("reaction_event_log")
    op.drop_column("characters", "archetype_score")
