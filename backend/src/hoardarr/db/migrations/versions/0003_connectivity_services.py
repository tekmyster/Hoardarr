"""Add managed connectivity services.

Revision ID: 0003_connectivity_services
Revises: 0002_plan_approvals
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_connectivity_services"
down_revision = "0002_plan_approvals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "connectivity_services",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("protocol", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("config_sha256", sa.String(length=64), nullable=False),
        sa.Column("secret_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("state_json", sa.JSON(), nullable=False),
        sa.Column("last_error_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("protocol", "name", name="uq_connectivity_protocol_name"),
    )
    op.create_index(
        op.f("ix_connectivity_services_protocol"),
        "connectivity_services",
        ["protocol"],
        unique=False,
    )
    op.create_index(
        op.f("ix_connectivity_services_status"),
        "connectivity_services",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_connectivity_services_status"), table_name="connectivity_services")
    op.drop_index(op.f("ix_connectivity_services_protocol"), table_name="connectivity_services")
    op.drop_table("connectivity_services")
