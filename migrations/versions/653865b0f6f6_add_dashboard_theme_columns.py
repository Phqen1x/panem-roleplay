"""add dashboard theme columns to users

Revision ID: 653865b0f6f6
Revises: c7e2a4f9b1d6
Create Date: 2026-09-23 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "653865b0f6f6"
down_revision: str | None = "c7e2a4f9b1d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("dashboard_background_hex", sa.String(length=7), nullable=True)
    )
    op.add_column("users", sa.Column("dashboard_accent_hex", sa.String(length=7), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "dashboard_accent_hex")
    op.drop_column("users", "dashboard_background_hex")
