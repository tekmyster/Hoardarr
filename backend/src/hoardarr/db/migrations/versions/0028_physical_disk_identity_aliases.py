"""Preserve retired physical disk identities across control-plane migration.

Revision ID: 0028_physical_disk_identity_aliases
Revises: 0027_logical_storage_pool_geometry
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0028_physical_disk_identity_aliases"
down_revision = "0027_logical_storage_pool_geometry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "physical_disk_identity_aliases",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("physical_disk_id", sa.String(length=36), nullable=False),
        sa.Column("alias_identity", sa.String(length=512), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["physical_disk_id"], ["physical_disks.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "alias_identity", name="uq_physical_disk_identity_alias"
        ),
    )
    op.create_index(
        "ix_physical_disk_identity_alias_disk",
        "physical_disk_identity_aliases",
        ["physical_disk_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_physical_disk_identity_alias_disk",
        table_name="physical_disk_identity_aliases",
    )
    op.drop_table("physical_disk_identity_aliases")
