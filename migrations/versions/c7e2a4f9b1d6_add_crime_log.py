"""add crime_log table

Revision ID: c7e2a4f9b1d6
Revises: b4d7f1a8c3e9
Create Date: 2026-09-23 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7e2a4f9b1d6"
down_revision: str | None = "b4d7f1a8c3e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "crime_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("character_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("tick", sa.Integer(), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("caught", sa.Boolean(), nullable=False),
        sa.Column("target_name", sa.String(length=64), nullable=True),
        sa.Column("good_name", sa.String(length=64), nullable=True),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_crime_log_character_id"), "crime_log", ["character_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_crime_log_character_id"), table_name="crime_log")
    op.drop_table("crime_log")
