"""Add immutable destructive-plan approvals.

Revision ID: 0002_plan_approvals
Revises: 0001_initial
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_plan_approvals"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plan_approvals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "plan_id",
            sa.String(36),
            sa.ForeignKey("plans.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "wizard_session_id",
            sa.String(36),
            sa.ForeignKey("wizard_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("wizard_revision", sa.Integer(), nullable=False),
        sa.Column("plan_sha256", sa.String(64), nullable=False),
        sa.Column(
            "hardware_snapshot_id",
            sa.String(36),
            sa.ForeignKey("hardware_snapshots.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("hardware_snapshot_sha256", sa.String(64), nullable=False),
        sa.Column("device_binding_sha256", sa.String(64), nullable=False),
        sa.Column("selected_device_ids_json", sa.JSON(), nullable=False),
        sa.Column("confirmation_sha256", sa.String(64), nullable=False),
        sa.Column("actor_type", sa.String(32), nullable=False),
        sa.Column("actor_id", sa.String(36), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_plan_approvals_wizard_session_id", "plan_approvals", ["wizard_session_id"])
    op.create_index("ix_plan_approvals_plan_sha256", "plan_approvals", ["plan_sha256"])
    op.create_index("ix_plan_approvals_actor_id", "plan_approvals", ["actor_id"])


def downgrade() -> None:
    raise RuntimeError("Hoardarr schema downgrades are not supported")
