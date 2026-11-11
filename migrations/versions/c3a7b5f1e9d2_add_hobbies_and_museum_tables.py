"""add hobbies and museum tables

Revision ID: c3a7b5f1e9d2
Revises: a4c8e1f6b2d9
Create Date: 2026-11-11 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3a7b5f1e9d2"
down_revision: str | None = "a4c8e1f6b2d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "character_hobbies",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "character_id", sa.Integer(), sa.ForeignKey("characters.id"), nullable=False
        ),
        sa.Column("hobby_id", sa.String(length=32), nullable=False),
        sa.Column("sessions_played", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_played_tick", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("character_id", "hobby_id", name="uq_character_hobby_once"),
    )
    op.create_index("ix_character_hobbies_character_id", "character_hobbies", ["character_id"])
    op.alter_column("character_hobbies", "sessions_played", server_default=None)

    op.create_table(
        "collectible_instances",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "owner_character_id", sa.Integer(), sa.ForeignKey("characters.id"), nullable=False
        ),
        sa.Column("collectible_id", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("found_tick", sa.Integer(), nullable=False),
        sa.Column("found_district_id", sa.Integer(), nullable=True),
        sa.Column("donated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_collectible_instances_owner_character_id",
        "collectible_instances",
        ["owner_character_id"],
    )
    op.create_index(
        "ix_collectible_instances_collectible_id", "collectible_instances", ["collectible_id"]
    )

    op.create_table(
        "museum_slots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "owner_character_id", sa.Integer(), sa.ForeignKey("characters.id"), nullable=False
        ),
        sa.Column(
            "collectible_instance_id",
            sa.Integer(),
            sa.ForeignKey("collectible_instances.id"),
            nullable=False,
        ),
        sa.Column("wing", sa.String(length=32), nullable=False, server_default="General"),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("donated_tick", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "collectible_instance_id", name="uq_museum_slot_one_per_instance"
        ),
    )
    op.create_index("ix_museum_slots_owner_character_id", "museum_slots", ["owner_character_id"])
    op.alter_column("museum_slots", "wing", server_default=None)
    op.alter_column("museum_slots", "position", server_default=None)


def downgrade() -> None:
    op.drop_table("museum_slots")
    op.drop_table("collectible_instances")
    op.drop_table("character_hobbies")
