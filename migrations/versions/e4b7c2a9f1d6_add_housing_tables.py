"""add housing tables

Revision ID: e4b7c2a9f1d6
Revises: d8e2f5a1c9b3
Create Date: 2026-09-13 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e4b7c2a9f1d6"
down_revision: str | None = "d8e2f5a1c9b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "properties",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("district_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("tier", sa.String(length=16), nullable=False),
        sa.Column("complex_id", sa.String(length=64), nullable=True),
        sa.Column("owner_kind", sa.String(length=16), nullable=False, server_default="npc"),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("characters.id"), nullable=True),
        sa.Column("for_sale", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("suggested_price", sa.Float(), nullable=False, server_default="0"),
        sa.Column("asking_price", sa.Float(), nullable=True),
        sa.Column("mortgage_principal", sa.Float(), nullable=False, server_default="0"),
        sa.Column("mortgage_payment", sa.Float(), nullable=False, server_default="0"),
        sa.Column("mortgage_next_due_tick", sa.Integer(), nullable=True),
        sa.Column("mortgage_missed_payments", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at_tick", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_properties_district_id", "properties", ["district_id"])
    op.create_index("ix_properties_complex_id", "properties", ["complex_id"])

    op.create_table(
        "apartment_leases",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "property_id",
            sa.Integer(),
            sa.ForeignKey("properties.id"),
            nullable=False,
        ),
        sa.Column(
            "tenant_character_id",
            sa.Integer(),
            sa.ForeignKey("characters.id"),
            nullable=False,
        ),
        sa.Column("rent_price", sa.Float(), nullable=False),
        sa.Column("started_tick", sa.Integer(), nullable=False),
        sa.Column("next_rent_due_tick", sa.Integer(), nullable=False),
        sa.Column("missed_payments", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("property_id", name="uq_apartment_lease_one_per_unit"),
    )
    op.create_index("ix_apartment_leases_property_id", "apartment_leases", ["property_id"])
    op.create_index(
        "ix_apartment_leases_tenant_character_id",
        "apartment_leases",
        ["tenant_character_id"],
    )

    op.create_table(
        "property_auctions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "property_id",
            sa.Integer(),
            sa.ForeignKey("properties.id"),
            nullable=False,
        ),
        sa.Column("seller_kind", sa.String(length=16), nullable=False),
        sa.Column("seller_id", sa.Integer(), sa.ForeignKey("characters.id"), nullable=True),
        sa.Column("minimum_bid", sa.Float(), nullable=False),
        sa.Column("current_bid", sa.Float(), nullable=True),
        sa.Column(
            "current_bidder_id",
            sa.Integer(),
            sa.ForeignKey("characters.id"),
            nullable=True,
        ),
        sa.Column("ends_at_tick", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
    )
    op.create_index("ix_property_auctions_property_id", "property_auctions", ["property_id"])

    op.add_column("characters", sa.Column("housing_property_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_characters_housing_property_id",
        "characters",
        "properties",
        ["housing_property_id"],
        ["id"],
    )

    op.alter_column("properties", "owner_kind", server_default=None)
    op.alter_column("properties", "for_sale", server_default=None)
    op.alter_column("properties", "suggested_price", server_default=None)
    op.alter_column("properties", "mortgage_principal", server_default=None)
    op.alter_column("properties", "mortgage_payment", server_default=None)
    op.alter_column("properties", "mortgage_missed_payments", server_default=None)
    op.alter_column("properties", "created_at_tick", server_default=None)
    op.alter_column("apartment_leases", "missed_payments", server_default=None)
    op.alter_column("property_auctions", "status", server_default=None)


def downgrade() -> None:
    op.drop_constraint("fk_characters_housing_property_id", "characters", type_="foreignkey")
    op.drop_column("characters", "housing_property_id")
    op.drop_table("property_auctions")
    op.drop_table("apartment_leases")
    op.drop_table("properties")
