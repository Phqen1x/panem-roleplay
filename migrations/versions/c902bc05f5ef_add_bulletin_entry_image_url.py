"""add bulletin entry image_url

Revision ID: c902bc05f5ef
Revises: 57743fbb8277
Create Date: 2026-11-16 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c902bc05f5ef"
down_revision: str | None = "57743fbb8277"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("bulletin_entries", sa.Column("image_url", sa.String(length=512), nullable=True))


def downgrade() -> None:
    op.drop_column("bulletin_entries", "image_url")
