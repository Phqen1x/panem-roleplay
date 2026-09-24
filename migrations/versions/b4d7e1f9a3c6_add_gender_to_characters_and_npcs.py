"""add gender to characters and npcs

Revision ID: b4d7e1f9a3c6
Revises: a1c9e4f7b2d8
Create Date: 2026-09-24 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4d7e1f9a3c6"
down_revision: str | None = "a1c9e4f7b2d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("characters", sa.Column("gender", sa.String(length=16), nullable=True))
    op.add_column("npcs", sa.Column("gender", sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column("npcs", "gender")
    op.drop_column("characters", "gender")
