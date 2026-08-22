"""Preserve rollup envelopes and state transitions.

Revision ID: 0007_telemetry_rollup_details
Revises: 0006_metric_alert_rules
"""

import sqlalchemy as sa
from alembic import op

revision = "0007_telemetry_rollup_details"
down_revision = "0006_metric_alert_rules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("metric_rollups") as batch:
        batch.add_column(sa.Column("first", sa.Float(), nullable=True))
        batch.add_column(sa.Column("first_text", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("last_text", sa.String(length=128), nullable=True))
        batch.add_column(
            sa.Column("transition_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(sa.Column("states_json", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("metric_rollups") as batch:
        batch.drop_column("states_json")
        batch.drop_column("transition_count")
        batch.drop_column("last_text")
        batch.drop_column("first_text")
        batch.drop_column("first")
