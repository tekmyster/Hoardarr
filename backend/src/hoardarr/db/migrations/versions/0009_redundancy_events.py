"""Persist controller redundancy lifecycle events.

Revision ID: 0009_redundancy_events
Revises: 0008_storage_redundancy
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_redundancy_events"
down_revision = "0008_storage_redundancy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "storage_redundancy_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("storage_entity_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("path_id", sa.String(length=36), nullable=True),
        sa.Column("controller_id", sa.String(length=36), nullable=True),
        sa.Column("operation_id", sa.String(length=36), nullable=True),
        sa.Column("previous_state", sa.String(length=32), nullable=True),
        sa.Column("resulting_state", sa.String(length=32), nullable=False),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["controller_id"], ["storage_controllers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["operation_id"], ["operations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["path_id"], ["storage_paths.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["storage_entity_id"], ["storage_entities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_storage_redundancy_events_storage_time",
        "storage_redundancy_events",
        ["storage_entity_id", "occurred_at"],
    )
    op.create_index(
        "ix_storage_redundancy_events_type_time",
        "storage_redundancy_events",
        ["event_type", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_storage_redundancy_events_type_time", table_name="storage_redundancy_events")
    op.drop_index("ix_storage_redundancy_events_storage_time", table_name="storage_redundancy_events")
    op.drop_table("storage_redundancy_events")
