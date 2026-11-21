"""replace block_outfit/block_palette with block_parts

Revision ID: a4c8e1f6b2d9
Revises: f7b2e4a1c8d3
Create Date: 2026-11-05 12:00:00.000000

The single-outfit/single-palette 3D map avatar (`f7b2e4a1c8d3`) is
replaced by four independently-chosen body parts (head/torso/arms/legs),
each with its own shape and color -- see `panem_shared.block_models`'s
module docstring. A follow-up migration rather than editing
`f7b2e4a1c8d3` in place: that revision may already be applied in a real
deployment, and Alembic has no way to "re-run" an already-applied
revision whose body changed out from under it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a4c8e1f6b2d9"
down_revision: str | None = "f7b2e4a1c8d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFAULT_PARTS_JSON = (
    '{"head": "a", "head_color": "a", "torso": "a", "torso_color": "a", '
    '"arms": "a", "arms_color": "a", "legs": "a", "legs_color": "a"}'
)


def upgrade() -> None:
    op.add_column(
        "characters",
        sa.Column(
            "block_parts",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text(f"'{_DEFAULT_PARTS_JSON}'::jsonb"),
        ),
    )
    op.alter_column("characters", "block_parts", server_default=None)
    op.drop_column("characters", "block_palette")
    op.drop_column("characters", "block_outfit")


def downgrade() -> None:
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
    op.drop_column("characters", "block_parts")
