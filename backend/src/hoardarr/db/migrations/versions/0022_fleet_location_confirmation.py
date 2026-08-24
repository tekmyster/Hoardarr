"""Track explicit confirmation of fleet location settings.

Revision ID: 0022_fleet_location_confirmation
Revises: 0021_fleet_event_cursor
"""

import sqlalchemy as sa
from alembic import op

revision = "0022_fleet_location_confirmation"
down_revision = "0021_fleet_event_cursor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "fleet_telemetry_state",
        sa.Column("location_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("fleet_telemetry_state", "location_confirmed")
