"""Add operator-declared topology planning documents.

Revision ID: 0014_topology_plans
Revises: 0013_topology_expectations
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014_topology_plans"
down_revision = "0013_topology_expectations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "topology_plans",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("template_id", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("plan_json", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_topology_plans_updated", "topology_plans", ["updated_at"])


def downgrade() -> None:
    op.drop_index("ix_topology_plans_updated", table_name="topology_plans")
    op.drop_table("topology_plans")
