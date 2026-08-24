"""Add privacy settings and bounded fleet telemetry queue.

Revision ID: 0020_fleet_telemetry
Revises: 0019_webhook_delivery
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020_fleet_telemetry"
down_revision = "0019_webhook_delivery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fleet_telemetry_state",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("installation_id", sa.String(36), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("hardware_enabled", sa.Boolean(), nullable=False),
        sa.Column("enhanced_enabled", sa.Boolean(), nullable=False),
        sa.Column("content_enabled", sa.Boolean(), nullable=False),
        sa.Column("country_code", sa.String(2), nullable=True),
        sa.Column("timezone", sa.String(128), nullable=False),
        sa.Column("location_detection_method", sa.String(32), nullable=False),
        sa.Column("credential_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("credential_fingerprint", sa.String(16), nullable=True),
        sa.Column("registration_status", sa.String(32), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("installation_id"),
    )
    op.create_table(
        "fleet_telemetry_queue",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("message_type", sa.String(32), nullable=False),
        sa.Column("telemetry_level", sa.Integer(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error_json", sa.JSON(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_fleet_telemetry_queue_due",
        "fleet_telemetry_queue",
        ["status", "next_attempt_at"],
    )
    op.create_index(
        "ix_fleet_telemetry_queue_priority_created",
        "fleet_telemetry_queue",
        ["priority", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_fleet_telemetry_queue_priority_created", table_name="fleet_telemetry_queue")
    op.drop_index("ix_fleet_telemetry_queue_due", table_name="fleet_telemetry_queue")
    op.drop_table("fleet_telemetry_queue")
    op.drop_table("fleet_telemetry_state")
