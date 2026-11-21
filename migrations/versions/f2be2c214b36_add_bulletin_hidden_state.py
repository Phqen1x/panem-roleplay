"""add bulletin hidden state

Revision ID: f2be2c214b36
Revises: a7e3c9f2b5d1
Create Date: 2026-11-16 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f2be2c214b36"
down_revision: str | None = "a7e3c9f2b5d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "bulletin_user_state",
        sa.Column("hidden", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("bulletin_user_state", "hidden", server_default=None)


def downgrade() -> None:
    op.drop_column("bulletin_user_state", "hidden")
