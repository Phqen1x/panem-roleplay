"""drop tesserae_count

Revision ID: 8edf10234e89
Revises: 2b838e376cd7
Create Date: 2026-09-12 17:49:43.659919

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8edf10234e89"
down_revision: str | None = "2b838e376cd7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("characters", "tesserae_count")


def downgrade() -> None:
    op.add_column(
        "characters",
        sa.Column("tesserae_count", sa.Integer(), nullable=False, server_default="0"),
    )
