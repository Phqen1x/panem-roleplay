"""add character last_inn_stay_tick

Revision ID: 0be5d42ad310
Revises: a5e7c1f3b9d6
Create Date: 2026-11-23 17:36:49.319347

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0be5d42ad310"
down_revision: str | None = "a5e7c1f3b9d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("characters", sa.Column("last_inn_stay_tick", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("characters", "last_inn_stay_tick")
