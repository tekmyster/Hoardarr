"""Add provider capability observations to storage volumes.

Revision ID: 0024_storage_volume_capabilities
Revises: 0023_storage_volumes
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0024_storage_volume_capabilities"
down_revision = "0023_storage_volumes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("storage_volumes") as batch:
        batch.add_column(
            sa.Column("capabilities_json", sa.JSON(), nullable=False, server_default="{}")
        )
        batch.add_column(sa.Column("capabilities_detected_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    with op.batch_alter_table("storage_volumes") as batch:
        batch.drop_column("capabilities_detected_at")
        batch.drop_column("capabilities_json")
