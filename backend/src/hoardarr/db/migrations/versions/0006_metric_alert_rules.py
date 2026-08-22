"""Add configurable metric alert rules.

Revision ID: 0006_metric_alert_rules
Revises: 0005_enterprise_telemetry
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_metric_alert_rules"
down_revision = "0005_enterprise_telemetry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "metric_alert_rules",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("metric_id", sa.String(length=128), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=True),
        sa.Column("entity_id", sa.String(length=36), nullable=True),
        sa.Column("operator", sa.String(length=8), nullable=False),
        sa.Column("warning_value", sa.Float(), nullable=False),
        sa.Column("critical_value", sa.Float(), nullable=True),
        sa.Column("clear_value", sa.Float(), nullable=False),
        sa.Column("sustained_seconds", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["entity_id"], ["metric_entities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_metric_alert_rules_enabled_metric",
        "metric_alert_rules",
        ["enabled", "metric_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_metric_alert_rules_enabled_metric", table_name="metric_alert_rules")
    op.drop_table("metric_alert_rules")
