"""add dialogue settings

Revision ID: b4d2e8f1c6a9
Revises: e8c9d0f1a2b3
Create Date: 2026-11-21 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4d2e8f1c6a9"
down_revision: str | None = "e8c9d0f1a2b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dialogue_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
    )
    op.execute("INSERT INTO dialogue_settings (id, enabled) VALUES (1, true)")


def downgrade() -> None:
    op.drop_table("dialogue_settings")
