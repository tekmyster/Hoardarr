"""Add persistent two-node HA peer awareness.

Revision ID: 0025_ha_peer_awareness
Revises: 0024_storage_volume_capabilities
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0025_ha_peer_awareness"
down_revision = "0024_storage_volume_capabilities"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ha_configurations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("mode", sa.String(64), nullable=False),
        sa.Column("local_node_id", sa.String(128), nullable=False, unique=True),
        sa.Column("local_name", sa.String(128), nullable=False),
        sa.Column("local_fqdn", sa.String(253), nullable=False),
        sa.Column("local_ip", sa.String(64), nullable=False),
        sa.Column("local_role", sa.String(32), nullable=False),
        sa.Column("peer_node_id", sa.String(128), nullable=False, unique=True),
        sa.Column("peer_name", sa.String(128), nullable=False),
        sa.Column("peer_fqdn", sa.String(253), nullable=False),
        sa.Column("peer_ip", sa.String(64), nullable=False),
        sa.Column("peer_role", sa.String(32), nullable=False),
        sa.Column("service_ip", sa.String(64)),
        sa.Column("current_owner_node_id", sa.String(128)),
        sa.Column("peer_reachable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("peer_last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("peer_report_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "ha_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "configuration_id",
            sa.String(36),
            sa.ForeignKey("ha_configurations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("cause", sa.String(512)),
        sa.Column("previous_owner_node_id", sa.String(128)),
        sa.Column("resulting_owner_node_id", sa.String(128)),
        sa.Column("detail_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ha_events_time", "ha_events", ["occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_ha_events_time", table_name="ha_events")
    op.drop_table("ha_events")
    op.drop_table("ha_configurations")
