"""Add resumable Storage Group drain jobs and file checkpoints.

Revision ID: 0011_storage_drain_jobs
Revises: 0010_storage_groups
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011_storage_drain_jobs"
down_revision = "0010_storage_groups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "storage_drain_jobs",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("storage_group_id", sa.String(36), nullable=False),
        sa.Column("source_backend_id", sa.String(36), nullable=False),
        sa.Column("plan_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("phase", sa.String(32), nullable=False),
        sa.Column("verification_mode", sa.String(16), nullable=False),
        sa.Column("pause_requested", sa.Boolean(), nullable=False),
        sa.Column("files_total", sa.Integer(), nullable=False),
        sa.Column("files_copied", sa.Integer(), nullable=False),
        sa.Column("files_verified", sa.Integer(), nullable=False),
        sa.Column("bytes_total", sa.Integer(), nullable=False),
        sa.Column("bytes_copied", sa.Integer(), nullable=False),
        sa.Column("current_relative_path", sa.String(4096), nullable=True),
        sa.Column("report_json", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["id"], ["operations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_backend_id"], ["storage_backends.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["storage_group_id"], ["storage_groups.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_storage_drain_jobs_group_status",
        "storage_drain_jobs",
        ["storage_group_id", "status"],
    )
    op.create_index(
        "ix_storage_drain_jobs_updated_at", "storage_drain_jobs", ["updated_at"]
    )
    op.create_table(
        "storage_drain_entries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.String(36), nullable=False),
        sa.Column("relative_path", sa.String(4096), nullable=False),
        sa.Column("destination_backend_id", sa.String(36), nullable=False),
        sa.Column("source_size", sa.Integer(), nullable=False),
        sa.Column("source_mtime_ns", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("digest_algorithm", sa.String(16), nullable=True),
        sa.Column("digest_hex", sa.String(128), nullable=True),
        sa.Column("copied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["destination_backend_id"], ["storage_backends.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["job_id"], ["storage_drain_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "relative_path", name="uq_storage_drain_entry_path"),
    )
    op.create_index(
        "ix_storage_drain_entries_job_status_id",
        "storage_drain_entries",
        ["job_id", "status", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_storage_drain_entries_job_status_id", table_name="storage_drain_entries"
    )
    op.drop_table("storage_drain_entries")
    op.drop_index("ix_storage_drain_jobs_updated_at", table_name="storage_drain_jobs")
    op.drop_index("ix_storage_drain_jobs_group_status", table_name="storage_drain_jobs")
    op.drop_table("storage_drain_jobs")
