"""add lockpick tries

Revision ID: b6d9e2c4a7f1
Revises: a3f7c1e8b2d9
Create Date: 2026-09-17 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b6d9e2c4a7f1"
down_revision: str | None = "a3f7c1e8b2d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "characters",
        sa.Column("jail_lockpick_tries_used", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("characters", "jail_lockpick_tries_used")
