"""add relationship summary

Revision ID: d4e8f1a6c3b9
Revises: b2d4f7a9c1e6
Create Date: 2026-09-15 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e8f1a6c3b9"
down_revision: str | None = "b2d4f7a9c1e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("relationships", sa.Column("summary", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("relationships", "summary")
