"""add contraband columns

Revision ID: a3f7c1e8b2d9
Revises: d4e8f1a6c3b9
Create Date: 2026-09-17 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3f7c1e8b2d9"
down_revision: str | None = "d4e8f1a6c3b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "characters",
        sa.Column("job_is_illicit", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "characters",
        sa.Column("illicit_heat", sa.Float(), nullable=False, server_default="0.0"),
    )
    op.add_column(
        "characters",
        sa.Column("jail_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("characters", sa.Column("jail_sentence_ticks", sa.Integer(), nullable=True))
    op.add_column("characters", sa.Column("last_steal_tick", sa.Integer(), nullable=True))
    op.add_column("district_state", sa.Column("crackdown_until_tick", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("district_state", "crackdown_until_tick")
    op.drop_column("characters", "last_steal_tick")
    op.drop_column("characters", "jail_sentence_ticks")
    op.drop_column("characters", "jail_count")
    op.drop_column("characters", "illicit_heat")
    op.drop_column("characters", "job_is_illicit")
