"""Add storage groups, durable disk registry, and lifecycle events.

Revision ID: 0010_storage_groups
Revises: 0009_redundancy_events
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_storage_groups"
down_revision = "0009_redundancy_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "storage_groups",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("namespace_path", sa.String(4096), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("policy_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("namespace_path"),
    )
    op.create_index("ix_storage_groups_state", "storage_groups", ["state"])
    op.create_table(
        "physical_disks",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("stable_identity", sa.String(512), nullable=False),
        sa.Column("kernel_path", sa.String(4096), nullable=True),
        sa.Column("serial", sa.String(256), nullable=True),
        sa.Column("wwn", sa.String(256), nullable=True),
        sa.Column("vendor", sa.String(128), nullable=True),
        sa.Column("model", sa.String(256), nullable=True),
        sa.Column("capacity_bytes", sa.Integer(), nullable=True),
        sa.Column("logical_sector_bytes", sa.Integer(), nullable=True),
        sa.Column("physical_sector_bytes", sa.Integer(), nullable=True),
        sa.Column("media_type", sa.String(32), nullable=True),
        sa.Column("health_state", sa.String(32), nullable=False),
        sa.Column("lifecycle_state", sa.String(32), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stable_identity"),
    )
    op.create_index("ix_physical_disks_lifecycle_state", "physical_disks", ["lifecycle_state"])
    op.create_index("ix_physical_disks_last_seen_at", "physical_disks", ["last_seen_at"])
    op.create_table(
        "storage_backends",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("storage_group_id", sa.String(36), nullable=False),
        sa.Column("storage_entity_id", sa.String(36), nullable=True),
        sa.Column("physical_disk_id", sa.String(36), nullable=True),
        sa.Column("stable_identity", sa.String(512), nullable=False),
        sa.Column("namespace_path", sa.String(4096), nullable=True),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("lifecycle_state", sa.String(32), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["physical_disk_id"], ["physical_disks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["storage_entity_id"], ["storage_entities.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["storage_group_id"], ["storage_groups.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "storage_group_id", "stable_identity", name="uq_group_backend_identity"
        ),
        sa.UniqueConstraint("physical_disk_id", name="uq_storage_backend_physical_disk"),
        sa.UniqueConstraint("storage_entity_id", name="uq_storage_backend_storage_entity"),
    )
    op.create_index("ix_storage_backends_lifecycle_state", "storage_backends", ["lifecycle_state"])
    op.create_index(
        "ix_storage_backends_group_state",
        "storage_backends",
        ["storage_group_id", "lifecycle_state"],
    )
    op.create_index(
        "uq_storage_backends_preferred_write",
        "storage_backends",
        ["storage_group_id"],
        unique=True,
        sqlite_where=sa.text("lifecycle_state = 'preferred_write'"),
    )
    op.create_table(
        "storage_lifecycle_events",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("storage_group_id", sa.String(36), nullable=False),
        sa.Column("storage_backend_id", sa.String(36), nullable=True),
        sa.Column("physical_disk_id", sa.String(36), nullable=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("previous_state", sa.String(32), nullable=True),
        sa.Column("resulting_state", sa.String(32), nullable=False),
        sa.Column("actor_type", sa.String(32), nullable=False),
        sa.Column("actor_id", sa.String(36), nullable=False),
        sa.Column("reason", sa.String(512), nullable=True),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["physical_disk_id"], ["physical_disks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["storage_backend_id"], ["storage_backends.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["storage_group_id"], ["storage_groups.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_storage_lifecycle_events_group_time",
        "storage_lifecycle_events",
        ["storage_group_id", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_storage_lifecycle_events_group_time", table_name="storage_lifecycle_events")
    op.drop_table("storage_lifecycle_events")
    op.drop_index("uq_storage_backends_preferred_write", table_name="storage_backends")
    op.drop_index("ix_storage_backends_group_state", table_name="storage_backends")
    op.drop_index("ix_storage_backends_lifecycle_state", table_name="storage_backends")
    op.drop_table("storage_backends")
    op.drop_index("ix_physical_disks_last_seen_at", table_name="physical_disks")
    op.drop_index("ix_physical_disks_lifecycle_state", table_name="physical_disks")
    op.drop_table("physical_disks")
    op.drop_index("ix_storage_groups_state", table_name="storage_groups")
    op.drop_table("storage_groups")
