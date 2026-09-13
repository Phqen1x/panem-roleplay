"""replace is_victor with positions

Revision ID: d4b1f6a2c9e7
Revises: a7c2e8f19d3b
Create Date: 2026-09-13 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d4b1f6a2c9e7"
down_revision: str | None = "a7c2e8f19d3b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "characters",
        sa.Column(
            "positions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.execute("UPDATE characters SET positions = '[\"victor\"]'::jsonb WHERE is_victor = true")
    op.drop_column("characters", "is_victor")


def downgrade() -> None:
    op.add_column(
        "characters",
        sa.Column("is_victor", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute("UPDATE characters SET is_victor = true WHERE positions @> '[\"victor\"]'::jsonb")
    op.drop_column("characters", "positions")
