"""Allow file-level logical storage to report sector geometry honestly.

Revision ID: 0027_logical_storage_pool_geometry
Revises: 0026_volume_snapshots
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0027_logical_storage_pool_geometry"
down_revision = "0026_volume_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("storage_entities") as batch:
        batch.alter_column(
            "logical_sector_bytes",
            existing_type=sa.Integer(),
            nullable=True,
        )
        batch.alter_column(
            "physical_sector_bytes",
            existing_type=sa.Integer(),
            nullable=True,
        )


def downgrade() -> None:
    # A downgrade cannot truthfully manufacture geometry for file-level pools.
    # Remove those pool registry rows before restoring the old non-null schema.
    op.execute("DELETE FROM storage_entities WHERE provider = 'mergerfs'")
    with op.batch_alter_table("storage_entities") as batch:
        batch.alter_column(
            "logical_sector_bytes",
            existing_type=sa.Integer(),
            nullable=False,
        )
        batch.alter_column(
            "physical_sector_bytes",
            existing_type=sa.Integer(),
            nullable=False,
        )
