"""use timestamptz for datetime columns missing timezone=True

Revision ID: 0fb3bf76f733
Revises: 7116c3213f6e
Create Date: 2026-09-11 00:23:19.501409

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0fb3bf76f733"
down_revision: str | None = "7116c3213f6e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # These columns were never given `DateTime(timezone=True)`, so Postgres
    # stored them as `TIMESTAMP WITHOUT TIME ZONE` while the app always
    # wrote/read timezone-aware UTC datetimes -- an inconsistency that
    # asyncpg rejects outright on write (can't bind a tz-aware value into a
    # naive column) rather than silently corrupting data, so every existing
    # value here is NULL. `AT TIME ZONE 'UTC'` is still the correct
    # reinterpretation for any that aren't.
    for table, column, nullable in (
        ("scene_messages", "ts", False),
        ("scene_messages", "edited_at", True),
        ("scene_messages", "deleted_at", True),
        ("scenes", "last_message_at", True),
        ("users", "tos_accepted_at", True),
        ("users", "banned_at", True),
    ):
        op.alter_column(
            table,
            column,
            existing_type=postgresql.TIMESTAMP(),
            type_=sa.DateTime(timezone=True),
            existing_nullable=nullable,
            postgresql_using=f"{column} AT TIME ZONE 'UTC'",
        )


def downgrade() -> None:
    for table, column, nullable in (
        ("users", "banned_at", True),
        ("users", "tos_accepted_at", True),
        ("scenes", "last_message_at", True),
        ("scene_messages", "deleted_at", True),
        ("scene_messages", "edited_at", True),
        ("scene_messages", "ts", False),
    ):
        op.alter_column(
            table,
            column,
            existing_type=sa.DateTime(timezone=True),
            type_=postgresql.TIMESTAMP(),
            existing_nullable=nullable,
            postgresql_using=f"{column} AT TIME ZONE 'UTC'",
        )
