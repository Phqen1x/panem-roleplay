"""add character last_poach_tick

Revision ID: b4d7f1a8c3e9
Revises: a1c8f4d0e6b2
Create Date: 2026-09-23 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4d7f1a8c3e9"
down_revision: str | None = "a1c8f4d0e6b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("characters", sa.Column("last_poach_tick", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("characters", "last_poach_tick")
