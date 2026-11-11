"""add personal event chains and global event tables

Revision ID: d6b2f9c4a1e7
Revises: c3a7b5f1e9d2
Create Date: 2026-11-11 00:00:01.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d6b2f9c4a1e7"
down_revision: str | None = "c3a7b5f1e9d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "personal_event_chains",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "character_id", sa.Integer(), sa.ForeignKey("characters.id"), nullable=False
        ),
        sa.Column("chain_kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("step", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "state", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"
        ),
        sa.Column("started_tick", sa.Integer(), nullable=False),
        sa.Column("updated_tick", sa.Integer(), nullable=False),
        sa.Column("expires_tick", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_personal_event_chains_character_id", "personal_event_chains", ["character_id"]
    )
    op.alter_column("personal_event_chains", "status", server_default=None)
    op.alter_column("personal_event_chains", "step", server_default=None)
    op.alter_column("personal_event_chains", "state", server_default=None)

    op.create_table(
        "global_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("hobby_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("district_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="scheduled"),
        sa.Column("starts_tick", sa.Integer(), nullable=False),
        sa.Column("ends_tick", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_global_events_district_id", "global_events", ["district_id"])
    op.create_index("ix_global_events_status", "global_events", ["status"])
    op.alter_column("global_events", "description", server_default=None)
    op.alter_column("global_events", "status", server_default=None)

    op.create_table(
        "global_event_entries",
        sa.Column(
            "event_id", sa.Integer(), sa.ForeignKey("global_events.id"), primary_key=True
        ),
        sa.Column(
            "character_id", sa.Integer(), sa.ForeignKey("characters.id"), primary_key=True
        ),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rewarded", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("global_event_entries", "score", server_default=None)
    op.alter_column("global_event_entries", "attempts", server_default=None)
    op.alter_column("global_event_entries", "rewarded", server_default=None)


def downgrade() -> None:
    op.drop_table("global_event_entries")
    op.drop_table("global_events")
    op.drop_table("personal_event_chains")
