from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class SetupClaim(Base):
    __tablename__ = "setup_claims"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default="initial-owner")
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    csrf_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class ApiToken(Base):
    __tablename__ = "api_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    scopes_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class Operation(Base):
    __tablename__ = "operations"
    __table_args__ = (
        UniqueConstraint("actor_id", "kind", "idempotency_key", name="uq_operation_idempotency"),
        Index("ix_operations_status_not_before", "status", "not_before"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True, default="queued")
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    resource_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    request_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    event_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    leased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class OperationEvent(Base):
    __tablename__ = "operation_events"
    __table_args__ = (
        UniqueConstraint("operation_id", "sequence", name="uq_operation_event_sequence"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    operation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("operations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(String(512), nullable=False)
    data_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class HardwareSnapshot(Base):
    __tablename__ = "hardware_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    operation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("operations.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    detector_schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class WizardSession(Base):
    __tablename__ = "wizard_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workflow: Mapped[str] = mapped_column(String(64), nullable=False)
    workflow_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="simple")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft", index=True)
    current_step: Mapped[str] = mapped_column(String(64), nullable=False, default="layout")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    hardware_snapshot_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("hardware_snapshots.id", ondelete="RESTRICT"), nullable=True
    )
    answers_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    plan_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class Plan(Base):
    __tablename__ = "plans"
    __table_args__ = (
        UniqueConstraint("wizard_session_id", "revision", name="uq_plan_wizard_revision"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    wizard_session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("wizard_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    document_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class PlanApproval(Base):
    """Immutable evidence that an actor approved one exact destructive plan."""

    __tablename__ = "plan_approvals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    plan_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("plans.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    wizard_session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("wizard_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    wizard_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    hardware_snapshot_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("hardware_snapshots.id", ondelete="RESTRICT"),
        nullable=False,
    )
    hardware_snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    device_binding_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    selected_device_ids_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    confirmation_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class IntegrationConnection(Base):
    __tablename__ = "integration_connections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    adapter: Mapped[str] = mapped_column(String(64), nullable=False, default="servarr")
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    expected_product: Mapped[str] = mapped_column(String(32), nullable=False)
    base_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    approved_ips_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    allow_localhost: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    api_key_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    verify_tls: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    discovered_product: Mapped[str | None] = mapped_column(String(32), nullable=True)
    product_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    capabilities_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    state_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class ConnectivityService(Base):
    __tablename__ = "connectivity_services"
    __table_args__ = (UniqueConstraint("protocol", "name", name="uq_connectivity_protocol_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    protocol: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    config_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    secret_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    state_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    last_error_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class ShareAcl(Base):
    __tablename__ = "share_acls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    connectivity_service_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("connectivity_services.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    plan_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    plan_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    state_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class StorageEntity(Base):
    """One durable logical storage object, independent of its current Linux paths."""

    __tablename__ = "storage_entities"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    stable_identity: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    storage_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="block")
    filesystem_uuid: Mapped[str | None] = mapped_column(String(128), nullable=True)
    mountpoint: Mapped[str] = mapped_column(String(4096), nullable=False)
    presentation_device: Mapped[str] = mapped_column(String(4096), nullable=False)
    capacity_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    logical_sector_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    physical_sector_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    topology_state: Mapped[str] = mapped_column(String(32), nullable=False, default="single_path")
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="scsi")
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class StorageGroup(Base):
    """A user-facing stable namespace composed from one or more storage backends."""

    __tablename__ = "storage_groups"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    namespace_path: Mapped[str] = mapped_column(String(4096), nullable=False, unique=True)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False, default="media")
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="active", index=True)
    policy_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class PhysicalDisk(Base):
    """Durable disk registry; kernel paths are observations, never identity."""

    __tablename__ = "physical_disks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    stable_identity: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    kernel_path: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    serial: Mapped[str | None] = mapped_column(String(256), nullable=True)
    wwn: Mapped[str | None] = mapped_column(String(256), nullable=True)
    vendor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model: Mapped[str | None] = mapped_column(String(256), nullable=True)
    capacity_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    logical_sector_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    physical_sector_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    media_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    health_state: Mapped[str] = mapped_column(String(32), nullable=False, default="not_reported")
    lifecycle_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="discovered", index=True
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, index=True
    )


class StorageBackend(Base):
    """A lifecycle-managed backend assigned to a stable Storage Group namespace."""

    __tablename__ = "storage_backends"
    __table_args__ = (
        UniqueConstraint("storage_group_id", "stable_identity", name="uq_group_backend_identity"),
        UniqueConstraint("physical_disk_id", name="uq_storage_backend_physical_disk"),
        UniqueConstraint("storage_entity_id", name="uq_storage_backend_storage_entity"),
        Index("ix_storage_backends_group_state", "storage_group_id", "lifecycle_state"),
        Index(
            "uq_storage_backends_preferred_write",
            "storage_group_id",
            unique=True,
            sqlite_where=text("lifecycle_state = 'preferred_write'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    storage_group_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("storage_groups.id", ondelete="CASCADE"), nullable=False
    )
    storage_entity_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("storage_entities.id", ondelete="SET NULL"), nullable=True
    )
    physical_disk_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("physical_disks.id", ondelete="SET NULL"), nullable=True
    )
    stable_identity: Mapped[str] = mapped_column(String(512), nullable=False)
    namespace_path: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="data")
    lifecycle_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="assigned", index=True
    )
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class StorageLifecycleEvent(Base):
    __tablename__ = "storage_lifecycle_events"
    __table_args__ = (
        Index("ix_storage_lifecycle_events_group_time", "storage_group_id", "occurred_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    storage_group_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("storage_groups.id", ondelete="CASCADE"), nullable=False
    )
    storage_backend_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("storage_backends.id", ondelete="SET NULL"), nullable=True
    )
    physical_disk_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("physical_disks.id", ondelete="SET NULL"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resulting_state: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(36), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class StorageDrainJob(Base):
    """Durable control record for one resumable Storage Group evacuation."""

    __tablename__ = "storage_drain_jobs"
    __table_args__ = (
        Index("ix_storage_drain_jobs_group_status", "storage_group_id", "status"),
        Index("ix_storage_drain_jobs_updated_at", "updated_at"),
    )

    id: Mapped[str] = mapped_column(
        String(36), ForeignKey("operations.id", ondelete="CASCADE"), primary_key=True
    )
    storage_group_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("storage_groups.id", ondelete="RESTRICT"), nullable=False
    )
    source_backend_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("storage_backends.id", ondelete="RESTRICT"), nullable=False
    )
    plan_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    phase: Mapped[str] = mapped_column(String(32), nullable=False, default="preflight")
    verification_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    pause_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    files_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    files_copied: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    files_verified: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bytes_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bytes_copied: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_relative_path: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    report_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class StorageDrainEntry(Base):
    """One immutable source-file observation and its durable drain checkpoint."""

    __tablename__ = "storage_drain_entries"
    __table_args__ = (
        UniqueConstraint("job_id", "relative_path", name="uq_storage_drain_entry_path"),
        Index("ix_storage_drain_entries_job_status_id", "job_id", "status", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("storage_drain_jobs.id", ondelete="CASCADE"), nullable=False
    )
    relative_path: Mapped[str] = mapped_column(String(4096), nullable=False)
    destination_backend_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("storage_backends.id", ondelete="RESTRICT"), nullable=False
    )
    source_size: Mapped[int] = mapped_column(Integer, nullable=False)
    source_mtime_ns: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    digest_algorithm: Mapped[str | None] = mapped_column(String(16), nullable=True)
    digest_hex: Mapped[str | None] = mapped_column(String(128), nullable=True)
    copied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class StorageRedundancyEvent(Base):
    """Durable, user-visible controller/path lifecycle history."""

    __tablename__ = "storage_redundancy_events"
    __table_args__ = (
        Index("ix_storage_redundancy_events_storage_time", "storage_entity_id", "occurred_at"),
        Index("ix_storage_redundancy_events_type_time", "event_type", "occurred_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    storage_entity_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("storage_entities.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    path_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("storage_paths.id", ondelete="SET NULL"), nullable=True
    )
    controller_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("storage_controllers.id", ondelete="SET NULL"), nullable=True
    )
    operation_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("operations.id", ondelete="SET NULL"), nullable=True
    )
    previous_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resulting_state: Mapped[str] = mapped_column(String(32), nullable=False)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class StorageController(Base):
    __tablename__ = "storage_controllers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    stable_identity: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str | None] = mapped_column(String(256), nullable=True)
    state_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, index=True
    )


class StoragePath(Base):
    __tablename__ = "storage_paths"
    __table_args__ = (
        UniqueConstraint("storage_entity_id", "stable_path_identity", name="uq_storage_path"),
        Index("ix_storage_paths_entity_state", "storage_entity_id", "state"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    storage_entity_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("storage_entities.id", ondelete="CASCADE"), nullable=False
    )
    controller_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("storage_controllers.id", ondelete="SET NULL"), nullable=True
    )
    stable_path_identity: Mapped[str] = mapped_column(String(512), nullable=False)
    kernel_path: Mapped[str] = mapped_column(String(4096), nullable=False)
    logical_storage_identity: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    protocol: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    optimized: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, index=True
    )


class AddonInstallation(Base):
    __tablename__ = "addon_installations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="installed")
    last_error_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class UpdateState(Base):
    __tablename__ = "update_state"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default="system")
    channel: Mapped[str] = mapped_column(String(32), nullable=False, default="stable")
    latest_metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    metadata_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_operation_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("operations.id", ondelete="SET NULL"), nullable=True
    )
    last_error_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    target_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class MetricEntity(Base):
    __tablename__ = "metric_entities"
    __table_args__ = (
        UniqueConstraint("entity_type", "stable_id", name="uq_metric_entity_stable_id"),
        Index("ix_metric_entities_type_seen", "entity_type", "last_seen_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    stable_id: Mapped[str] = mapped_column(String(512), nullable=False)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    labels_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    topology_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, index=True
    )


class MetricSample(Base):
    __tablename__ = "metric_samples"
    __table_args__ = (
        UniqueConstraint(
            "entity_id", "metric_id", "observed_at", name="uq_metric_sample_observation"
        ),
        Index("ix_metric_samples_metric_time", "metric_id", "observed_at"),
        Index("ix_metric_samples_entity_metric_time", "entity_id", "metric_id", "observed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("metric_entities.id", ondelete="CASCADE"), nullable=False
    )
    metric_id: Mapped[str] = mapped_column(String(128), nullable=False)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_text: Mapped[str | None] = mapped_column(String(128), nullable=True)
    quality: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    collection_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    raw: Mapped[bool] = mapped_column(Boolean, nullable=False)
    labels_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(96), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class MetricRollup(Base):
    __tablename__ = "metric_rollups"
    __table_args__ = (
        UniqueConstraint(
            "entity_id",
            "metric_id",
            "resolution",
            "period_start",
            name="uq_metric_rollup_period",
        ),
        Index("ix_metric_rollups_query", "entity_id", "metric_id", "resolution", "period_start"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("metric_entities.id", ondelete="CASCADE"), nullable=False
    )
    metric_id: Mapped[str] = mapped_column(String(128), nullable=False)
    resolution: Mapped[str] = mapped_column(String(16), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    minimum: Mapped[float | None] = mapped_column(Float, nullable=True)
    maximum: Mapped[float | None] = mapped_column(Float, nullable=True)
    mean: Mapped[float | None] = mapped_column(Float, nullable=True)
    first: Mapped[float | None] = mapped_column(Float, nullable=True)
    last: Mapped[float | None] = mapped_column(Float, nullable=True)
    first_text: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_text: Mapped[str | None] = mapped_column(String(128), nullable=True)
    transition_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    states_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    p50: Mapped[float | None] = mapped_column(Float, nullable=True)
    p95: Mapped[float | None] = mapped_column(Float, nullable=True)
    p99: Mapped[float | None] = mapped_column(Float, nullable=True)
    quality: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class MetricAlert(Base):
    __tablename__ = "metric_alerts"
    __table_args__ = (
        Index("ix_metric_alerts_state_started", "state", "started_at"),
        Index("ix_metric_alerts_entity_metric", "entity_id", "metric_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    rule_id: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("metric_entities.id", ondelete="CASCADE"), nullable=False
    )
    metric_id: Mapped[str] = mapped_column(String(128), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    trigger_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    threshold_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    topology_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class MetricAlertRule(Base):
    __tablename__ = "metric_alert_rules"
    __table_args__ = (Index("ix_metric_alert_rules_enabled_metric", "enabled", "metric_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    metric_id: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("metric_entities.id", ondelete="CASCADE"), nullable=True
    )
    operator: Mapped[str] = mapped_column(String(8), nullable=False)
    warning_value: Mapped[float] = mapped_column(Float, nullable=False)
    critical_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    clear_value: Mapped[float] = mapped_column(Float, nullable=False)
    sustained_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class TelemetryState(Base):
    __tablename__ = "telemetry_state"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    state_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
