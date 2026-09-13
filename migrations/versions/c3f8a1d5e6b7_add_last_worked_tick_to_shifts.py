"""add last_worked_tick to shifts

Revision ID: c3f8a1d5e6b7
Revises: e9a2f6c1b8d4
Create Date: 2026-09-13 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3f8a1d5e6b7"
down_revision: str | None = "e9a2f6c1b8d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("shifts", sa.Column("last_worked_tick", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("shifts", "last_worked_tick")
