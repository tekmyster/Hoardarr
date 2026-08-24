"""Add persistent fleet lifecycle-event cursors.

Revision ID: 0021_fleet_event_cursor
Revises: 0020_fleet_telemetry
"""

import sqlalchemy as sa
from alembic import op

revision = "0021_fleet_event_cursor"
down_revision = "0020_fleet_telemetry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fleet_telemetry_cursors",
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("last_occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_record_id", sa.String(length=64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("source"),
    )


def downgrade() -> None:
    op.drop_table("fleet_telemetry_cursors")
