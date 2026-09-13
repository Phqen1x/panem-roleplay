"""add npc overrides

Revision ID: b2d4f7a9c1e6
Revises: a1c9e4f2b8d3
Create Date: 2026-09-13 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2d4f7a9c1e6"
down_revision: str | None = "a1c9e4f2b8d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("npcs", sa.Column("backstory_override", sa.String(length=1500), nullable=True))
    op.add_column("npcs", sa.Column("appearance_override", sa.String(length=400), nullable=True))


def downgrade() -> None:
    op.drop_column("npcs", "appearance_override")
    op.drop_column("npcs", "backstory_override")
