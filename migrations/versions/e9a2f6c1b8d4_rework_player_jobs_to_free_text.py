"""rework player jobs to free text + shift choice + leveling

Revision ID: e9a2f6c1b8d4
Revises: b1c4e7a92f05
Create Date: 2026-09-13 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e9a2f6c1b8d4"
down_revision: str | None = "b1c4e7a92f05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "characters",
        "job_id",
        new_column_name="job_title",
        existing_type=sa.String(length=64),
        type_=sa.String(length=80),
    )
    op.add_column("characters", sa.Column("shift_phase", sa.String(length=16), nullable=True))
    op.add_column(
        "characters",
        sa.Column("shifts_completed", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("characters", sa.Column("last_active_tick", sa.Integer(), nullable=True))
    op.drop_table("job_overrides")


def downgrade() -> None:
    op.create_table(
        "job_overrides",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("district_id", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("disabled", sa.Boolean(), nullable=False),
        sa.Column("updated_by_discord_id", sa.BigInteger(), nullable=False),
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
    op.create_index(
        op.f("ix_job_overrides_district_id"), "job_overrides", ["district_id"], unique=False
    )
    op.drop_column("characters", "last_active_tick")
    op.drop_column("characters", "shifts_completed")
    op.drop_column("characters", "shift_phase")
    op.alter_column(
        "characters",
        "job_title",
        new_column_name="job_id",
        existing_type=sa.String(length=80),
        type_=sa.String(length=64),
    )
