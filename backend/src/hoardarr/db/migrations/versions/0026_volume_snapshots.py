"""Add provider-native volume snapshots and bounded schedules.

Revision ID: 0026_volume_snapshots
Revises: 0025_ha_peer_awareness
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0026_volume_snapshots"
down_revision = "0025_ha_peer_awareness"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "storage_volume_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "volume_id",
            sa.String(36),
            sa.ForeignKey("storage_volumes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider_snapshot_id", sa.String(640), nullable=False, unique=True),
        sa.Column("snapshot_name", sa.String(96), nullable=False),
        sa.Column("provider_guid", sa.String(128)),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column(
            "created_by_operation_id",
            sa.String(36),
            sa.ForeignKey("operations.id", ondelete="SET NULL"),
        ),
        sa.Column("detail_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("restored_at", sa.DateTime(timezone=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_storage_volume_snapshots_volume_state",
        "storage_volume_snapshots",
        ["volume_id", "state", "created_at"],
    )
    op.create_table(
        "storage_volume_snapshot_schedules",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "volume_id",
            sa.String(36),
            sa.ForeignKey("storage_volumes.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("interval_hours", sa.Integer(), nullable=False),
        sa.Column("retention_count", sa.Integer(), nullable=False),
        sa.Column("prefix", sa.String(32), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True)),
        sa.Column("last_run_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "interval_hours >= 1 AND interval_hours <= 8760",
            name="ck_volume_snapshot_schedule_interval",
        ),
        sa.CheckConstraint(
            "retention_count >= 1 AND retention_count <= 1024",
            name="ck_volume_snapshot_schedule_retention",
        ),
    )
    op.create_index(
        "ix_volume_snapshot_schedules_due",
        "storage_volume_snapshot_schedules",
        ["enabled", "next_run_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_volume_snapshot_schedules_due",
        table_name="storage_volume_snapshot_schedules",
    )
    op.drop_table("storage_volume_snapshot_schedules")
    op.drop_index(
        "ix_storage_volume_snapshots_volume_state",
        table_name="storage_volume_snapshots",
    )
    op.drop_table("storage_volume_snapshots")
