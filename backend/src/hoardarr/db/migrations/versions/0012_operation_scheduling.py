"""Add durable operation scheduling.

Revision ID: 0012_operation_scheduling
Revises: 0011_storage_drain_jobs
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_operation_scheduling"
down_revision = "0011_storage_drain_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "operations",
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_operations_status_not_before",
        "operations",
        ["status", "not_before"],
    )


def downgrade() -> None:
    op.drop_index("ix_operations_status_not_before", table_name="operations")
    op.drop_column("operations", "not_before")
