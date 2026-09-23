"""add theme_profiles table; replace user scalar theme columns with profile FKs

Revision ID: a2520b13ef46
Revises: 653865b0f6f6
Create Date: 2026-09-23 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a2520b13ef46"
down_revision: str | None = "653865b0f6f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "theme_profiles",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column("background_hex", sa.String(length=7), nullable=False),
        sa.Column("accent_hex", sa.String(length=7), nullable=False),
        sa.Column("panel_hex", sa.String(length=7), nullable=False),
        sa.Column("text_hex", sa.String(length=7), nullable=False),
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
    op.create_index("ix_theme_profiles_user_id", "theme_profiles", ["user_id"])

    # No inline `sa.ForeignKey(...)` here -- `theme_profiles.user_id` points
    # back at `users.id`, so the FK is added via a separate named `ALTER
    # TABLE` below instead, the same `use_alter`-equivalent split
    # `e4b7c2a9f1d6_add_housing_tables.py` uses for `characters.
    # housing_property_id` <-> `properties.owner_id`'s own cycle.
    op.add_column(
        "users", sa.Column("active_theme_profile_id", sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        "fk_users_active_theme_profile_id",
        "users",
        "theme_profiles",
        ["active_theme_profile_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "characters",
        sa.Column(
            "theme_profile_id",
            sa.Integer(),
            sa.ForeignKey("theme_profiles.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # No migration path from the old single-slot columns to a named profile
    # (there's no name to give it) -- a donor who'd already customized just
    # starts over with one save, same "never-customized == default" meaning
    # NULL already carried on those two columns.
    op.drop_column("users", "dashboard_background_hex")
    op.drop_column("users", "dashboard_accent_hex")


def downgrade() -> None:
    op.add_column(
        "users", sa.Column("dashboard_background_hex", sa.String(length=7), nullable=True)
    )
    op.add_column("users", sa.Column("dashboard_accent_hex", sa.String(length=7), nullable=True))
    op.drop_column("characters", "theme_profile_id")
    op.drop_constraint("fk_users_active_theme_profile_id", "users", type_="foreignkey")
    op.drop_column("users", "active_theme_profile_id")
    op.drop_index("ix_theme_profiles_user_id", table_name="theme_profiles")
    op.drop_table("theme_profiles")
