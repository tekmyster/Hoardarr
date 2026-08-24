"""Add bounded alert suppression lifecycle fields.

Revision ID: 0018_alert_suppression
Revises: 0017_remote_backups
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018_alert_suppression"
down_revision = "0017_remote_backups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("metric_alerts", sa.Column("suppressed_until", sa.DateTime(timezone=True)))
    op.add_column("metric_alerts", sa.Column("suppressed_by", sa.String(36)))
    op.add_column("metric_alerts", sa.Column("suppression_reason", sa.String(256)))


def downgrade() -> None:
    with op.batch_alter_table("metric_alerts") as batch:
        batch.drop_column("suppression_reason")
        batch.drop_column("suppressed_by")
        batch.drop_column("suppressed_until")
