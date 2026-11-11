"""add rumors and bulletin tables

Revision ID: a7e3c9f2b5d1
Revises: f4d9b6a2c1e8
Create Date: 2026-11-11 00:00:04.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7e3c9f2b5d1"
down_revision: str | None = "f4d9b6a2c1e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rumors",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("district_id", sa.Integer(), nullable=False),
        sa.Column("text", sa.String(length=300), nullable=False),
        sa.Column("reliability_pct", sa.Integer(), nullable=False),
        sa.Column("related_kind", sa.String(length=32), nullable=True),
        sa.Column("related_id", sa.Integer(), nullable=True),
        sa.Column("origin", sa.String(length=64), nullable=False, server_default="overheard"),
        sa.Column("spawned_tick", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_rumors_district_id", "rumors", ["district_id"])
    op.alter_column("rumors", "origin", server_default=None)

    op.create_table(
        "rumor_investigations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("rumor_id", sa.Integer(), sa.ForeignKey("rumors.id"), nullable=False),
        sa.Column(
            "character_id", sa.Integer(), sa.ForeignKey("characters.id"), nullable=False
        ),
        sa.Column("finding", sa.String(length=300), nullable=False),
        sa.Column("reliability_delta", sa.Integer(), nullable=False),
        sa.Column("tick", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_rumor_investigations_rumor_id", "rumor_investigations", ["rumor_id"])
    op.create_index(
        "ix_rumor_investigations_character_id", "rumor_investigations", ["character_id"]
    )

    op.create_table(
        "bulletin_entries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("tag", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("body", sa.String(length=600), nullable=False, server_default=""),
        sa.Column("district_id", sa.Integer(), nullable=True),
        sa.Column("location_id", sa.String(length=64), nullable=True),
        sa.Column("posted_tick", sa.Integer(), nullable=False),
        sa.Column("event_tick", sa.Integer(), nullable=True),
        sa.Column("requirements", sa.String(length=200), nullable=True),
        sa.Column("danger_level", sa.Integer(), nullable=True),
        sa.Column("posted_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("retracted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_bulletin_entries_district_id", "bulletin_entries", ["district_id"])
    op.alter_column("bulletin_entries", "body", server_default=None)
    op.alter_column("bulletin_entries", "retracted", server_default=None)

    op.create_table(
        "bulletin_user_state",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column(
            "entry_id", sa.Integer(), sa.ForeignKey("bulletin_entries.id"), primary_key=True
        ),
        sa.Column("interested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("category", sa.String(length=32), nullable=True),
        sa.Column("sort_position", sa.Integer(), nullable=True),
    )
    op.alter_column("bulletin_user_state", "interested", server_default=None)

    op.create_table(
        "bulletin_reminders",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "entry_id", sa.Integer(), sa.ForeignKey("bulletin_entries.id"), nullable=False
        ),
        sa.Column("remind_at_tick", sa.Integer(), nullable=False),
        sa.UniqueConstraint("user_id", "entry_id", name="uq_bulletin_reminder_once_per_user"),
    )
    op.create_index("ix_bulletin_reminders_user_id", "bulletin_reminders", ["user_id"])
    op.create_index(
        "ix_bulletin_reminders_remind_at_tick", "bulletin_reminders", ["remind_at_tick"]
    )


def downgrade() -> None:
    op.drop_table("bulletin_reminders")
    op.drop_table("bulletin_user_state")
    op.drop_table("bulletin_entries")
    op.drop_table("rumor_investigations")
    op.drop_table("rumors")
