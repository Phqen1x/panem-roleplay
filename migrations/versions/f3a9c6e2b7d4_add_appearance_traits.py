"""add appearance_traits

Revision ID: f3a9c6e2b7d4
Revises: e7a2f4c9d1b6
Create Date: 2026-09-20 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f3a9c6e2b7d4"
down_revision: str | None = "e7a2f4c9d1b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "characters",
        sa.Column(
            "appearance_traits",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("characters", "appearance_traits")
