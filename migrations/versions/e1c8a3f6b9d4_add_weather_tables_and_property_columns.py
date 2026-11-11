"""add weather tables and property weather columns

Revision ID: e1c8a3f6b9d4
Revises: d6b2f9c4a1e7
Create Date: 2026-11-11 00:00:02.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e1c8a3f6b9d4"
down_revision: str | None = "d6b2f9c4a1e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "weather_forecasts",
        sa.Column("district_id", sa.Integer(), primary_key=True),
        sa.Column("day_index", sa.Integer(), primary_key=True),
        sa.Column("season", sa.String(length=16), nullable=False),
        sa.Column("temp_high", sa.Integer(), nullable=False),
        sa.Column("temp_low", sa.Integer(), nullable=False),
        sa.Column("condition", sa.String(length=16), nullable=False, server_default="clear"),
        sa.Column("announced_tick", sa.Integer(), nullable=False),
    )
    op.alter_column("weather_forecasts", "condition", server_default=None)

    op.create_table(
        "weather_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("district_id", sa.Integer(), nullable=False),
        sa.Column("day_index", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.Integer(), nullable=False),
        sa.Column("predicted_start_tick", sa.Integer(), nullable=False),
        sa.Column("predicted_duration_ticks", sa.Integer(), nullable=False),
        sa.Column("actual_start_tick", sa.Integer(), nullable=True),
        sa.Column("actual_end_tick", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="forecast"),
        sa.Column("announced_tick", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_weather_events_district_id", "weather_events", ["district_id"])
    op.create_index("ix_weather_events_day_index", "weather_events", ["day_index"])
    op.create_index("ix_weather_events_status", "weather_events", ["status"])
    op.alter_column("weather_events", "status", server_default=None)

    op.add_column(
        "properties", sa.Column("damage_level", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "properties",
        sa.Column(
            "weather_prep",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
    )
    op.alter_column("properties", "damage_level", server_default=None)
    op.alter_column("properties", "weather_prep", server_default=None)


def downgrade() -> None:
    op.drop_column("properties", "weather_prep")
    op.drop_column("properties", "damage_level")
    op.drop_table("weather_events")
    op.drop_table("weather_forecasts")
