"""add block appearance, pets, property interior items

Revision ID: f7b2e4a1c8d3
Revises: e8c9d0f1a2b3
Create Date: 2026-11-05 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f7b2e4a1c8d3"
down_revision: str | None = "e8c9d0f1a2b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "characters",
        sa.Column("block_outfit", sa.String(length=1), nullable=False, server_default="a"),
    )
    op.add_column(
        "characters",
        sa.Column("block_palette", sa.String(length=1), nullable=False, server_default="a"),
    )
    op.alter_column("characters", "block_outfit", server_default=None)
    op.alter_column("characters", "block_palette", server_default=None)

    op.create_table(
        "pets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("owner_character_id", sa.Integer(), nullable=False),
        sa.Column("species_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("acquired_tick", sa.Integer(), nullable=False, server_default="0"),
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
        sa.ForeignKeyConstraint(["owner_character_id"], ["characters.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_pets_owner_character_id"), "pets", ["owner_character_id"], unique=False
    )
    op.alter_column("pets", "acquired_tick", server_default=None)

    op.create_table(
        "property_interior_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("property_id", sa.Integer(), nullable=False),
        sa.Column("item_key", sa.String(length=64), nullable=False),
        sa.Column("pos_x", sa.Float(), nullable=False, server_default="0"),
        sa.Column("pos_z", sa.Float(), nullable=False, server_default="0"),
        sa.Column("rotation_y", sa.Float(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["property_id"], ["properties.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_property_interior_items_property_id"),
        "property_interior_items",
        ["property_id"],
        unique=False,
    )
    op.alter_column("property_interior_items", "pos_x", server_default=None)
    op.alter_column("property_interior_items", "pos_z", server_default=None)
    op.alter_column("property_interior_items", "rotation_y", server_default=None)


def downgrade() -> None:
    op.drop_index(
        op.f("ix_property_interior_items_property_id"), table_name="property_interior_items"
    )
    op.drop_table("property_interior_items")
    op.drop_index(op.f("ix_pets_owner_character_id"), table_name="pets")
    op.drop_table("pets")
    op.drop_column("characters", "block_palette")
    op.drop_column("characters", "block_outfit")
