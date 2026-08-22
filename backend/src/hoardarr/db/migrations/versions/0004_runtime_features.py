"""Add ACL, add-on, and update runtime state.

Revision ID: 0004_runtime_features
Revises: 0003_connectivity_services
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_runtime_features"
down_revision = "0003_connectivity_services"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "share_acls",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("connectivity_service_id", sa.String(length=36), nullable=False),
        sa.Column("plan_json", sa.JSON(), nullable=False),
        sa.Column("plan_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("state_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["connectivity_service_id"], ["connectivity_services.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("connectivity_service_id"),
    )
    op.create_table(
        "addon_installations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("manifest_json", sa.JSON(), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("last_error_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "update_state",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("latest_metadata_json", sa.JSON(), nullable=True),
        sa.Column("metadata_sha256", sa.String(length=64), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_operation_id", sa.String(length=36), nullable=True),
        sa.Column("last_error_json", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["last_operation_id"], ["operations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("update_state")
    op.drop_table("addon_installations")
    op.drop_table("share_acls")
