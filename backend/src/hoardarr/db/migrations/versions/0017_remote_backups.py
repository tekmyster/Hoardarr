"""Add encrypted remote backup targets and durable backup runs.

Revision ID: 0017_remote_backups
Revises: 0016_foreign_migration_jobs
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017_remote_backups"
down_revision = "0016_foreign_migration_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "remote_backup_targets",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("endpoint_url", sa.String(2048), nullable=True),
        sa.Column("region", sa.String(64), nullable=False),
        sa.Column("bucket", sa.String(255), nullable=False),
        sa.Column("prefix", sa.String(1024), nullable=False),
        sa.Column("force_path_style", sa.Boolean(), nullable=False),
        sa.Column("verify_tls", sa.Boolean(), nullable=False),
        sa.Column("allow_private_network", sa.Boolean(), nullable=False),
        sa.Column("allow_insecure_http", sa.Boolean(), nullable=False),
        sa.Column("bandwidth_limit_mib", sa.Integer(), nullable=True),
        sa.Column("schedule_json", sa.JSON(), nullable=False),
        sa.Column("secret_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("credential_fingerprint", sa.String(16), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_json", sa.JSON(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_remote_backup_target_name"),
    )
    op.create_index(
        "ix_remote_backup_targets_enabled_updated",
        "remote_backup_targets",
        ["enabled", "updated_at"],
    )
    op.create_table(
        "remote_backup_runs",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("target_id", sa.String(36), nullable=False),
        sa.Column("backup_kind", sa.String(32), nullable=False),
        sa.Column("object_key", sa.String(2048), nullable=True),
        sa.Column("upload_id", sa.String(1024), nullable=True),
        sa.Column("completed_parts_json", sa.JSON(), nullable=False),
        sa.Column("artifact_sha256", sa.String(64), nullable=True),
        sa.Column("artifact_size_bytes", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("phase", sa.String(32), nullable=False),
        sa.Column("report_json", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["id"], ["operations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_id"], ["remote_backup_targets.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_remote_backup_runs_target_created",
        "remote_backup_runs",
        ["target_id", "created_at"],
    )
    op.create_index(
        "ix_remote_backup_runs_status_updated",
        "remote_backup_runs",
        ["status", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_remote_backup_runs_status_updated", table_name="remote_backup_runs")
    op.drop_index("ix_remote_backup_runs_target_created", table_name="remote_backup_runs")
    op.drop_table("remote_backup_runs")
    op.drop_index("ix_remote_backup_targets_enabled_updated", table_name="remote_backup_targets")
    op.drop_table("remote_backup_targets")
