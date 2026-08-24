from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
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


class ForeignImportEvidence(Base):
    __tablename__ = "foreign_import_evidence"
    __table_args__ = (
        Index("ix_foreign_import_evidence_source_active", "source_type", "active", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    document_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class TopologyExpectation(Base):
    __tablename__ = "topology_expectations"
    __table_args__ = (Index("ix_topology_expectations_active_updated", "active", "updated_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    source_snapshot_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("hardware_snapshots.id", ondelete="RESTRICT"), nullable=False
    )
    expected_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class TopologyDriftEvent(Base):
    __tablename__ = "topology_drift_events"
    __table_args__ = (
        Index("ix_topology_drift_expectation_state", "expectation_id", "state"),
        Index("ix_topology_drift_fingerprint_state", "fingerprint", "state"),
        Index("ix_topology_drift_last_seen", "last_seen_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    expectation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("topology_expectations.id", ondelete="CASCADE"), nullable=False
    )
    snapshot_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("hardware_snapshots.id", ondelete="RESTRICT"), nullable=False
    )
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(512), nullable=False)
    message: Mapped[str] = mapped_column(String(512), nullable=False)
    expected_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    observed_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TopologyPlan(Base):
    __tablename__ = "topology_plans"
    __table_args__ = (Index("ix_topology_plans_updated", "updated_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    template_id: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    plan_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
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


class StorageVolume(Base):
    """A provider-backed dataset, filesystem volume, block volume, or LUN."""

    __tablename__ = "storage_volumes"
    __table_args__ = (
        UniqueConstraint(
            "provider", "provider_resource_id", name="uq_storage_volume_provider_resource"
        ),
        CheckConstraint("size_bytes IS NULL OR size_bytes >= 0", name="ck_volume_size"),
        CheckConstraint(
            "allocated_bytes IS NULL OR allocated_bytes >= 0", name="ck_volume_allocated"
        ),
        Index("ix_storage_volumes_parent", "parent_storage_entity_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    stable_identity: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_resource_id: Mapped[str] = mapped_column(String(512), nullable=False)
    presentation: Mapped[str] = mapped_column(String(16), nullable=False)
    parent_storage_entity_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("storage_entities.id", ondelete="SET NULL"), nullable=True
    )
    mountpoint: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    device_path: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    filesystem_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    filesystem_uuid: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    allocated_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lifecycle_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", index=True
    )
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    capabilities_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    capabilities_detected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class HAConfiguration(Base):
    """Persistent two-node peer awareness; it does not authorize automatic ownership changes."""

    __tablename__ = "ha_configurations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    mode: Mapped[str] = mapped_column(
        String(64), nullable=False, default="controlled_single_writer"
    )
    local_node_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    local_name: Mapped[str] = mapped_column(String(128), nullable=False)
    local_fqdn: Mapped[str] = mapped_column(String(253), nullable=False)
    local_ip: Mapped[str] = mapped_column(String(64), nullable=False)
    local_role: Mapped[str] = mapped_column(String(32), nullable=False)
    peer_node_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    peer_name: Mapped[str] = mapped_column(String(128), nullable=False)
    peer_fqdn: Mapped[str] = mapped_column(String(253), nullable=False)
    peer_ip: Mapped[str] = mapped_column(String(64), nullable=False)
    peer_role: Mapped[str] = mapped_column(String(32), nullable=False)
    service_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    current_owner_node_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    peer_reachable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    peer_last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    peer_report_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class HAEvent(Base):
    __tablename__ = "ha_events"
    __table_args__ = (Index("ix_ha_events_time", "occurred_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    configuration_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("ha_configurations.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    cause: Mapped[str | None] = mapped_column(String(512), nullable=True)
    previous_owner_node_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    resulting_owner_node_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    detail_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
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


class ForeignMigrationJob(Base):
    """Durable, restart-safe checkpoint for one read-only foreign intake."""

    __tablename__ = "foreign_migration_jobs"
    __table_args__ = (
        Index("ix_foreign_migration_jobs_status_updated", "status", "updated_at"),
        Index("ix_foreign_migration_jobs_candidate_created", "candidate_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        String(36), ForeignKey("operations.id", ondelete="CASCADE"), primary_key=True
    )
    candidate_id: Mapped[str] = mapped_column(String(32), nullable=False)
    destination_backend_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("storage_backends.id", ondelete="RESTRICT"), nullable=False
    )
    plan_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    phase: Mapped[str] = mapped_column(String(32), nullable=False, default="preflight")
    verification_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    collision_policy: Mapped[str] = mapped_column(String(24), nullable=False)
    pause_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    files_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    files_copied: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    files_verified: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    files_reused: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
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


class ForeignMigrationEntry(Base):
    """Immutable source observation and copy/verification checkpoint."""

    __tablename__ = "foreign_migration_entries"
    __table_args__ = (
        UniqueConstraint("job_id", "relative_path", name="uq_foreign_migration_entry_path"),
        Index("ix_foreign_migration_entries_job_status_id", "job_id", "status", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("foreign_migration_jobs.id", ondelete="CASCADE"), nullable=False
    )
    relative_path: Mapped[str] = mapped_column(String(4096), nullable=False)
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


class RemoteBackupTarget(Base):
    __tablename__ = "remote_backup_targets"
    __table_args__ = (
        UniqueConstraint("name", name="uq_remote_backup_target_name"),
        Index("ix_remote_backup_targets_enabled_updated", "enabled", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    endpoint_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    region: Mapped[str] = mapped_column(String(64), nullable=False, default="us-east-1")
    bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    prefix: Mapped[str] = mapped_column(String(1024), nullable=False, default="hoardarr")
    force_path_style: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verify_tls: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    allow_private_network: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    allow_insecure_http: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    bandwidth_limit_mib: Mapped[int | None] = mapped_column(Integer, nullable=True)
    schedule_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    secret_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    credential_fingerprint: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="not_tested")
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class RemoteBackupRun(Base):
    __tablename__ = "remote_backup_runs"
    __table_args__ = (
        Index("ix_remote_backup_runs_target_created", "target_id", "created_at"),
        Index("ix_remote_backup_runs_status_updated", "status", "updated_at"),
    )

    id: Mapped[str] = mapped_column(
        String(36), ForeignKey("operations.id", ondelete="CASCADE"), primary_key=True
    )
    target_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("remote_backup_targets.id", ondelete="RESTRICT"), nullable=False
    )
    backup_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    object_key: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    upload_id: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    completed_parts_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    artifact_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    artifact_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    phase: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    report_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class WebhookEndpoint(Base):
    __tablename__ = "webhook_endpoints"
    __table_args__ = (
        UniqueConstraint("name", name="uq_webhook_endpoint_name"),
        Index("ix_webhook_endpoints_enabled_updated", "enabled", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    approved_ips_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    allow_localhost: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verify_tls: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    event_types_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    secret_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    secret_fingerprint: Mapped[str] = mapped_column(String(16), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="not_tested")
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        UniqueConstraint("endpoint_id", "event_id", name="uq_webhook_delivery_event"),
        Index("ix_webhook_deliveries_due", "status", "next_attempt_at"),
        Index("ix_webhook_deliveries_endpoint_created", "endpoint_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    endpoint_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("webhook_endpoints.id", ondelete="CASCADE"), nullable=False
    )
    event_id: Mapped[str] = mapped_column(String(160), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    response_status: Mapped[int | None] = mapped_column(Integer)
    last_error_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class FleetTelemetryState(Base):
    """Persistent privacy choices and installation-scoped fleet identity."""

    __tablename__ = "fleet_telemetry_state"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default="system")
    installation_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    hardware_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    enhanced_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    content_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    country_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    timezone: Mapped[str] = mapped_column(String(128), nullable=False, default="UTC")
    location_detection_method: Mapped[str] = mapped_column(
        String(32), nullable=False, default="os_timezone"
    )
    location_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    credential_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    credential_fingerprint: Mapped[str | None] = mapped_column(String(16), nullable=True)
    registration_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unregistered"
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class FleetTelemetryQueue(Base):
    """Bounded durable outbound queue; the browser never owns delivery state."""

    __tablename__ = "fleet_telemetry_queue"
    __table_args__ = (
        Index("ix_fleet_telemetry_queue_due", "status", "next_attempt_at"),
        Index("ix_fleet_telemetry_queue_priority_created", "priority", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    message_type: Mapped[str] = mapped_column(String(32), nullable=False)
    telemetry_level: Mapped[int] = mapped_column(Integer, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    last_error_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class FleetTelemetryCursor(Base):
    __tablename__ = "fleet_telemetry_cursors"

    source: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_record_id: Mapped[str] = mapped_column(String(64), nullable=False)
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
    suppressed_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    suppressed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    suppression_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)


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
