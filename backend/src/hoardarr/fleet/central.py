from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
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
    func,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from hoardarr.core.secrets import SecretBox
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
        categories = {
            "storage_layout": payload.get("storage_configuration"),
            "application": payload.get("applications_detected"),
            "capacity": payload.get("capacity"),
            "feature_usage": payload.get("feature_usage"),
        }
        for kind, details in categories.items():
            if details is not None:
                session.add(
                    CategoryObservation(
                        installation_id=installation_id,
                        kind=kind,
                        observed_at=observed_at,
                        details_json={"value": details},
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
                CategoryObservation(
                    installation_id=installation_id,
                    kind="feature_usage",
                    observed_at=observed_at,
                    details_json=payload,
                )
            )
        return
    session.add(
        CategoryObservation(
            installation_id=installation_id,
            kind="capacity",
            observed_at=observed_at,
            details_json=payload,
        )
    )


def create_central_app(settings: FleetCentralSettings) -> FastAPI:
    connect_args = (
        {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
    )
    engine = create_engine(settings.database_url, connect_args=connect_args, pool_pre_ping=True)
    CentralBase.metadata.create_all(engine)
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
            cross_system = session.scalar(
                select(func.count()).select_from(
                    select(DriveObservation.drive_id)
                    .group_by(DriveObservation.drive_id)
                    .having(func.count(func.distinct(DriveObservation.installation_id)) > 1)
                    .subquery()
                )
            )
        return JSONResponse(
            {
                "active_installations": active or 0,
                "versions": [
                    {"version": version, "installations": count} for version, count in versions
                ],
                "drives_seen_in_multiple_installations": cross_system or 0,
                "active_window_days": settings.active_window_days,
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
