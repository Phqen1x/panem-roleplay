"""add character approval_notified_at

Revision ID: e7a2f4c9d1b6
Revises: c1e5a9d3f6b2
Create Date: 2026-09-18 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7a2f4c9d1b6"
down_revision: str | None = "c1e5a9d3f6b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "characters", sa.Column("approval_notified_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("characters", "approval_notified_at")
