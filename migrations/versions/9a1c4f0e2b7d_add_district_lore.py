"""add district_lore and district_lore_people tables

Revision ID: 9a1c4f0e2b7d
Revises: 4826400ed79f
Create Date: 2026-09-24 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "9a1c4f0e2b7d"
down_revision: str | None = "4826400ed79f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "district_lore",
        sa.Column("district_id", sa.Integer(), nullable=False),
        sa.Column("classification", sa.String(length=16), nullable=True),
        sa.Column(
            "adjectives",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("accent_notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("urban_rural_notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("academy_name", sa.String(length=120), nullable=True),
        sa.Column("academy_notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("games_history", sa.Text(), nullable=False, server_default=""),
        sa.Column("regime_notes", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "opinions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("misc_notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("district_id"),
    )

    op.create_table(
        "district_lore_people",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("district_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("character_id", sa.Integer(), sa.ForeignKey("characters.id"), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_district_lore_people_district_id", "district_lore_people", ["district_id"])


def downgrade() -> None:
    op.drop_index("ix_district_lore_people_district_id", table_name="district_lore_people")
    op.drop_table("district_lore_people")
    op.drop_table("district_lore")
