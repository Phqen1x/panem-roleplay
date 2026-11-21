"""merge dialogue settings and feature flags branches

Revision ID: a5e7c1f3b9d6
Revises: 8a1f6d2c9b4e, b4d2e8f1c6a9
Create Date: 2026-11-21 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "a5e7c1f3b9d6"
down_revision: str | Sequence[str] | None = ("8a1f6d2c9b4e", "b4d2e8f1c6a9")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
