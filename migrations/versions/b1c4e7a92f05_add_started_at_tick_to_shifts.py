"""add started_at_tick to shifts

Revision ID: b1c4e7a92f05
Revises: d4b1f6a2c9e7
Create Date: 2026-09-13 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1c4e7a92f05"
down_revision: str | None = "d4b1f6a2c9e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("shifts", sa.Column("started_at_tick", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("shifts", "started_at_tick")
