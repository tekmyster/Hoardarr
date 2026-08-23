"""Add expected-topology baselines and durable drift episodes.

Revision ID: 0013_topology_expectations
Revises: 0012_operation_scheduling
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_topology_expectations"
down_revision = "0012_operation_scheduling"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "topology_expectations",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("source_snapshot_id", sa.String(36), nullable=False),
        sa.Column("expected_json", sa.JSON(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_snapshot_id"], ["hardware_snapshots.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_topology_expectations_active_updated",
        "topology_expectations",
        ["active", "updated_at"],
    )
    op.create_table(
        "topology_drift_events",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("expectation_id", sa.String(36), nullable=False),
        sa.Column("snapshot_id", sa.String(36), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("entity_id", sa.String(512), nullable=False),
        sa.Column("message", sa.String(512), nullable=False),
        sa.Column("expected_json", sa.JSON(), nullable=False),
        sa.Column("observed_json", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["expectation_id"], ["topology_expectations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["hardware_snapshots.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_topology_drift_expectation_state",
        "topology_drift_events",
        ["expectation_id", "state"],
    )
    op.create_index(
        "ix_topology_drift_fingerprint_state",
        "topology_drift_events",
        ["fingerprint", "state"],
    )
    op.create_index(
        "ix_topology_drift_last_seen", "topology_drift_events", ["last_seen_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_topology_drift_last_seen", table_name="topology_drift_events")
    op.drop_index(
        "ix_topology_drift_fingerprint_state", table_name="topology_drift_events"
    )
    op.drop_index(
        "ix_topology_drift_expectation_state", table_name="topology_drift_events"
    )
    op.drop_table("topology_drift_events")
    op.drop_index(
        "ix_topology_expectations_active_updated", table_name="topology_expectations"
    )
    op.drop_table("topology_expectations")
