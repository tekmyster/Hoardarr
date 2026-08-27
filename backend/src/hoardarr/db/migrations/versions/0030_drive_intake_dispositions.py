"""Persist immutable, plan-bound drive intake dispositions.

Revision ID: 0030_drive_intake_dispositions
Revises: 0029_user_active_state
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0030_drive_intake_dispositions"
down_revision = "0029_user_active_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "drive_intake_dispositions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("physical_disk_id", sa.String(length=36), nullable=False),
        sa.Column("stable_identity", sa.String(length=512), nullable=False),
        sa.Column("operation_id", sa.String(length=36), nullable=False),
        sa.Column("evaluating_operation_id", sa.String(length=36), nullable=False),
        sa.Column("plan_id", sa.String(length=36), nullable=False),
        sa.Column("plan_sha256", sa.String(length=64), nullable=False),
        sa.Column("wizard_revision", sa.Integer(), nullable=False),
        sa.Column("hardware_snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("hardware_snapshot_sha256", sa.String(length=64), nullable=False),
        sa.Column("device_binding_sha256", sa.String(length=64), nullable=False),
        sa.Column("device_fingerprint_sha256", sa.String(length=64), nullable=False),
        sa.Column("device_fingerprint_json", sa.JSON(), nullable=False),
        sa.Column("execution_result_sha256", sa.String(length=64), nullable=False),
        sa.Column("policy_name", sa.String(length=128), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("policy_sha256", sa.String(length=64), nullable=False),
        sa.Column("intended_use", sa.String(length=64), nullable=False),
        sa.Column("required_tests_json", sa.JSON(), nullable=False),
        sa.Column("test_results_json", sa.JSON(), nullable=False),
        sa.Column("disposition", sa.String(length=16), nullable=False),
        sa.Column("reason_codes_json", sa.JSON(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "disposition IN ('PASS','FAIL','QUARANTINED','INCOMPLETE','UNSUPPORTED','SOURCE_ONLY')",
            name="ck_drive_intake_disposition",
        ),
        sa.ForeignKeyConstraint(
            ["evaluating_operation_id"], ["operations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["hardware_snapshot_id"], ["hardware_snapshots.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["operation_id"], ["operations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["physical_disk_id"], ["physical_disks.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "operation_id",
            "physical_disk_id",
            "policy_sha256",
            name="uq_drive_intake_operation_disk_policy",
        ),
    )
    op.create_index(
        "ix_drive_intake_disk_evaluated",
        "drive_intake_dispositions",
        ["physical_disk_id", "evaluated_at"],
        unique=False,
    )
    op.create_index(
        "ix_drive_intake_disposition",
        "drive_intake_dispositions",
        ["disposition"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_drive_intake_disposition", table_name="drive_intake_dispositions")
    op.drop_index(
        "ix_drive_intake_disk_evaluated",
        table_name="drive_intake_dispositions",
    )
    op.drop_table("drive_intake_dispositions")
