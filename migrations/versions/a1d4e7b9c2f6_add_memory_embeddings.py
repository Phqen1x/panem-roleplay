"""add memories.embedding / embedding_model (semantic NPC memory recall)

Revision ID: a1d4e7b9c2f6
Revises: e8c9d0f1a2b3
Create Date: 2026-10-06 12:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a1d4e7b9c2f6"
down_revision: str | None = "e8c9d0f1a2b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("memories", sa.Column("embedding", postgresql.ARRAY(sa.Float()), nullable=True))
    op.add_column("memories", sa.Column("embedding_model", sa.String(length=96), nullable=True))


def downgrade() -> None:
    op.drop_column("memories", "embedding_model")
    op.drop_column("memories", "embedding")
