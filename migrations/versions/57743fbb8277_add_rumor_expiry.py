"""add rumor expiry

Revision ID: 57743fbb8277
Revises: f2be2c214b36
Create Date: 2026-11-16 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "57743fbb8277"
down_revision: str | None = "f2be2c214b36"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 1 simulation month (panem_shared.constants.DAYS_PER_MONTH * TICKS_PER_DAY)
# -- kept as a literal here rather than importing panem_shared, the same
# "migrations don't import application code" posture every other
# migration in this project already holds to.
_ONE_MONTH_TICKS = 30 * 24


def upgrade() -> None:
    op.add_column("rumors", sa.Column("expires_tick", sa.Integer(), nullable=True))
    op.create_index("ix_rumors_expires_tick", "rumors", ["expires_tick"])
    # Backfill every existing rumor with a real expiry (1 sim month past
    # whenever it was spawned) instead of leaving it permanently NULL --
    # an already-ancient rumor with no expiry at all would otherwise sit
    # on the board forever once the sweep that reads this column starts
    # running.
    op.execute(f"UPDATE rumors SET expires_tick = spawned_tick + {_ONE_MONTH_TICKS}")


def downgrade() -> None:
    op.drop_index("ix_rumors_expires_tick", table_name="rumors")
    op.drop_column("rumors", "expires_tick")
