"""Add durable logical storage, controller, and path identities.

Revision ID: 0008_storage_redundancy
Revises: 0007_telemetry_rollup_details
"""

import sqlalchemy as sa
from alembic import op

revision = "0008_storage_redundancy"
down_revision = "0007_telemetry_rollup_details"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "storage_entities",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("stable_identity", sa.String(length=512), nullable=False),
        sa.Column("storage_kind", sa.String(length=32), nullable=False),
        sa.Column("filesystem_uuid", sa.String(length=128), nullable=True),
        sa.Column("mountpoint", sa.String(length=4096), nullable=False),
        sa.Column("presentation_device", sa.String(length=4096), nullable=False),
        sa.Column("capacity_bytes", sa.Integer(), nullable=False),
        sa.Column("logical_sector_bytes", sa.Integer(), nullable=False),
        sa.Column("physical_sector_bytes", sa.Integer(), nullable=False),
        sa.Column("topology_state", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stable_identity"),
    )
    op.create_table(
        "storage_controllers",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("stable_identity", sa.String(length=512), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=256), nullable=True),
        sa.Column("state_json", sa.JSON(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stable_identity"),
    )
    op.create_index("ix_storage_controllers_last_seen_at", "storage_controllers", ["last_seen_at"])
    op.create_table(
        "storage_paths",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("storage_entity_id", sa.String(length=36), nullable=False),
        sa.Column("controller_id", sa.String(length=36), nullable=True),
        sa.Column("stable_path_identity", sa.String(length=512), nullable=False),
        sa.Column("kernel_path", sa.String(length=4096), nullable=False),
        sa.Column("logical_storage_identity", sa.String(length=512), nullable=False),
        sa.Column("protocol", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("optimized", sa.Boolean(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["controller_id"], ["storage_controllers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["storage_entity_id"], ["storage_entities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_entity_id", "stable_path_identity", name="uq_storage_path"),
    )
    op.create_index(
        "ix_storage_paths_entity_state", "storage_paths", ["storage_entity_id", "state"]
    )
    op.create_index("ix_storage_paths_last_seen_at", "storage_paths", ["last_seen_at"])
    op.create_index(
        "ix_storage_paths_logical_storage_identity", "storage_paths", ["logical_storage_identity"]
    )


def downgrade() -> None:
    op.drop_table("storage_paths")
    op.drop_table("storage_controllers")
    op.drop_table("storage_entities")
