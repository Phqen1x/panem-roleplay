"""add property location_id

Revision ID: c1e5a9d3f6b2
Revises: b6d9e2c4a7f1
Create Date: 2026-09-18 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1e5a9d3f6b2"
down_revision: str | None = "b6d9e2c4a7f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("properties", sa.Column("location_id", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("properties", "location_id")
