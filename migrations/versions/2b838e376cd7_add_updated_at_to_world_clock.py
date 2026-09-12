"""add updated_at to world_clock

Revision ID: 2b838e376cd7
Revises: 6fc777554ae5
Create Date: 2026-09-12 17:04:05.517615

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2b838e376cd7"
down_revision: str | None = "6fc777554ae5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # server_default backfills the single existing world_clock row (if any);
    # dropped right after so the column stays Python-side-only like every
    # other timestamp column in this schema.
    op.add_column(
        "world_clock",
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.alter_column("world_clock", "updated_at", server_default=None)


def downgrade() -> None:
    op.drop_column("world_clock", "updated_at")
