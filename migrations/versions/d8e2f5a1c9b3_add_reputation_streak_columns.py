"""add reputation streak columns to characters

Revision ID: d8e2f5a1c9b3
Revises: c3f8a1d5e6b7
Create Date: 2026-09-13 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d8e2f5a1c9b3"
down_revision: str | None = "c3f8a1d5e6b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "characters",
        sa.Column("consecutive_wins", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "characters",
        sa.Column("consecutive_losses", sa.Integer(), nullable=False, server_default="0"),
    )
    op.alter_column("characters", "consecutive_wins", server_default=None)
    op.alter_column("characters", "consecutive_losses", server_default=None)


def downgrade() -> None:
    op.drop_column("characters", "consecutive_losses")
    op.drop_column("characters", "consecutive_wins")
