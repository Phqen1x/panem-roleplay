"""add layer tables, repurpose appearance_traits as appearance_layers

Revision ID: a1c8f4d0e6b2
Revises: f3a9c6e2b7d4
Create Date: 2026-09-21 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1c8f4d0e6b2"
down_revision: str | None = "f3a9c6e2b7d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "layer_categories",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("z_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "layer_options",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "category_id", sa.Integer(), sa.ForeignKey("layer_categories.id"), nullable=False
        ),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("image_path", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_layer_options_category_id", "layer_options", ["category_id"])

    # Old fixed-palette trait data (hair_style: "mohawk", etc.) is not a
    # valid appearance_layers value ({category_id: option_id}) -- both the
    # categories and every option they'd reference were just created above,
    # so there is nothing for old rows' data to map onto. Every character
    # just starts uncustomized again under the new system, same as the
    # column's existing "never customized" null convention.
    op.alter_column(
        "characters", "appearance_traits", new_column_name="appearance_layers"
    )


def downgrade() -> None:
    op.alter_column(
        "characters", "appearance_layers", new_column_name="appearance_traits"
    )
    op.drop_index("ix_layer_options_category_id", table_name="layer_options")
    op.drop_table("layer_options")
    op.drop_table("layer_categories")
