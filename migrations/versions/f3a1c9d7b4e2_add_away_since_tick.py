"""add away_since_tick to characters

Revision ID: f3a1c9d7b4e2
Revises: 8edf10234e89
Create Date: 2026-09-13 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3a1c9d7b4e2"
down_revision: str | None = "8edf10234e89"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("characters", sa.Column("away_since_tick", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("characters", "away_since_tick")
