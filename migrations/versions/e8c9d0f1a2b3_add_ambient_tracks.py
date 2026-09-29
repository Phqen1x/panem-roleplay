"""add ambient_tracks table

Revision ID: e8c9d0f1a2b3
Revises: c6b8f2a4d1e9
Create Date: 2026-09-28 22:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e8c9d0f1a2b3"
down_revision: str | None = "c6b8f2a4d1e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ambient_tracks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("file_path", sa.String(length=512), nullable=False),
        sa.Column("scope", sa.String(length=32), nullable=False, server_default="global"),
        sa.Column("district_id", sa.Integer(), nullable=True),
        sa.Column("location_id", sa.String(length=64), nullable=True),
        sa.Column("channel_id", sa.String(length=64), nullable=True),
        sa.Column("created_by_staff_discord_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ambient_tracks_scope"), "ambient_tracks", ["scope"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_ambient_tracks_scope"), table_name="ambient_tracks")
    op.drop_table("ambient_tracks")
