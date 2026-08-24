"""Add persisted, provenance-bound foreign import evidence.

Revision ID: 0015_foreign_import_evidence
Revises: 0014_topology_plans
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015_foreign_import_evidence"
down_revision = "0014_topology_plans"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "foreign_import_evidence",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("source_type", sa.String(64), nullable=False),
        sa.Column("document_sha256", sa.String(64), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_sha256"),
    )
    op.create_index(
        "ix_foreign_import_evidence_source_active",
        "foreign_import_evidence",
        ["source_type", "active", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_foreign_import_evidence_source_active",
        table_name="foreign_import_evidence",
    )
    op.drop_table("foreign_import_evidence")
