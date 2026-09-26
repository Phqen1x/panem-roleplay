"""add pending mode-switch columns to characters

Revision ID: c6b8f2a4d1e9
Revises: d8f3a2c5e7b1
Create Date: 2026-09-26 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c6b8f2a4d1e9"
down_revision: str | None = "d8f3a2c5e7b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("characters", sa.Column("pending_rp_mode", sa.String(length=16), nullable=True))
    op.add_column("characters", sa.Column("pending_job_title", sa.String(length=80), nullable=True))
    op.add_column(
        "characters", sa.Column("pending_shift_phase", sa.String(length=16), nullable=True)
    )
    op.add_column(
        "characters",
        sa.Column(
            "pending_job_is_illicit", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column(
        "characters",
        sa.Column("pending_mode_switch_notified_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("characters", "pending_mode_switch_notified_at")
    op.drop_column("characters", "pending_job_is_illicit")
    op.drop_column("characters", "pending_shift_phase")
    op.drop_column("characters", "pending_job_title")
    op.drop_column("characters", "pending_rp_mode")
