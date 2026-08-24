"""Add durable foreign migration jobs and file checkpoints.

Revision ID: 0016_foreign_migration_jobs
Revises: 0015_foreign_import_evidence
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016_foreign_migration_jobs"
down_revision = "0015_foreign_import_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "foreign_migration_jobs",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("candidate_id", sa.String(32), nullable=False),
        sa.Column("destination_backend_id", sa.String(36), nullable=False),
        sa.Column("plan_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("phase", sa.String(32), nullable=False),
        sa.Column("verification_mode", sa.String(16), nullable=False),
        sa.Column("collision_policy", sa.String(24), nullable=False),
        sa.Column("pause_requested", sa.Boolean(), nullable=False),
        sa.Column("files_total", sa.Integer(), nullable=False),
        sa.Column("files_copied", sa.Integer(), nullable=False),
        sa.Column("files_verified", sa.Integer(), nullable=False),
        sa.Column("files_reused", sa.Integer(), nullable=False),
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
            ["destination_backend_id"], ["storage_backends.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_foreign_migration_jobs_status_updated",
        "foreign_migration_jobs",
        ["status", "updated_at"],
    )
    op.create_index(
        "ix_foreign_migration_jobs_candidate_created",
        "foreign_migration_jobs",
        ["candidate_id", "created_at"],
    )
    op.create_table(
        "foreign_migration_entries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.String(36), nullable=False),
        sa.Column("relative_path", sa.String(4096), nullable=False),
        sa.Column("source_size", sa.Integer(), nullable=False),
        sa.Column("source_mtime_ns", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("digest_algorithm", sa.String(16), nullable=True),
        sa.Column("digest_hex", sa.String(128), nullable=True),
        sa.Column("copied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["foreign_migration_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "relative_path", name="uq_foreign_migration_entry_path"),
    )
    op.create_index(
        "ix_foreign_migration_entries_job_status_id",
        "foreign_migration_entries",
        ["job_id", "status", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_foreign_migration_entries_job_status_id",
        table_name="foreign_migration_entries",
    )
    op.drop_table("foreign_migration_entries")
    op.drop_index(
        "ix_foreign_migration_jobs_candidate_created", table_name="foreign_migration_jobs"
    )
    op.drop_index(
        "ix_foreign_migration_jobs_status_updated", table_name="foreign_migration_jobs"
    )
    op.drop_table("foreign_migration_jobs")
