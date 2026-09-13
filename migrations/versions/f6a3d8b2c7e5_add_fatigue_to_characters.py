"""add fatigue to characters

Revision ID: f6a3d8b2c7e5
Revises: e4b7c2a9f1d6
Create Date: 2026-09-13 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f6a3d8b2c7e5"
down_revision: str | None = "e4b7c2a9f1d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "characters",
        sa.Column("fatigue", sa.Float(), nullable=False, server_default="100"),
    )
    op.alter_column("characters", "fatigue", server_default=None)


def downgrade() -> None:
    op.drop_column("characters", "fatigue")
