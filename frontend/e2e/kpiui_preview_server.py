"""Run an isolated KPI quality preview through the production API and frontend.

This helper is validation-only.  It creates a temporary SQLite database, marks
every seeded entity as deterministic test evidence, and never reads or writes a
storage device.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import uvicorn
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from hoardarr.api.app import create_app
from hoardarr.auth.service import issue_setup_token
from hoardarr.core.config import Settings
from hoardarr.db.engine import create_database_engine, create_session_factory
from hoardarr.db.migrate import upgrade_database
from hoardarr.db.models import MetricEntity, MetricRollup
from hoardarr.telemetry.entitlements import canonical_json, installation_id
from hoardarr.telemetry.samples import EntityReading, MetricReading
from hoardarr.telemetry.store import ingest
from sqlalchemy import select


def _install_test_license(settings: Settings, now: datetime) -> None:
    private_key = Ed25519PrivateKey.generate()
    settings.telemetry_license_trust_file.write_text(
        json.dumps(
            {
                "keys": {
                    "kpiui-preview": base64.b64encode(
                        private_key.public_key().public_bytes_raw()
                    ).decode()
                }
            }
        ),
        encoding="utf-8",
    )
    payload = {
        "license_id": "deterministic-kpiui-preview",
        "installation_id": installation_id(settings.installation_identity_file),
        "not_before": (now - timedelta(hours=1)).isoformat(),
        "expires_at": (now + timedelta(hours=6)).isoformat(),
        "capabilities": ["metrics.history.extended"],
    }
    settings.telemetry_license_file.write_text(
        json.dumps(
            {
                "payload": payload,
                "key_id": "kpiui-preview",
                "signature": base64.b64encode(
                    private_key.sign(canonical_json(payload))
                ).decode(),
            }
        ),
        encoding="utf-8",
    )


def _seed(session_factory: object, now: datetime) -> str:
    entity = EntityReading(
        "drive",
        "fixture:kpi-quality-evidence",
        "KPI QUALITY TEST DATA — TEST SSD",
        labels={"evidence_scope": "deterministic_test_fixture"},
    )
    readings = [
        MetricReading(
            entity,
            "io.read.bytes_per_second",
            now,
            1_048_576,
            "available",
            "Deterministic KPI test provider",
            300,
        ),
        MetricReading(
            entity,
            "io.write.bytes_per_second",
            now,
            None,
            "not_reported",
            "Deterministic KPI test provider",
            300,
        ),
        MetricReading(
            entity,
            "io.read.iops",
            now,
            None,
            "unsupported",
            "Deterministic KPI test provider",
            300,
        ),
        MetricReading(
            entity,
            "io.write.iops",
            now,
            None,
            "temporarily_unavailable",
            "Deterministic KPI test provider",
            300,
            error_code="provider_timeout",
        ),
        MetricReading(
            entity,
            "io.read.latency",
            now,
            12,
            "stale",
            "Deterministic KPI test provider",
            300,
        ),
        MetricReading(
            entity,
            "io.write.latency",
            now,
            4.5,
            "estimated",
            "Deterministic KPI test provider",
            300,
        ),
        MetricReading(
            entity,
            "io.queue.depth",
            now,
            6,
            "derived",
            "Deterministic KPI test provider",
            300,
            labels={
                "methodology": (
                    "read operations plus write operations divided by completed sampling intervals"
                )
            },
        ),
        MetricReading(
            entity,
            "health.overall",
            now,
            "healthy",
            "available",
            "Deterministic KPI test provider",
            300,
        ),
    ]
    with session_factory() as session, session.begin():  # type: ignore[operator]
        ingest(session, readings)
        metric_entity = session.scalar(
            select(MetricEntity).where(
                MetricEntity.stable_id == "fixture:kpi-quality-evidence"
            )
        )
        assert metric_entity is not None
        first_hour = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)
        second_hour = first_hour + timedelta(hours=1)
        session.add_all(
            [
                MetricRollup(
                    entity_id=metric_entity.id,
                    metric_id="io.read.bytes_per_second",
                    resolution="hour",
                    period_start=first_hour,
                    sample_count=12,
                    minimum=250_000,
                    maximum=8_000_000,
                    mean=2_000_000,
                    first=1_000_000,
                    last=3_000_000,
                    transition_count=0,
                    states_json=[],
                    quality="available",
                ),
                MetricRollup(
                    entity_id=metric_entity.id,
                    metric_id="io.read.bytes_per_second",
                    resolution="hour",
                    period_start=second_hour,
                    sample_count=0,
                    minimum=None,
                    maximum=None,
                    mean=None,
                    first=None,
                    last=None,
                    transition_count=0,
                    states_json=[],
                    quality="temporarily_unavailable",
                ),
                MetricRollup(
                    entity_id=metric_entity.id,
                    metric_id="health.overall",
                    resolution="hour",
                    period_start=first_hour,
                    sample_count=3,
                    minimum=None,
                    maximum=None,
                    mean=None,
                    first=None,
                    last=None,
                    first_text="healthy",
                    last_text="healthy",
                    transition_count=2,
                    states_json=["healthy", "degraded", "healthy"],
                    quality="available",
                ),
                MetricRollup(
                    entity_id=metric_entity.id,
                    metric_id="health.overall",
                    resolution="hour",
                    period_start=second_hour,
                    sample_count=1,
                    minimum=None,
                    maximum=None,
                    mean=None,
                    first=None,
                    last=None,
                    first_text="healthy",
                    last_text="healthy",
                    transition_count=0,
                    states_json=["healthy"],
                    quality="available",
                ),
            ]
        )
        token = issue_setup_token(session, ttl_seconds=3_600)
    return token


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=7877)
    parser.add_argument("--telemetry-delay-ms", type=int, default=0)
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[2]
    state = Path(tempfile.mkdtemp(prefix="hoardarr-kpiui-preview-"))
    settings = Settings(
        environment="test",
        database_url=f"sqlite:///{(state / 'preview.db').as_posix()}",
        secret_key_file=state / "secret.key",
        frontend_dir=repository / "frontend" / "dist",
        bind_host="127.0.0.1",
        bind_port=args.port,
        secure_cookies=False,
        allowed_origins=(f"http://127.0.0.1:{args.port}",),
        telemetry_license_file=state / "license.json",
        telemetry_license_trust_file=state / "license-trust.json",
        installation_identity_file=state / "machine-id",
    )
    settings.installation_identity_file.write_text(
        "kpiui-deterministic-preview\n", encoding="utf-8"
    )
    upgrade_database(settings.database_url)
    engine = create_database_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    now = datetime.now(UTC).replace(microsecond=0)
    _install_test_license(settings, now)
    token = _seed(session_factory, now)
    engine.dispose()
    print(f"PREVIEW_URL=http://127.0.0.1:{args.port}/#pair={token}", flush=True)
    print(f"PREVIEW_STATE={state}", flush=True)
    app = create_app(settings)
    if args.telemetry_delay_ms > 0:

        @app.middleware("http")
        async def delay_telemetry(request, call_next):  # type: ignore[no-untyped-def]
            if request.url.path.startswith("/api/v1/telemetry"):
                await asyncio.sleep(args.telemetry_delay_ms / 1_000)
            return await call_next(request)

    uvicorn.run(app, host=settings.bind_host, port=settings.bind_port)


if __name__ == "__main__":
    main()
