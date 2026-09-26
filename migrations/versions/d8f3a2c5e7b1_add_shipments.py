"""add shipments table

Revision ID: d8f3a2c5e7b1
Revises: b4d7e1f9a3c6
Create Date: 2026-09-25 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d8f3a2c5e7b1"
down_revision: str | None = "b4d7e1f9a3c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shipments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("district_id", sa.Integer(), nullable=False),
        sa.Column("location_id", sa.String(length=64), nullable=False),
        sa.Column("good_id", sa.String(length=64), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("spawned_tick", sa.Integer(), nullable=False),
        sa.Column("expires_tick", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_shipments_district_id"), "shipments", ["district_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_shipments_district_id"), table_name="shipments")
    op.drop_table("shipments")
