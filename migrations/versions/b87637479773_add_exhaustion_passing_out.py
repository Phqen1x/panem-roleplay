"""add exhaustion passed-out status

Revision ID: b87637479773
Revises: 0be5d42ad310
Create Date: 2026-11-23 23:32:53.847866

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b87637479773"
down_revision: str | None = "0be5d42ad310"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("characters", sa.Column("passed_out_until_tick", sa.Integer(), nullable=True))

    op.create_table(
        "exhaustion_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("passed_out_ticks", sa.Integer(), nullable=False),
    )
    op.execute("INSERT INTO exhaustion_settings (id, passed_out_ticks) VALUES (1, 12)")


def downgrade() -> None:
    op.drop_table("exhaustion_settings")
    op.drop_column("characters", "passed_out_until_tick")
