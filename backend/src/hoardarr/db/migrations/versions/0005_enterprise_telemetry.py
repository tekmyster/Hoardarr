"""Add normalized telemetry, rollups, alerts, and collector state.

Revision ID: 0005_enterprise_telemetry
Revises: 0004_runtime_features
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_enterprise_telemetry"
down_revision = "0004_runtime_features"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "metric_entities",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("stable_id", sa.String(length=512), nullable=False),
        sa.Column("display_name", sa.String(length=256), nullable=False),
        sa.Column("labels_json", sa.JSON(), nullable=False),
        sa.Column("topology_json", sa.JSON(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("entity_type", "stable_id", name="uq_metric_entity_stable_id"),
    )
    op.create_index("ix_metric_entities_last_seen_at", "metric_entities", ["last_seen_at"])
    op.create_index(
        "ix_metric_entities_type_seen", "metric_entities", ["entity_type", "last_seen_at"]
    )
    op.create_table(
        "metric_samples",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("entity_id", sa.String(length=36), nullable=False),
        sa.Column("metric_id", sa.String(length=128), nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("value_text", sa.String(length=128), nullable=True),
        sa.Column("quality", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("collection_interval_seconds", sa.Integer(), nullable=False),
        sa.Column("raw", sa.Boolean(), nullable=False),
        sa.Column("labels_json", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=96), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["entity_id"], ["metric_entities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "entity_id", "metric_id", "observed_at", name="uq_metric_sample_observation"
        ),
    )
    op.create_index("ix_metric_samples_metric_time", "metric_samples", ["metric_id", "observed_at"])
    op.create_index(
        "ix_metric_samples_entity_metric_time",
        "metric_samples",
        ["entity_id", "metric_id", "observed_at"],
    )
    op.create_table(
        "metric_rollups",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("entity_id", sa.String(length=36), nullable=False),
        sa.Column("metric_id", sa.String(length=128), nullable=False),
        sa.Column("resolution", sa.String(length=16), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("minimum", sa.Float(), nullable=True),
        sa.Column("maximum", sa.Float(), nullable=True),
        sa.Column("mean", sa.Float(), nullable=True),
        sa.Column("last", sa.Float(), nullable=True),
        sa.Column("p50", sa.Float(), nullable=True),
        sa.Column("p95", sa.Float(), nullable=True),
        sa.Column("p99", sa.Float(), nullable=True),
        sa.Column("quality", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["entity_id"], ["metric_entities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "entity_id", "metric_id", "resolution", "period_start", name="uq_metric_rollup_period"
        ),
    )
    op.create_index(
        "ix_metric_rollups_query",
        "metric_rollups",
        ["entity_id", "metric_id", "resolution", "period_start"],
    )
    op.create_table(
        "metric_alerts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("rule_id", sa.String(length=128), nullable=False),
        sa.Column("entity_id", sa.String(length=36), nullable=False),
        sa.Column("metric_id", sa.String(length=128), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("trigger_value", sa.Float(), nullable=True),
        sa.Column("threshold_json", sa.JSON(), nullable=False),
        sa.Column("topology_json", sa.JSON(), nullable=False),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by", sa.String(length=36), nullable=True),
        sa.ForeignKeyConstraint(["entity_id"], ["metric_entities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_metric_alerts_state_started", "metric_alerts", ["state", "started_at"])
    op.create_index("ix_metric_alerts_entity_metric", "metric_alerts", ["entity_id", "metric_id"])
    op.create_table(
        "telemetry_state",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("state_json", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("telemetry_state")
    op.drop_index("ix_metric_alerts_entity_metric", table_name="metric_alerts")
    op.drop_index("ix_metric_alerts_state_started", table_name="metric_alerts")
    op.drop_table("metric_alerts")
    op.drop_index("ix_metric_rollups_query", table_name="metric_rollups")
    op.drop_table("metric_rollups")
    op.drop_index("ix_metric_samples_entity_metric_time", table_name="metric_samples")
    op.drop_index("ix_metric_samples_metric_time", table_name="metric_samples")
    op.drop_table("metric_samples")
    op.drop_index("ix_metric_entities_type_seen", table_name="metric_entities")
    op.drop_index("ix_metric_entities_last_seen_at", table_name="metric_entities")
    op.drop_table("metric_entities")
