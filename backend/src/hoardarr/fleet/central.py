from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    create_engine,
    delete,
    exists,
    func,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from hoardarr.core.secrets import SecretBox
from hoardarr.fleet.migrate import central_database_is_current
from hoardarr.fleet.service import canonical_json

SUPPORTED_SCHEMAS = frozenset({1})
MAX_BODY_BYTES = 512 * 1024
MAX_BATCH_RECORDS = 250
MAX_RECORD_BYTES = 128 * 1024


def utc_now() -> datetime:
    return datetime.now(UTC)


class CentralBase(DeclarativeBase):
    pass


class Installation(CentralBase):
    __tablename__ = "fleet_installations"

    installation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    credential_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    credential_fingerprint: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    platform_family: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    last_sequence_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class InstallationHeartbeat(CentralBase):
    __tablename__ = "fleet_installation_heartbeats"
    __table_args__ = (
        Index("ix_fleet_heartbeats_installation_time", "installation_id", "observed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    installation_id: Mapped[str] = mapped_column(ForeignKey("fleet_installations.installation_id"))
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    platform_family: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class VersionObservation(CentralBase):
    __tablename__ = "fleet_version_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    installation_id: Mapped[str] = mapped_column(ForeignKey("fleet_installations.installation_id"))
    version: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class HardwareSnapshot(CentralBase):
    __tablename__ = "fleet_hardware_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    installation_id: Mapped[str] = mapped_column(
        ForeignKey("fleet_installations.installation_id"), index=True
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    system_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class Drive(CentralBase):
    __tablename__ = "fleet_drives"

    drive_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    identity_version: Mapped[int] = mapped_column(Integer, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DriveObservation(CentralBase):
    __tablename__ = "fleet_drive_observations"
    __table_args__ = (Index("ix_fleet_drive_observation_drive_time", "drive_id", "observed_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    drive_id: Mapped[str] = mapped_column(ForeignKey("fleet_drives.drive_id"))
    installation_id: Mapped[str] = mapped_column(
        ForeignKey("fleet_installations.installation_id"), index=True
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class DriveLifecycleEvent(CentralBase):
    __tablename__ = "fleet_drive_lifecycle_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    drive_id: Mapped[str | None] = mapped_column(String(64), index=True)
    installation_id: Mapped[str] = mapped_column(
        ForeignKey("fleet_installations.installation_id"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class CategoryObservation(CentralBase):
    __tablename__ = "fleet_category_observations"
    __table_args__ = (Index("ix_fleet_category_kind_time", "kind", "observed_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    installation_id: Mapped[str] = mapped_column(
        ForeignKey("fleet_installations.installation_id"), index=True
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class ControllerObservation(CentralBase):
    __tablename__ = "fleet_controller_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    installation_id: Mapped[str] = mapped_column(
        ForeignKey("fleet_installations.installation_id"), index=True
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class StorageLayoutObservation(CentralBase):
    __tablename__ = "fleet_storage_layout_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    installation_id: Mapped[str] = mapped_column(
        ForeignKey("fleet_installations.installation_id"), index=True
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class ApplicationObservation(CentralBase):
    __tablename__ = "fleet_application_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    installation_id: Mapped[str] = mapped_column(
        ForeignKey("fleet_installations.installation_id"), index=True
    )
    product: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CapacityObservation(CentralBase):
    __tablename__ = "fleet_capacity_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    installation_id: Mapped[str] = mapped_column(
        ForeignKey("fleet_installations.installation_id"), index=True
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class FeatureUsageObservation(CentralBase):
    __tablename__ = "fleet_feature_usage_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    installation_id: Mapped[str] = mapped_column(
        ForeignKey("fleet_installations.installation_id"), index=True
    )
    feature: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GeographicSetting(CentralBase):
    __tablename__ = "fleet_geographic_settings"

    installation_id: Mapped[str] = mapped_column(
        ForeignKey("fleet_installations.installation_id"), primary_key=True
    )
    country_code: Mapped[str | None] = mapped_column(String(2))
    timezone: Mapped[str | None] = mapped_column(String(128))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IngestedBatch(CentralBase):
    __tablename__ = "fleet_ingested_batches"
    __table_args__ = (UniqueConstraint("installation_id", "sequence_number"),)

    batch_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    installation_id: Mapped[str] = mapped_column(
        ForeignKey("fleet_installations.installation_id"), index=True
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class IngestedRecord(CentralBase):
    __tablename__ = "fleet_ingested_records"
    __table_args__ = (UniqueConstraint("installation_id", "record_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    installation_id: Mapped[str] = mapped_column(
        ForeignKey("fleet_installations.installation_id"), index=True
    )
    record_id: Mapped[str] = mapped_column(String(36), nullable=False)
    batch_id: Mapped[str] = mapped_column(ForeignKey("fleet_ingested_batches.batch_id"))
    message_type: Mapped[str] = mapped_column(String(32), nullable=False)
    telemetry_level: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class CentralMaintenance(CentralBase):
    __tablename__ = "fleet_central_maintenance"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    last_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class Registration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    installation_id: uuid.UUID
    hoardarr_version: str = Field(min_length=1, max_length=64)
    build_commit: str | None = Field(default=None, max_length=80)
    schema_version: int
    platform_family: str = Field(min_length=1, max_length=64)
    heartbeat_at: datetime

    @field_validator("schema_version")
    @classmethod
    def schema_is_supported(cls, value: int) -> int:
        if value not in SUPPORTED_SCHEMAS:
            raise ValueError("unsupported telemetry schema")
        return value


class FleetCentralSettings(BaseModel):
    database_url: str
    secret_key_file: Path
    admin_token: str | None = None
    active_window_days: int = Field(default=30, ge=1, le=365)
    raw_retention_days: int = Field(default=90, ge=7, le=730)
    opt_in_retention_days: int = Field(default=30, ge=1, le=365)
    heartbeat_retention_days: int = Field(default=400, ge=30, le=1825)
    snapshot_retention_days: int = Field(default=730, ge=30, le=3650)
    drive_lifecycle_retention_days: int = Field(default=3650, ge=365, le=7300)
    retention_batch_size: int = Field(default=1000, ge=10, le=10000)


def _problem(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "type": f"https://hoardarr.com/problems/{code}",
            "status": status,
            "code": code,
            "detail": detail,
        },
        media_type="application/problem+json",
    )


def _parse_time(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be an ISO 8601 string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include an offset")
    return parsed.astimezone(UTC)


def _ingest_record(
    session: Session, installation_id: str, batch_id: str, record: dict[str, Any]
) -> None:
    record_id = str(uuid.UUID(str(record["id"])))
    existing = session.scalar(
        select(IngestedRecord).where(
            IngestedRecord.installation_id == installation_id,
            IngestedRecord.record_id == record_id,
        )
    )
    if existing is not None:
        if existing.payload_sha256 != record["payload_sha256"]:
            raise ValueError("record identifier was reused with different content")
        return
    payload = record.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("record payload must be an object")
    encoded = canonical_json(payload)
    if len(encoded) > MAX_RECORD_BYTES:
        raise OverflowError("telemetry record is too large")
    digest = hashlib.sha256(encoded).hexdigest()
    if not hmac.compare_digest(digest, str(record.get("payload_sha256", ""))):
        raise ValueError("record payload digest does not match")
    message_type = str(record.get("message_type", ""))
    if message_type not in {"heartbeat", "inventory", "event", "observation"}:
        raise ValueError("unsupported message type")
    level = record.get("telemetry_level")
    if not isinstance(level, int) or level < 0 or level > 3:
        raise ValueError("invalid telemetry level")
    schema_version = record.get("schema_version")
    if schema_version not in SUPPORTED_SCHEMAS:
        raise ValueError("unsupported telemetry schema")
    observed_at = _parse_time(record.get("created_at"))
    session.add(
        IngestedRecord(
            installation_id=installation_id,
            record_id=record_id,
            batch_id=batch_id,
            message_type=message_type,
            telemetry_level=level,
            schema_version=schema_version,
            observed_at=observed_at,
            payload_sha256=digest,
            payload_json=payload,
        )
    )
    _project_record(session, installation_id, message_type, observed_at, payload)


def _project_record(
    session: Session,
    installation_id: str,
    message_type: str,
    observed_at: datetime,
    payload: dict[str, Any],
) -> None:
    if message_type == "heartbeat":
        installation = session.get(Installation, installation_id)
        if installation is not None:
            installation.version = str(payload.get("hoardarr_version", "unknown"))[:64]
            installation.platform_family = str(
                payload.get("platform_family", "unknown")
            )[:64]
        session.add(
            InstallationHeartbeat(
                installation_id=installation_id,
                version=str(payload.get("hoardarr_version", "unknown"))[:64],
                platform_family=str(payload.get("platform_family", "unknown"))[:64],
                observed_at=observed_at,
            )
        )
        session.add(
            VersionObservation(
                installation_id=installation_id,
                version=str(payload.get("hoardarr_version", "unknown"))[:64],
                observed_at=observed_at,
            )
        )
        return
    if message_type == "inventory":
        system = payload.get("system") if isinstance(payload.get("system"), dict) else {}
        session.add(
            HardwareSnapshot(
                installation_id=installation_id,
                observed_at=observed_at,
                system_json=system,
            )
        )
        for item in payload.get("storage_hardware", [])[:512]:
            if not isinstance(item, dict) or not isinstance(item.get("drive_id"), str):
                continue
            drive_id = item["drive_id"][:64]
            drive = session.get(Drive, drive_id)
            if drive is None:
                drive = Drive(
                    drive_id=drive_id,
                    identity_version=int(item.get("drive_identity_version", 1)),
                    first_seen_at=observed_at,
                    last_seen_at=observed_at,
                )
                session.add(drive)
            else:
                drive.last_seen_at = observed_at
            session.add(
                DriveObservation(
                    drive_id=drive_id,
                    installation_id=installation_id,
                    observed_at=observed_at,
                    details_json=item,
                )
            )
        storage = payload.get("storage_configuration")
        if isinstance(storage, dict):
            session.add(
                StorageLayoutObservation(
                    installation_id=installation_id,
                    observed_at=observed_at,
                    details_json=storage,
                )
            )
            session.add(
                CapacityObservation(
                    installation_id=installation_id,
                    observed_at=observed_at,
                    details_json={
                        key: storage[key]
                        for key in (
                            "logical_capacity_bytes",
                            "usable_capacity_bytes",
                            "free_percent",
                        )
                        if key in storage
                    },
                )
            )
        controllers = payload.get("controller_observations")
        if isinstance(controllers, dict):
            session.add(
                ControllerObservation(
                    installation_id=installation_id,
                    observed_at=observed_at,
                    details_json=controllers,
                )
            )
        for product in payload.get("applications_detected", [])[:128]:
            if isinstance(product, str):
                session.add(
                    ApplicationObservation(
                        installation_id=installation_id,
                        product=product[:64],
                        observed_at=observed_at,
                    )
                )
        for feature in payload.get("feature_usage", [])[:128]:
            if isinstance(feature, str):
                session.add(
                    FeatureUsageObservation(
                        installation_id=installation_id,
                        feature=feature[:96],
                        observed_at=observed_at,
                    )
                )
        if payload.get("country_code") or payload.get("timezone"):
            setting = session.get(GeographicSetting, installation_id)
            if setting is None:
                setting = GeographicSetting(
                    installation_id=installation_id, observed_at=observed_at
                )
                session.add(setting)
            setting.country_code = payload.get("country_code")
            setting.timezone = payload.get("timezone")
            setting.observed_at = observed_at
        return
    if message_type == "event":
        event_type = str(payload.get("event_type", "unknown"))[:64]
        drive_id = payload.get("drive_id") if isinstance(payload.get("drive_id"), str) else None
        if event_type.startswith("drive_"):
            session.add(
                DriveLifecycleEvent(
                    drive_id=drive_id,
                    installation_id=installation_id,
                    event_type=event_type,
                    observed_at=observed_at,
                    details_json=payload,
                )
            )
        else:
            session.add(
                FeatureUsageObservation(
                    installation_id=installation_id,
                    feature=event_type,
                    observed_at=observed_at,
                )
            )
        return
    session.add(
        CapacityObservation(
            installation_id=installation_id,
            observed_at=observed_at,
            details_json=payload,
        )
    )


def _bounded_delete(
    session: Session,
    model: type[CentralBase],
    id_column: Any,
    *conditions: Any,
    limit: int,
) -> int:
    identifiers = list(session.scalars(select(id_column).where(*conditions).limit(limit)))
    if identifiers:
        session.execute(delete(model).where(id_column.in_(identifiers)))
    return len(identifiers)


def run_retention(
    session: Session,
    settings: FleetCentralSettings,
    *,
    now: datetime | None = None,
    force: bool = False,
) -> dict[str, int | str]:
    now = now or utc_now()
    state = session.get(CentralMaintenance, "retention")
    if state is not None:
        last_run = state.last_run_at
        if last_run.tzinfo is None:
            last_run = last_run.replace(tzinfo=UTC)
        if not force and now - last_run < timedelta(hours=1):
            return {"status": "not_due", "deleted": 0}
    limit = settings.retention_batch_size
    counts: dict[str, int | str] = {"status": "completed"}
    counts["opt_in_records"] = _bounded_delete(
        session,
        IngestedRecord,
        IngestedRecord.id,
        IngestedRecord.telemetry_level >= 2,
        IngestedRecord.observed_at < now - timedelta(days=settings.opt_in_retention_days),
        limit=limit,
    )
    counts["raw_records"] = _bounded_delete(
        session,
        IngestedRecord,
        IngestedRecord.id,
        IngestedRecord.observed_at < now - timedelta(days=settings.raw_retention_days),
        limit=limit,
    )
    counts["heartbeats"] = _bounded_delete(
        session,
        InstallationHeartbeat,
        InstallationHeartbeat.id,
        InstallationHeartbeat.observed_at < now - timedelta(days=settings.heartbeat_retention_days),
        limit=limit,
    )
    counts["versions"] = _bounded_delete(
        session,
        VersionObservation,
        VersionObservation.id,
        VersionObservation.observed_at < now - timedelta(days=settings.heartbeat_retention_days),
        limit=limit,
    )
    counts["hardware_snapshots"] = _bounded_delete(
        session,
        HardwareSnapshot,
        HardwareSnapshot.id,
        HardwareSnapshot.observed_at < now - timedelta(days=settings.snapshot_retention_days),
        limit=limit,
    )
    counts["category_observations"] = _bounded_delete(
        session,
        CategoryObservation,
        CategoryObservation.id,
        CategoryObservation.observed_at < now - timedelta(days=settings.snapshot_retention_days),
        limit=limit,
    )
    for label, model in (
        ("controller_observations", ControllerObservation),
        ("storage_layout_observations", StorageLayoutObservation),
        ("application_observations", ApplicationObservation),
        ("capacity_observations", CapacityObservation),
        ("feature_usage_observations", FeatureUsageObservation),
    ):
        counts[label] = _bounded_delete(
            session,
            model,
            model.id,
            model.observed_at < now - timedelta(days=settings.snapshot_retention_days),
            limit=limit,
        )
    counts["drive_observations"] = _bounded_delete(
        session,
        DriveObservation,
        DriveObservation.id,
        DriveObservation.observed_at
        < now - timedelta(days=settings.drive_lifecycle_retention_days),
        limit=limit,
    )
    counts["drive_lifecycle_events"] = _bounded_delete(
        session,
        DriveLifecycleEvent,
        DriveLifecycleEvent.id,
        DriveLifecycleEvent.observed_at
        < now - timedelta(days=settings.drive_lifecycle_retention_days),
        limit=limit,
    )
    orphaned_batches = list(
        session.scalars(
            select(IngestedBatch.batch_id)
            .where(
                IngestedBatch.received_at < now - timedelta(days=settings.raw_retention_days),
                ~exists(
                    select(IngestedRecord.id).where(
                        IngestedRecord.batch_id == IngestedBatch.batch_id
                    )
                ),
            )
            .limit(limit)
        )
    )
    if orphaned_batches:
        session.execute(delete(IngestedBatch).where(IngestedBatch.batch_id.in_(orphaned_batches)))
    counts["batches"] = len(orphaned_batches)
    counts["deleted"] = sum(value for value in counts.values() if isinstance(value, int))
    if state is None:
        state = CentralMaintenance(id="retention", last_run_at=now, details_json={})
        session.add(state)
    state.last_run_at = now
    state.details_json = counts
    return counts


def create_central_app(settings: FleetCentralSettings) -> FastAPI:
    connect_args = (
        {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
    )
    engine = create_engine(settings.database_url, connect_args=connect_args, pool_pre_ping=True)
    if not central_database_is_current(engine, settings.database_url):
        engine.dispose()
        raise RuntimeError(
            "fleet database migrations are not current; run hoardarr-fleet-ingestion migrate"
        )
    sessions = sessionmaker(engine, expire_on_commit=False)
    secret_box = SecretBox.from_file(settings.secret_key_file, create=True)
    app = FastAPI(title="Hoardarr Fleet Ingestion", version="1.0")

    @app.post("/api/telemetry/v1/register", status_code=201)
    async def register(request: Request) -> JSONResponse:
        raw = await request.body()
        if len(raw) > 16 * 1024:
            return _problem(413, "body_too_large", "Registration body exceeds the allowed size.")
        try:
            body = Registration.model_validate_json(raw)
        except ValidationError:
            return _problem(422, "invalid_registration", "Registration payload is invalid.")
        installation_id = str(body.installation_id)
        with sessions() as session, session.begin():
            if session.get(Installation, installation_id) is not None:
                return _problem(409, "already_registered", "Installation is already registered.")
            credential = secrets.token_urlsafe(48)
            session.add(
                Installation(
                    installation_id=installation_id,
                    credential_ciphertext=secret_box.encrypt(
                        "fleet-central-credential", installation_id, credential
                    ),
                    credential_fingerprint=secret_box.fingerprint(
                        "fleet-central-credential", credential
                    )[:16],
                    schema_version=body.schema_version,
                    version=body.hoardarr_version,
                    platform_family=body.platform_family,
                    last_seen_at=body.heartbeat_at.astimezone(UTC),
                )
            )
        return JSONResponse(
            status_code=201, content={"credential": credential, "schema_version": 1}
        )

    @app.post("/api/telemetry/v1/batch")
    async def batch(
        request: Request, x_hoardarr_signature: str | None = Header(default=None)
    ) -> JSONResponse:
        raw = await request.body()
        if len(raw) > MAX_BODY_BYTES:
            return _problem(413, "body_too_large", "Telemetry batch exceeds the allowed size.")
        try:
            body = json.loads(raw)
            installation_id = str(uuid.UUID(str(body["installation_id"])))
            batch_id = str(uuid.UUID(str(body["batch_id"])))
            schema_version = body["schema_version"]
            sequence = body["sequence_number"]
            records = body["records"]
            _parse_time(body["created_at"])
            if schema_version not in SUPPORTED_SCHEMAS:
                return _problem(422, "unsupported_schema", "Telemetry schema is not supported.")
            if not isinstance(sequence, int) or sequence < 1:
                raise ValueError("invalid sequence")
            if not isinstance(records, list) or not records or len(records) > MAX_BATCH_RECORDS:
                raise ValueError("invalid record count")
            digest = hashlib.sha256(canonical_json(records)).hexdigest()
            if not hmac.compare_digest(digest, str(body["payload_digest"])):
                return _problem(422, "digest_mismatch", "Batch payload digest does not match.")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return _problem(422, "invalid_batch", "Telemetry batch is malformed.")
        with sessions() as session, session.begin():
            installation = session.get(Installation, installation_id)
            if installation is None:
                return _problem(401, "unknown_installation", "Installation is not registered.")
            credential = secret_box.decrypt(
                "fleet-central-credential", installation_id, installation.credential_ciphertext
            )
            expected = "v1=" + hmac.new(credential.encode(), raw, hashlib.sha256).hexdigest()
            if x_hoardarr_signature is None or not hmac.compare_digest(
                expected, x_hoardarr_signature
            ):
                return _problem(401, "invalid_signature", "Telemetry authentication failed.")
            if (
                session.get(IngestedBatch, batch_id) is not None
                or sequence <= installation.last_sequence_number
            ):
                return _problem(409, "replayed_batch", "Telemetry batch was already processed.")
            session.add(
                IngestedBatch(
                    batch_id=batch_id,
                    installation_id=installation_id,
                    sequence_number=sequence,
                    payload_digest=digest,
                )
            )
            try:
                for record in records:
                    if not isinstance(record, dict):
                        raise ValueError("record must be an object")
                    _ingest_record(session, installation_id, batch_id, record)
            except OverflowError:
                session.rollback()
                return _problem(
                    413, "record_too_large", "Telemetry record exceeds the allowed size."
                )
            except (KeyError, TypeError, ValueError):
                session.rollback()
                return _problem(422, "invalid_record", "Telemetry record is invalid.")
            installation.last_sequence_number = sequence
            installation.last_seen_at = utc_now()
            run_retention(session, settings)
            acknowledged = [str(record["id"]) for record in records]
        return JSONResponse({"acknowledged_record_ids": acknowledged, "batch_id": batch_id})

    @app.get("/api/admin/v1/fleet/summary")
    def summary(x_hoardarr_admin_token: str | None = Header(default=None)) -> JSONResponse:
        if settings.admin_token is None or not hmac.compare_digest(
            x_hoardarr_admin_token or "", settings.admin_token
        ):
            return _problem(
                401, "admin_authentication_required", "Admin authentication is required."
            )
        cutoff = utc_now() - timedelta(days=settings.active_window_days)
        with sessions() as session:
            active = session.scalar(
                select(func.count())
                .select_from(Installation)
                .where(Installation.last_seen_at >= cutoff)
            )
            versions = session.execute(
                select(Installation.version, func.count()).group_by(Installation.version)
            ).all()
            countries = session.execute(
                select(GeographicSetting.country_code, func.count())
                .where(GeographicSetting.country_code.is_not(None))
                .group_by(GeographicSetting.country_code)
                .order_by(GeographicSetting.country_code)
            ).all()
            timezones = session.execute(
                select(GeographicSetting.timezone, func.count())
                .where(GeographicSetting.timezone.is_not(None))
                .group_by(GeographicSetting.timezone)
                .order_by(GeographicSetting.timezone)
            ).all()
            applications = session.execute(
                select(
                    ApplicationObservation.product,
                    func.count(func.distinct(ApplicationObservation.installation_id)),
                )
                .where(ApplicationObservation.observed_at >= cutoff)
                .group_by(ApplicationObservation.product)
                .order_by(ApplicationObservation.product)
            ).all()
            features = session.execute(
                select(
                    FeatureUsageObservation.feature,
                    func.count(func.distinct(FeatureUsageObservation.installation_id)),
                )
                .where(FeatureUsageObservation.observed_at >= cutoff)
                .group_by(FeatureUsageObservation.feature)
                .order_by(FeatureUsageObservation.feature)
            ).all()
            cross_system = session.scalar(
                select(func.count()).select_from(
                    select(DriveObservation.drive_id)
                    .group_by(DriveObservation.drive_id)
                    .having(func.count(func.distinct(DriveObservation.installation_id)) > 1)
                    .subquery()
                )
            )
            maintenance = session.get(CentralMaintenance, "retention")
            recent_system = session.scalars(
                select(HardwareSnapshot).order_by(HardwareSnapshot.observed_at.desc()).limit(10_000)
            ).all()
            systems_by_installation: dict[str, dict[str, Any]] = {}
            for observation in recent_system:
                systems_by_installation.setdefault(
                    observation.installation_id, observation.system_json
                )
            recent_drives = session.scalars(
                select(DriveObservation).order_by(DriveObservation.observed_at.desc()).limit(50_000)
            ).all()
            drives_by_id: dict[str, dict[str, Any]] = {}
            for observation in recent_drives:
                drives_by_id.setdefault(observation.drive_id, observation.details_json)
            recent_layouts = session.scalars(
                select(StorageLayoutObservation)
                .order_by(StorageLayoutObservation.observed_at.desc())
                .limit(10_000)
            ).all()
            layouts_by_installation: dict[str, dict[str, Any]] = {}
            for observation in recent_layouts:
                layouts_by_installation.setdefault(
                    observation.installation_id, observation.details_json
                )
            recent_capacities = session.scalars(
                select(CapacityObservation)
                .order_by(CapacityObservation.observed_at.desc())
                .limit(10_000)
            ).all()
            capacities_by_installation: dict[str, dict[str, Any]] = {}
            for observation in recent_capacities:
                capacities_by_installation.setdefault(
                    observation.installation_id, observation.details_json
                )
            recent_controllers = session.scalars(
                select(ControllerObservation)
                .order_by(ControllerObservation.observed_at.desc())
                .limit(10_000)
            ).all()
            controllers_by_installation: dict[str, dict[str, Any]] = {}
            for observation in recent_controllers:
                controllers_by_installation.setdefault(
                    observation.installation_id, observation.details_json
                )
            application_rows = session.execute(
                select(ApplicationObservation.installation_id, ApplicationObservation.product)
                .where(ApplicationObservation.observed_at >= cutoff)
                .order_by(ApplicationObservation.installation_id, ApplicationObservation.product)
                .limit(100_000)
            ).all()
            version_rows = session.execute(
                select(
                    VersionObservation.installation_id,
                    VersionObservation.version,
                    VersionObservation.observed_at,
                )
                .order_by(
                    VersionObservation.installation_id,
                    VersionObservation.observed_at,
                    VersionObservation.id,
                )
                .limit(100_000)
            ).all()

        def distribution(values: list[str | None]) -> list[dict[str, int | str]]:
            counts: dict[str, int] = {}
            for value in values:
                label = value or "not_reported"
                counts[label] = counts.get(label, 0) + 1
            return [
                {"value": value, "count": count}
                for value, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
            ]

        def byte_distribution(
            values: list[int | None], boundaries: tuple[tuple[int, str], ...]
        ) -> list[dict[str, int | str]]:
            labels: list[str] = []
            for value in values:
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    labels.append("not_reported")
                    continue
                label = boundaries[-1][1]
                for upper, candidate in boundaries:
                    if value < upper:
                        label = candidate
                        break
                labels.append(label)
            return distribution(labels)

        application_sets: dict[str, set[str]] = {}
        for installation_id, product in application_rows:
            application_sets.setdefault(installation_id, set()).add(str(product))
        application_combinations = distribution(
            [" + ".join(sorted(products)) for products in application_sets.values()]
        )

        observed_versions: dict[str, list[str]] = {}
        for installation_id, version, _observed_at in version_rows:
            sequence = observed_versions.setdefault(installation_id, [])
            if not sequence or sequence[-1] != version:
                sequence.append(str(version))
        transition_counts: dict[str, int] = {}
        for sequence in observed_versions.values():
            for previous, current in pairwise(sequence):
                transition = f"{previous} → {current}"
                transition_counts[transition] = transition_counts.get(transition, 0) + 1

        gib = 1024**3
        tib = 1024**4
        ram_boundaries = (
            (4 * gib, "under_4_GiB"),
            (8 * gib, "4_to_8_GiB"),
            (16 * gib, "8_to_16_GiB"),
            (32 * gib, "16_to_32_GiB"),
            (64 * gib, "32_to_64_GiB"),
            (128 * gib, "64_to_128_GiB"),
            (2**63, "128_GiB_or_more"),
        )
        capacity_boundaries = (
            (1 * tib, "under_1_TiB"),
            (4 * tib, "1_to_4_TiB"),
            (8 * tib, "4_to_8_TiB"),
            (16 * tib, "8_to_16_TiB"),
            (64 * tib, "16_to_64_TiB"),
            (2**63, "64_TiB_or_more"),
        )

        drive_values = list(drives_by_id.values())
        system_values = list(systems_by_installation.values())
        layout_values = list(layouts_by_installation.values())
        return JSONResponse(
            {
                "active_installations": active or 0,
                "versions": [
                    {"version": version, "installations": count} for version, count in versions
                ],
                "drives_seen_in_multiple_installations": cross_system or 0,
                "active_window_days": settings.active_window_days,
                "countries": [
                    {"country_code": country, "installations": count}
                    for country, count in countries
                ],
                "timezones": [
                    {"timezone": timezone, "installations": count} for timezone, count in timezones
                ],
                "hardware": {
                    "cpu_vendors": distribution(
                        [
                            str(item.get("cpu_vendor")) if item.get("cpu_vendor") else None
                            for item in system_values
                        ]
                    ),
                    "cpu_models": distribution(
                        [
                            str(item.get("cpu_model")) if item.get("cpu_model") else None
                            for item in system_values
                        ]
                    ),
                    "platform_models": distribution(
                        [
                            str(item.get("platform_model")) if item.get("platform_model") else None
                            for item in system_values
                        ]
                    ),
                    "platform_vendors": distribution(
                        [
                            str(item.get("platform_vendor"))
                            if item.get("platform_vendor")
                            else None
                            for item in system_values
                        ]
                    ),
                    "installed_memory": byte_distribution(
                        [
                            item.get("installed_memory_bytes")
                            if isinstance(item.get("installed_memory_bytes"), int)
                            else None
                            for item in system_values
                        ],
                        ram_boundaries,
                    ),
                    "controllers": distribution(
                        [
                            str(model)
                            for item in controllers_by_installation.values()
                            for model in item.get("models", [])
                            if isinstance(model, str)
                        ]
                    ),
                    "enclosures": distribution(
                        [
                            str(item.get("enclosure_model"))
                            if item.get("enclosure_model")
                            else None
                            for item in drive_values
                        ]
                    ),
                    "sampled_installations": len(system_values),
                },
                "drives": {
                    "vendors": distribution(
                        [
                            str(item.get("vendor")) if item.get("vendor") else None
                            for item in drive_values
                        ]
                    ),
                    "models": distribution(
                        [
                            str(item.get("model")) if item.get("model") else None
                            for item in drive_values
                        ]
                    ),
                    "media_types": distribution(
                        [
                            str(item.get("media_type")) if item.get("media_type") else None
                            for item in drive_values
                        ]
                    ),
                    "health": distribution(
                        [
                            str(item.get("health")) if item.get("health") else None
                            for item in drive_values
                        ]
                    ),
                    "capacities": byte_distribution(
                        [
                            item.get("capacity_bytes")
                            if isinstance(item.get("capacity_bytes"), int)
                            else None
                            for item in drive_values
                        ],
                        capacity_boundaries,
                    ),
                    "sampled_drives": len(drive_values),
                },
                "storage": {
                    "backend_types": distribution(
                        [
                            str(value)
                            for layout in layout_values
                            for value in layout.get("backend_types", [])
                            if isinstance(value, str)
                        ]
                    ),
                    "purposes": distribution(
                        [
                            str(value)
                            for layout in layout_values
                            for value in layout.get("purposes", [])
                            if isinstance(value, str)
                        ]
                    ),
                    "sampled_installations": len(layout_values),
                    "controller_redundancy_installations": sum(
                        1
                        for item in layout_values
                        if isinstance(item.get("controller_redundant_count"), int)
                        and item["controller_redundant_count"] > 0
                    ),
                    "logical_capacity": byte_distribution(
                        [
                            item.get("logical_capacity_bytes")
                            if isinstance(item.get("logical_capacity_bytes"), int)
                            else None
                            for item in capacities_by_installation.values()
                        ],
                        capacity_boundaries,
                    ),
                    "free_space_percent": distribution(
                        [
                            "under_10_percent"
                            if value < 10
                            else "10_to_25_percent"
                            if value < 25
                            else "25_to_50_percent"
                            if value < 50
                            else "50_percent_or_more"
                            for item in capacities_by_installation.values()
                            if isinstance((value := item.get("free_percent")), (int, float))
                            and not isinstance(value, bool)
                        ]
                    ),
                },
                "applications": [
                    {"product": product, "installations": count} for product, count in applications
                ],
                "application_combinations": application_combinations,
                "upgrade_adoption": {
                    "installations_with_observed_upgrade": sum(
                        1 for sequence in observed_versions.values() if len(sequence) > 1
                    ),
                    "sampled_installations": len(observed_versions),
                    "transitions": [
                        {"transition": transition, "installations": count}
                        for transition, count in sorted(
                            transition_counts.items(), key=lambda item: (-item[1], item[0])
                        )
                    ],
                },
                "feature_usage": [
                    {"feature": feature, "installations": count} for feature, count in features
                ],
                "query_limits": {
                    "hardware_observations": 10_000,
                    "drive_observations": 50_000,
                    "storage_observations": 10_000,
                    "capacity_observations": 10_000,
                    "controller_observations": 10_000,
                    "application_observations": 100_000,
                    "version_observations": 100_000,
                },
                "methodology": (
                    "Latest retained observation per installation or drive; observed in Hoardarr "
                    "installations, not manufacturer failure-rate evidence."
                ),
                "last_retention_run": maintenance.last_run_at.isoformat()
                if maintenance is not None
                else None,
            }
        )

    @app.get("/healthz")
    def health() -> dict[str, str]:
        with sessions() as session:
            session.execute(select(1))
        return {"status": "healthy"}

    app.state.engine = engine
    app.state.sessions = sessions
    return app
