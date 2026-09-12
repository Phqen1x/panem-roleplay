"""add interaction_count to relationships

Revision ID: a7c2e8f19d3b
Revises: f3a1c9d7b4e2
Create Date: 2026-09-13 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c2e8f19d3b"
down_revision: str | None = "f3a1c9d7b4e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "relationships",
        sa.Column("interaction_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.alter_column("relationships", "interaction_count", server_default=None)


def downgrade() -> None:
    op.drop_column("relationships", "interaction_count")
