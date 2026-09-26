"""add rp modes + needs meters to characters; affliction_types, character_afflictions, trades tables

Revision ID: 4826400ed79f
Revises: a2520b13ef46
Create Date: 2026-09-23 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4826400ed79f"
down_revision: str | None = "a2520b13ef46"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing rows backfill to "simulation" -- the RP-modes feature's
    # gating is all additive, so this preserves every already-existing
    # character's current behavior exactly.
    op.add_column(
        "characters",
        sa.Column("rp_mode", sa.String(length=16), nullable=False, server_default="simulation"),
    )
    op.add_column(
        "characters", sa.Column("rp_mode_changed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "characters",
        sa.Column("crime_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "characters",
        sa.Column("crime_toggle_changed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("characters", sa.Column("thirst", sa.Float(), nullable=False, server_default="0"))
    op.add_column(
        "characters", sa.Column("sanity", sa.Float(), nullable=False, server_default="100")
    )
    op.add_column("characters", sa.Column("last_ate_tick", sa.Integer(), nullable=True))
    op.add_column("characters", sa.Column("last_drank_tick", sa.Integer(), nullable=True))
    op.add_column("characters", sa.Column("last_entertained_tick", sa.Integer(), nullable=True))
    op.add_column("characters", sa.Column("death_cause", sa.String(length=400), nullable=True))

    op.create_table(
        "affliction_types",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=64), nullable=False, unique=True),
        sa.Column("description", sa.String(length=400), nullable=False, server_default=""),
        sa.Column("is_permanent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("cure_stat", sa.String(length=16), nullable=True),
        sa.Column("cure_threshold", sa.Float(), nullable=True),
        sa.Column("auto_apply_stat", sa.String(length=16), nullable=True),
        sa.Column("auto_apply_threshold", sa.Float(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )

    op.create_table(
        "character_afflictions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("character_id", sa.Integer(), nullable=False),
        sa.Column(
            "affliction_type_id",
            sa.Integer(),
            sa.ForeignKey("affliction_types.id"),
            nullable=False,
        ),
        sa.Column("cause", sa.String(length=400), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column(
            "applied_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("cured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_character_afflictions_character_id", "character_afflictions", ["character_id"]
    )
    op.create_index(
        "ix_character_afflictions_affliction_type_id",
        "character_afflictions",
        ["affliction_type_id"],
    )

    op.create_table(
        "trades",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("initiator_character_id", sa.Integer(), nullable=False),
        sa.Column("recipient_character_id", sa.Integer(), nullable=False),
        sa.Column("give_good_id", sa.String(length=64), nullable=True),
        sa.Column("give_qty", sa.Integer(), nullable=True),
        sa.Column("give_money", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("want_good_id", sa.String(length=64), nullable=True),
        sa.Column("want_qty", sa.Integer(), nullable=True),
        sa.Column("want_money", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_trades_initiator_character_id", "trades", ["initiator_character_id"])
    op.create_index("ix_trades_recipient_character_id", "trades", ["recipient_character_id"])
    op.create_index("ix_trades_status", "trades", ["status"])


def downgrade() -> None:
    op.drop_index("ix_trades_status", table_name="trades")
    op.drop_index("ix_trades_recipient_character_id", table_name="trades")
    op.drop_index("ix_trades_initiator_character_id", table_name="trades")
    op.drop_table("trades")

    op.drop_index("ix_character_afflictions_affliction_type_id", table_name="character_afflictions")
    op.drop_index("ix_character_afflictions_character_id", table_name="character_afflictions")
    op.drop_table("character_afflictions")

    op.drop_table("affliction_types")

    op.drop_column("characters", "death_cause")
    op.drop_column("characters", "last_entertained_tick")
    op.drop_column("characters", "last_drank_tick")
    op.drop_column("characters", "last_ate_tick")
    op.drop_column("characters", "sanity")
    op.drop_column("characters", "thirst")
    op.drop_column("characters", "crime_toggle_changed_at")
    op.drop_column("characters", "crime_enabled")
    op.drop_column("characters", "rp_mode_changed_at")
    op.drop_column("characters", "rp_mode")
