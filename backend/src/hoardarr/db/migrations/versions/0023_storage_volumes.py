"""Add canonical provider-backed storage volumes.

Revision ID: 0023_storage_volumes
Revises: 0022_fleet_location_confirmation
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0023_storage_volumes"
down_revision = "0022_fleet_location_confirmation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "storage_volumes",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("stable_identity", sa.String(512), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("resource_type", sa.String(32), nullable=False),
        sa.Column("provider_resource_id", sa.String(512), nullable=False),
        sa.Column("presentation", sa.String(16), nullable=False),
        sa.Column("parent_storage_entity_id", sa.String(36), nullable=True),
        sa.Column("mountpoint", sa.String(4096), nullable=True),
        sa.Column("device_path", sa.String(4096), nullable=True),
        sa.Column("filesystem_type", sa.String(64), nullable=True),
        sa.Column("filesystem_uuid", sa.String(128), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("allocated_bytes", sa.Integer(), nullable=True),
        sa.Column("lifecycle_state", sa.String(32), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("size_bytes IS NULL OR size_bytes >= 0", name="ck_volume_size"),
        sa.CheckConstraint(
            "allocated_bytes IS NULL OR allocated_bytes >= 0", name="ck_volume_allocated"
        ),
        sa.ForeignKeyConstraint(
            ["parent_storage_entity_id"], ["storage_entities.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider", "provider_resource_id", name="uq_storage_volume_provider_resource"
        ),
        sa.UniqueConstraint("stable_identity"),
    )
    op.create_index(
        "ix_storage_volumes_lifecycle_state",
        "storage_volumes",
        ["lifecycle_state"],
    )
    op.create_index("ix_storage_volumes_parent", "storage_volumes", ["parent_storage_entity_id"])


def downgrade() -> None:
    op.drop_index("ix_storage_volumes_parent", table_name="storage_volumes")
    op.drop_index("ix_storage_volumes_lifecycle_state", table_name="storage_volumes")
    op.drop_table("storage_volumes")
