from __future__ import annotations

import base64
import json
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select

from hoardarr.api.app import create_app
from hoardarr.api.routes import telemetry as telemetry_routes
from hoardarr.auth.service import issue_setup_token
from hoardarr.core.config import Settings
from hoardarr.db.engine import create_database_engine, create_session_factory
from hoardarr.db.migrate import upgrade_database
from hoardarr.db.models import (
    MetricAlert,
    MetricAlertRule,
    MetricEntity,
    MetricRollup,
    MetricSample,
    Operation,
    StorageEntity,
)
from hoardarr.storage.redundancy import register_single_path_storage
from hoardarr.telemetry.alerts import evaluate_basic_alerts
from hoardarr.telemetry.analytics import (
    anomaly,
    capacity_forecast,
    correlate,
    endurance_forecast,
    nearest_rank,
)
from hoardarr.telemetry.catalog import CATALOG_BY_ID, METRICS, catalog_document
from hoardarr.telemetry.collectors import (
    ResetSafeCounterRates,
    _reading,
    _weighted_io_time,
    mergerfs_imbalance,
)
from hoardarr.telemetry.entitlements import (
    EntitlementService,
    canonical_json,
    installation_id,
)
from hoardarr.telemetry.samples import EntityReading, MetricReading
from hoardarr.telemetry.service import TelemetryService, collect_for_worker
from hoardarr.telemetry.store import (
    apply_retention,
    build_rollups,
    current_samples,
    history,
    ingest,
)


def runtime(tmp_path: Path):  # type: ignore[no-untyped-def]
    settings = Settings(
        environment="test",
        database_url=f"sqlite:///{(tmp_path / 'telemetry.db').as_posix()}",
        secret_key_file=tmp_path / "secret.key",
        secure_cookies=False,
        telemetry_license_file=tmp_path / "license.json",
        telemetry_license_trust_file=tmp_path / "trust.json",
        installation_identity_file=tmp_path / "machine-id",
    )
    settings.installation_identity_file.write_text("test-machine\n", encoding="utf-8")
    upgrade_database(settings.database_url)
    engine = create_database_engine(settings.database_url)
    return settings, engine, create_session_factory(engine)


def reading(
    metric_id: str,
    value: float | str | None,
    timestamp: datetime,
    *,
    quality: str = "available",
    entity_type: str = "drive",
    stable_id: str = "wwn:test",
) -> MetricReading:
    return MetricReading(
        entity=EntityReading(entity_type, stable_id, "Test drive"),
        metric_id=metric_id,
        observed_at=timestamp,
        value=value,
        quality=quality,  # type: ignore[arg-type]
        source="test fixture",
        collection_interval_seconds=5,
    )


def install_license(
    settings: Settings,
    private_key: Ed25519PrivateKey,
    capabilities: list[str],
    *,
    now: datetime,
    installation: str | None = None,
    corrupt: bool = False,
    starts: datetime | None = None,
    expires: datetime | None = None,
) -> None:
    public = private_key.public_key().public_bytes_raw()
    settings.telemetry_license_trust_file.write_text(
        json.dumps({"keys": {"test": base64.b64encode(public).decode()}}), encoding="utf-8"
    )
    payload = {
        "license_id": "test-license",
        "installation_id": installation or installation_id(settings.installation_identity_file),
        "not_before": (starts or now - timedelta(days=1)).isoformat(),
        "expires_at": (expires or now + timedelta(days=30)).isoformat(),
        "capabilities": capabilities,
    }
    signature = private_key.sign(canonical_json(payload))
    if corrupt:
        signature = b"x" * len(signature)
    settings.telemetry_license_file.write_text(
        json.dumps(
            {
                "payload": payload,
                "key_id": "test",
                "signature": base64.b64encode(signature).decode(),
            }
        ),
        encoding="utf-8",
    )


def claim(client: TestClient, token: str) -> str:
    response = client.post(
        "/api/v1/setup/claim",
        headers={"Origin": "http://testserver"},
        json={"token": token, "username": "owner", "password": "test"},
    )
    assert response.status_code == 201
    return str(response.json()["csrf_token"])


def test_catalog_has_unique_complete_machine_readable_rows() -> None:
    assert len(METRICS) >= 75
    assert len(METRICS) == len(CATALOG_BY_ID)
    for metric in METRICS:
        document = metric.document()
        assert set(document) == {
            "id",
            "name",
            "entity_types",
            "unit",
            "kind",
            "source",
            "minimum_interval_seconds",
            "capability",
            "retention_class",
            "aggregation",
            "availability",
            "formula",
            "test_evidence",
            "implementation_status",
            "physical_validation",
        }
        assert document["implementation_status"] in {
            "PRODUCTION IMPLEMENTED + SOFTWARE VERIFIED",
            "SUPPORTED WHEN PROVIDER REPORTS VALUE",
        }
        assert document["physical_validation"] in {"pending", "not_required"}


def test_checked_in_metric_catalog_matches_production_catalog() -> None:
    path = Path(__file__).parents[2] / "docs" / "telemetry" / "metric-catalog.json"
    checked_in = json.loads(path.read_text(encoding="utf-8"))
    production = json.loads(json.dumps(catalog_document()))
    assert checked_in["schema_version"] == 1
    assert checked_in["metrics"] == production


def test_sample_quality_never_turns_missing_into_zero() -> None:
    now = datetime.now(UTC)
    unavailable = reading("drive.temperature", None, now, quality="not_reported")
    assert unavailable.value is None
    with pytest.raises(ValueError, match="cannot include a value"):
        reading("drive.temperature", 0, now, quality="unsupported")
    with pytest.raises(ValueError, match="cannot be negative"):
        reading("drive.media_errors", -1, now)
    with pytest.raises(ValueError, match="timezone"):
        reading("drive.temperature", 20, datetime.now())


def test_counter_rates_handle_reset_duplicate_clock_and_identity_change() -> None:
    tracker = ResetSafeCounterRates(maximum_elapsed_seconds=30)
    assert (
        tracker.update("sda", identity="wwn:one", timestamp=10, counters={"bytes": 100})["bytes"]
        is None
    )
    assert (
        tracker.update("sda", identity="wwn:one", timestamp=20, counters={"bytes": 300})["bytes"]
        == 20
    )
    assert (
        tracker.update("sda", identity="wwn:one", timestamp=20, counters={"bytes": 400})["bytes"]
        is None
    )
    assert (
        tracker.update("sda", identity="wwn:one", timestamp=21, counters={"bytes": 10})["bytes"]
        is None
    )
    assert (
        tracker.update("sda", identity="wwn:two", timestamp=22, counters={"bytes": 1_000})["bytes"]
        is None
    )
    assert (
        tracker.update("sda", identity="wwn:two", timestamp=100, counters={"bytes": 2_000})["bytes"]
        is None
    )


def test_weighted_io_time_and_mergerfs_imbalance_are_source_backed(tmp_path: Path) -> None:
    diskstats = tmp_path / "diskstats"
    diskstats.write_text("8 0 sda 1 0 2 3 4 0 5 6 0 7 812\n", encoding="utf-8")
    assert _weighted_io_time("sda", diskstats) == 812
    assert _weighted_io_time("sdb", diskstats) is None

    class Facts:
        f_blocks = 100
        f_frsize = 1
        f_bavail = 0

    values = iter((80, 50, 20))

    def fake_statvfs(_path: str) -> Facts:
        result = Facts()
        result.f_bavail = next(values)
        return result

    imbalance, members = mergerfs_imbalance(["/one", "/two", "/three"], statvfs=fake_statvfs)
    assert imbalance == 60.0
    assert len(members) == 3


def test_read_ratio_and_multipath_failovers_use_durable_exact_transitions(tmp_path: Path) -> None:
    settings, engine, factory = runtime(tmp_path)
    service = TelemetryService(settings)
    now = datetime.now(UTC)
    drive = EntityReading("drive", "wwn:ratio", "Drive")
    path = EntityReading("multipath_device", "multipath:3600", "data")

    def batch(group: str) -> list[MetricReading]:
        return [
            _reading(
                drive,
                "io.read.bytes_per_second",
                25,
                observed_at=now,
                source="test",
                interval=5,
            ),
            _reading(
                drive,
                "io.write.bytes_per_second",
                75,
                observed_at=now,
                source="test",
                interval=5,
            ),
            _reading(
                path,
                "multipath.paths.active",
                1,
                observed_at=now,
                source="test",
                interval=30,
                labels={"active_group": group},
            ),
        ]

    with factory() as session, session.begin():
        first = service._derived_readings(session, batch("a"), now, 5)
        second = service._derived_readings(session, batch("a"), now, 5)
        third = service._derived_readings(session, batch("b"), now, 5)
        assert next(item for item in first if item.metric_id.endswith("read_ratio")).value == 0.25
        assert next(item for item in first if item.metric_id == "multipath.failovers").value == 0
        assert next(item for item in second if item.metric_id == "multipath.failovers").value == 0
        assert next(item for item in third if item.metric_id == "multipath.failovers").value == 1
    service.executor.shutdown(wait=True, cancel_futures=True)
    engine.dispose()


def test_logical_storage_history_and_today_counter_survive_path_transition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, engine, factory = runtime(tmp_path)
    service = TelemetryService(settings)
    now = datetime.now(UTC)
    path = {
        "kernel_path": "/dev/sdb",
        "capacity_bytes": 8_000_000_000,
        "identity": {"wwn": "naa.600a098000history"},
        "sector_sizes": {"logical_bytes": 512, "physical_bytes": 4096},
        "connection": {
            "protocol": "iscsi",
            "controller_address": "portal-a",
            "target_port_wwn": "target-a",
        },
    }

    class Filesystem:
        total = 409_600
        used = 163_840
        free = 245_760

    monkeypatch.setattr("hoardarr.telemetry.service.shutil.disk_usage", lambda _path: Filesystem())
    monkeypatch.setattr(
        "hoardarr.telemetry.service.os.path.realpath",
        lambda value: "/dev/dm-0" if value.startswith("/dev/mapper/") else value,
    )
    with factory() as session, session.begin():
        storage = register_single_path_storage(
            session,
            name="MediaPool",
            device=path,
            mountpoint="/media",
            presentation_device="/dev/sdb",
            filesystem_uuid="11111111-1111-4111-8111-111111111111",
        )
        direct = EntityReading("drive", "wwn:path-a", "Path A", labels={"device": "sdb"})
        first = service._logical_storage_readings(
            session,
            [
                _reading(
                    direct,
                    "io.write.today",
                    100,
                    observed_at=now,
                    source="test diskstats",
                    interval=5,
                ),
                _reading(
                    direct,
                    "io.write.bytes_per_second",
                    20,
                    observed_at=now,
                    source="test diskstats",
                    interval=5,
                ),
            ],
            now,
        )
        storage.presentation_device = "/dev/mapper/3600a098000history"
        mapped = EntityReading("drive", "wwn:map", "Map", labels={"device": "dm-0"})
        second = service._logical_storage_readings(
            session,
            [
                _reading(
                    mapped,
                    "io.write.today",
                    20,
                    observed_at=now + timedelta(seconds=5),
                    source="test diskstats",
                    interval=5,
                ),
                _reading(
                    mapped,
                    "io.write.bytes_per_second",
                    40,
                    observed_at=now + timedelta(seconds=5),
                    source="test diskstats",
                    interval=5,
                ),
            ],
            now + timedelta(seconds=5),
        )
        first_today = next(item for item in first if item.metric_id == "io.write.today")
        second_today = next(item for item in second if item.metric_id == "io.write.today")
        assert first_today.entity.stable_id == second_today.entity.stable_id
        assert first_today.value == 100
        assert second_today.value == 120
        assert session.scalar(select(StorageEntity)).id == storage.id  # type: ignore[union-attr]
    service.executor.shutdown(wait=True, cancel_futures=True)
    engine.dispose()


def test_slow_provider_has_one_bounded_inflight_task(tmp_path: Path) -> None:
    settings, engine, _factory = runtime(tmp_path)
    service = TelemetryService(settings)
    release = threading.Event()
    calls = 0

    def slow() -> list[str]:
        nonlocal calls
        calls += 1
        release.wait(1)
        return ["done"]

    assert service._run_provider("slow", slow, 0.01) == ([], "provider_timeout")
    assert service._run_provider("slow", slow, 0.01) == ([], "provider_busy")
    assert calls == 1
    assert len(service.inflight) == 1
    release.set()
    service.executor.shutdown(wait=True, cancel_futures=True)
    engine.dispose()


def test_worker_persists_telemetry_without_any_browser_or_api_consumer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, engine, factory = runtime(tmp_path)

    def service_with_value(value: float) -> TelemetryService:
        service = TelemetryService(settings)
        service.inventory_cache = {}
        service.last_inventory = time.monotonic()
        entity = EntityReading("host", "host:headless", "Headless host")
        monkeypatch.setattr(
            service.host,
            "collect",
            lambda **kwargs: [
                _reading(
                    entity,
                    "host.cpu.utilization",
                    value,
                    observed_at=kwargs["observed_at"],
                    source="headless worker fixture",
                    interval=5,
                )
            ],
        )
        monkeypatch.setattr(service.storage, "collect", lambda **_kwargs: [])
        monkeypatch.setattr(service.platform, "collect", lambda **_kwargs: [])
        return service

    first = service_with_value(10)
    collect_for_worker(factory, settings, first)
    first.executor.shutdown(wait=True, cancel_futures=True)
    time.sleep(0.01)
    restarted = service_with_value(30)
    collect_for_worker(factory, settings, restarted)
    restarted.executor.shutdown(wait=True, cancel_futures=True)
    with factory() as session:
        values = session.scalars(
            select(MetricSample.value)
            .where(MetricSample.metric_id == "host.cpu.utilization")
            .order_by(MetricSample.observed_at)
        ).all()
    assert values == [10.0, 30.0]
    engine.dispose()


def test_expensive_platform_collection_obeys_hardware_interval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, engine, factory = runtime(tmp_path)
    service = TelemetryService(settings)
    service.inventory_cache = {}
    service.last_inventory = time.monotonic()
    calls = 0

    def collect_platform(**_kwargs: object) -> list[object]:
        nonlocal calls
        calls += 1
        return []

    monkeypatch.setattr(service.host, "collect", lambda **_kwargs: [])
    monkeypatch.setattr(service.storage, "collect", lambda **_kwargs: [])
    monkeypatch.setattr(service.platform, "collect", collect_platform)
    with factory() as session:
        assert service.collect(session)["status"] == "collected"
        session.commit()
        service.last_run -= settings.telemetry_fast_interval_seconds + 1
        assert service.collect(session)["status"] == "collected"
    assert calls == 1
    service.close(wait=True)
    engine.dispose()


def test_telemetry_service_shutdown_is_idempotent_and_stops_collection(tmp_path: Path) -> None:
    settings, engine, factory = runtime(tmp_path)
    service = TelemetryService(settings)
    service.close()
    service.close()
    with factory() as session:
        assert service.collect(session, force=True) == {
            "status": "unavailable",
            "inserted": 0,
            "providers": {},
        }
    engine.dispose()


def test_tier_occupancy_uses_configured_transfer_identity_and_real_capacity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, engine, factory = runtime(tmp_path)

    class Facts:
        f_blocks = 100
        f_frsize = 1024
        f_bavail = 25

    monkeypatch.setattr(
        "hoardarr.telemetry.service.os.statvfs", lambda _path: Facts(), raising=False
    )
    with factory() as session, session.begin():
        session.add(
            Operation(
                kind="storage.transfer",
                status="queued",
                actor_type="browser_session",
                actor_id="00000000-0000-0000-0000-000000000001",
                request_sha256="a" * 64,
                request_json={
                    "plan": {
                        "source": "/data/downloads/job.mkv",
                        "source_identity": "wwn:fast-tier",
                        "required_bytes": 10,
                    }
                },
            )
        )
    service = TelemetryService(settings)
    with factory() as session:
        readings = service._tier_readings(session, datetime.now(UTC))
    occupancy = next(item for item in readings if item.metric_id == "tier.occupancy")
    assert occupancy.value == 75
    assert occupancy.entity.stable_id == "tier:wwn:fast-tier"
    service.executor.shutdown(wait=True, cancel_futures=True)
    engine.dispose()


def test_ingestion_is_idempotent_and_history_preserves_quality(tmp_path: Path) -> None:
    _settings, engine, factory = runtime(tmp_path)
    now = datetime.now(UTC).replace(microsecond=0)
    samples = [
        reading("io.read.iops", 10, now),
        reading("drive.temperature", None, now, quality="not_reported"),
    ]
    with factory() as session, session.begin():
        assert ingest(session, samples) == {"inserted": 2, "duplicates": 0}
        assert ingest(session, samples) == {"inserted": 0, "duplicates": 2}
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(MetricSample)) == 2
        current = current_samples(session)
        assert {item["quality"] for item in current} == {"available", "not_reported"}
        entity_id = current[0]["entity"]["id"]
        document = history(
            session,
            entity_id=entity_id,
            metric_id="drive.temperature",
            start=now - timedelta(seconds=1),
            end=now + timedelta(seconds=1),
            resolution="raw",
            limit=10,
        )
        assert document["points"][0]["value"] is None
        assert document["points"][0]["quality"] == "not_reported"
    engine.dispose()


def test_retention_rolls_up_before_bounded_deletion(tmp_path: Path) -> None:
    _settings, engine, factory = runtime(tmp_path)
    now = datetime(2026, 8, 22, 12, tzinfo=UTC)
    with factory() as session, session.begin():
        ingest(
            session,
            [
                reading("io.read.latency", float(index), now - timedelta(hours=3, minutes=index))
                for index in range(30)
            ],
        )
        result = apply_retention(
            session,
            now=now,
            recent_hours=2,
            hourly_days=30,
            daily_days=365,
        )
        assert result["raw_deleted"] == 30
    with factory() as session:
        rollup = session.scalar(select(MetricRollup).where(MetricRollup.resolution == "hour"))
        assert rollup is not None
        assert rollup.sample_count > 0
        assert rollup.p95 is not None
        assert session.scalar(select(func.count()).select_from(MetricSample)) == 0
    engine.dispose()


def test_rollups_preserve_numeric_envelopes_and_health_transitions(tmp_path: Path) -> None:
    _settings, engine, factory = runtime(tmp_path)
    now = datetime(2026, 8, 22, 12, tzinfo=UTC)
    observed = now - timedelta(hours=3)
    with factory() as session, session.begin():
        ingest(
            session,
            [
                reading("drive.temperature", 20, observed),
                reading("drive.temperature", 80, observed + timedelta(minutes=1)),
                reading("drive.temperature", 30, observed + timedelta(minutes=2)),
                reading("health.overall", "healthy", observed),
                reading("health.overall", "degraded", observed + timedelta(minutes=1)),
                reading("health.overall", "healthy", observed + timedelta(minutes=2)),
            ],
        )
        build_rollups(session, now=now)
    with factory() as session:
        numeric = session.scalar(
            select(MetricRollup).where(
                MetricRollup.metric_id == "drive.temperature",
                MetricRollup.resolution == "hour",
            )
        )
        state = session.scalar(
            select(MetricRollup).where(
                MetricRollup.metric_id == "health.overall",
                MetricRollup.resolution == "hour",
            )
        )
        assert numeric is not None
        assert (numeric.first, numeric.minimum, numeric.maximum, numeric.last) == (20, 20, 80, 30)
        assert state is not None
        assert state.mean is None
        assert state.states_json == ["healthy", "degraded", "healthy"]
        assert state.transition_count == 2
    engine.dispose()


def test_retention_cleanup_is_batched(tmp_path: Path) -> None:
    _settings, engine, factory = runtime(tmp_path)
    now = datetime(2026, 8, 22, 12, tzinfo=UTC)
    with factory() as session, session.begin():
        ingest(
            session,
            [
                reading("io.read.iops", index, now - timedelta(hours=3, seconds=index))
                for index in range(25)
            ],
        )
        result = apply_retention(
            session,
            now=now,
            recent_hours=2,
            hourly_days=30,
            daily_days=365,
            batch_size=10,
        )
        assert result["raw_deleted"] == 10
        assert (
            apply_retention(
                session,
                now=now,
                recent_hours=2,
                hourly_days=30,
                daily_days=365,
                batch_size=10,
            )["raw_deleted"]
            == 10
        )
        assert (
            apply_retention(
                session,
                now=now,
                recent_hours=2,
                hourly_days=30,
                daily_days=365,
                batch_size=10,
            )["raw_deleted"]
            == 5
        )
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(MetricSample)) == 0
        assert (
            session.scalar(
                select(func.sum(MetricRollup.sample_count)).where(MetricRollup.resolution == "hour")
            )
            == 25
        )
    engine.dispose()


def test_forecasts_require_history_and_do_not_invent_tbw() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    short = capacity_forecast(
        [(start + timedelta(days=index), 100.0 + index) for index in range(3)],
        total_bytes=1_000,
        now=start + timedelta(days=3),
    )
    assert short.status == "insufficient_history"
    available = capacity_forecast(
        [(start + timedelta(days=index), 100.0 + index * 10) for index in range(30)],
        total_bytes=1_000,
        now=start + timedelta(days=30),
    )
    assert available.status == "available"
    assert available.growth_bytes_per_day == pytest.approx(10)
    assert available.projected["90"]["days"] == 51
    wear = endurance_forecast(
        [(start + timedelta(days=index), 10.0 + index / 10) for index in range(30)],
        now=start + timedelta(days=30),
    )
    assert wear["status"] == "available"
    assert "remaining_tbw" not in wear
    assert "remaining_bytes" not in wear
    assert "NAND writes" in wear["methodology"]


def test_percentiles_anomalies_and_topology_correlation_are_explainable() -> None:
    assert nearest_rank(list(range(1, 101)), 0.95) == 95
    assert nearest_rank([1] * 19, 0.95) is None
    now = datetime.now(UTC)
    first = anomaly(
        entity={"id": "a", "topology": {"controller": "c1"}},
        metric_id="io.read.latency",
        observed=100,
        history=[9, 10, 11] * 10,
        now=now,
    )
    second = anomaly(
        entity={"id": "b", "topology": {"controller": "c1"}},
        metric_id="io.read.latency",
        observed=90,
        history=[9, 10, 11] * 10,
        now=now,
    )
    assert first and second
    result = correlate([first, second])
    assert result[0]["causation_claimed"] is False
    assert "at the same time" in result[0]["explanation"]


def test_license_states_and_capabilities_are_fail_closed(tmp_path: Path) -> None:
    settings, engine, factory = runtime(tmp_path)
    service = EntitlementService(settings)
    now = datetime(2026, 8, 22, tzinfo=UTC)
    with factory() as session, session.begin():
        assert service.evaluate(session, now=now).state == "unlicensed"
        assert service.evaluate(session, now=now).allows(None)
    key = Ed25519PrivateKey.generate()
    install_license(settings, key, ["metrics.analytics.capacity"], now=now)
    with factory() as session, session.begin():
        status = service.evaluate(session, now=now)
        assert status.state == "valid"
        assert status.allows("metrics.analytics.capacity")
        assert not status.allows("metrics.export")
    install_license(settings, key, ["metrics.analytics.capacity"], now=now, corrupt=True)
    with factory() as session, session.begin():
        assert service.evaluate(session, now=now).state == "invalid"
    install_license(settings, key, ["metrics.analytics.capacity"], now=now, installation="other")
    with factory() as session, session.begin():
        assert service.evaluate(session, now=now).state == "installation_mismatch"
    install_license(
        settings,
        key,
        ["metrics.analytics.capacity"],
        now=now,
        starts=now - timedelta(days=30),
        expires=now - timedelta(seconds=1),
    )
    with factory() as session, session.begin():
        status = service.evaluate(session, now=now)
        assert status.state == "expired"
        assert status.capabilities == frozenset()
        assert status.allows(None)
    settings.telemetry_license_file.write_text("{broken", encoding="utf-8")
    with factory() as session, session.begin():
        assert service.evaluate(session, now=now).state == "invalid"
    engine.dispose()


def test_license_clock_rollback_fails_closed_without_deleting_history(tmp_path: Path) -> None:
    settings, engine, factory = runtime(tmp_path)
    now = datetime(2026, 8, 22, tzinfo=UTC)
    key = Ed25519PrivateKey.generate()
    install_license(settings, key, ["metrics.history.extended"], now=now)
    with factory() as session, session.begin():
        ingest(session, [reading("io.read.iops", 4, now)])
        assert EntitlementService(settings).evaluate(session, now=now).state == "valid"
    with factory() as session, session.begin():
        status = EntitlementService(settings).evaluate(session, now=now - timedelta(hours=1))
        assert status.state == "clock_invalid"
        assert session.scalar(select(func.count()).select_from(MetricSample)) == 1
    engine.dispose()


def test_basic_alert_hysteresis_and_acknowledgement_state(tmp_path: Path) -> None:
    _settings, engine, factory = runtime(tmp_path)
    now = datetime.now(UTC).replace(microsecond=0)
    with factory() as session, session.begin():
        ingest(session, [reading("drive.temperature", 66, now)])
        sample = session.scalar(select(MetricSample))
        assert sample is not None
        assert evaluate_basic_alerts(session, [sample]) == {"opened": 1, "resolved": 0}
    with factory() as session, session.begin():
        entity = session.scalar(select(MetricEntity))
        assert entity is not None
        ingest(session, [reading("drive.temperature", 54, now + timedelta(seconds=5))])
        warm = session.scalar(select(MetricSample).order_by(MetricSample.id.desc()))
        assert evaluate_basic_alerts(session, [warm]) == {"opened": 0, "resolved": 0}
        ingest(session, [reading("drive.temperature", 50, now + timedelta(seconds=10))])
        cool = session.scalar(select(MetricSample).order_by(MetricSample.id.desc()))
        assert evaluate_basic_alerts(session, [cool]) == {"opened": 0, "resolved": 1}
    with factory() as session:
        alert = session.scalar(select(MetricAlert))
        assert alert is not None and alert.state == "resolved"
    engine.dispose()


def test_custom_alert_uses_sustained_window_and_hysteresis(tmp_path: Path) -> None:
    _settings, engine, factory = runtime(tmp_path)
    now = datetime.now(UTC).replace(microsecond=0)
    with factory() as session, session.begin():
        entity = MetricEntity(
            entity_type="drive",
            stable_id="wwn:test",
            display_name="Test drive",
        )
        session.add(entity)
        session.flush()
        session.add(
            MetricAlertRule(
                name="Busy drive",
                metric_id="io.utilization",
                entity_id=entity.id,
                operator="gt",
                warning_value=80,
                critical_value=95,
                clear_value=70,
                sustained_seconds=60,
                created_by="owner",
            )
        )
    with factory() as session, session.begin():
        ingest(session, [reading("io.utilization", 90, now)])
        sample = session.scalar(select(MetricSample).order_by(MetricSample.id.desc()))
        assert evaluate_basic_alerts(session, [sample]) == {"opened": 0, "resolved": 0}
        ingest(session, [reading("io.utilization", 96, now + timedelta(seconds=61))])
        sample = session.scalar(select(MetricSample).order_by(MetricSample.id.desc()))
        assert evaluate_basic_alerts(session, [sample]) == {"opened": 1, "resolved": 0}
        ingest(session, [reading("io.utilization", 75, now + timedelta(seconds=70))])
        sample = session.scalar(select(MetricSample).order_by(MetricSample.id.desc()))
        assert evaluate_basic_alerts(session, [sample]) == {"opened": 0, "resolved": 0}
        ingest(session, [reading("io.utilization", 65, now + timedelta(seconds=80))])
        sample = session.scalar(select(MetricSample).order_by(MetricSample.id.desc()))
        assert evaluate_basic_alerts(session, [sample]) == {"opened": 0, "resolved": 1}
    engine.dispose()


def test_current_marks_old_values_stale_without_erasing_last_value(tmp_path: Path) -> None:
    _settings, engine, factory = runtime(tmp_path)
    old = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=5)
    with factory() as session, session.begin():
        ingest(session, [reading("io.read.iops", 17, old)])
    with factory() as session:
        item = current_samples(session)[0]
        assert item["quality"] == "stale"
        assert item["value"] == 17
    engine.dispose()


def test_api_enforces_advanced_entitlements_but_keeps_basic_metrics(tmp_path: Path) -> None:
    settings, engine, factory = runtime(tmp_path)
    now = datetime.now(UTC).replace(microsecond=0)
    with factory() as session, session.begin():
        token = issue_setup_token(session)
        ingest(
            session,
            [
                reading("io.read.iops", 7, now),
                reading("drive.interface_crc_errors", 2, now),
            ],
        )
    app = create_app(settings)
    with TestClient(app, base_url="http://testserver") as client:
        claim(client, token)
        current = client.get("/api/v1/telemetry/current")
        assert current.status_code == 200
        assert [item["metric_id"] for item in current.json()["items"]] == ["io.read.iops"]
        restricted = client.get("/api/v1/telemetry/current?metric_id=drive.interface_crc_errors")
        assert restricted.status_code == 403
        catalog = client.get("/api/v1/telemetry/catalog")
        assert catalog.status_code == 200
        assert catalog.json()["entitlements"]["basic_metrics_available"] is True
        export = client.get("/api/v1/telemetry/export/prometheus")
        assert export.status_code == 403
    engine.dispose()


def test_advanced_alert_rule_api_is_entitled_validated_and_csrf_protected(
    tmp_path: Path,
) -> None:
    settings, engine, factory = runtime(tmp_path)
    now = datetime.now(UTC)
    key = Ed25519PrivateKey.generate()
    install_license(settings, key, ["metrics.alerting.advanced"], now=now)
    with factory() as session, session.begin():
        token = issue_setup_token(session)
    app = create_app(settings)
    with TestClient(app, base_url="http://testserver") as client:
        csrf = claim(client, token)
        body = {
            "name": "Sustained queue",
            "metric_id": "io.queue.depth",
            "entity_type": "drive",
            "operator": "gt",
            "warning_value": 8,
            "critical_value": 20,
            "clear_value": 5,
            "sustained_seconds": 120,
            "enabled": True,
        }
        rejected = client.post("/api/v1/telemetry/alert-rules", json=body)
        assert rejected.status_code == 403
        created = client.post(
            "/api/v1/telemetry/alert-rules",
            json=body,
            headers={"Origin": "http://testserver", "X-CSRF-Token": csrf},
        )
        assert created.status_code == 201
        rule_id = created.json()["id"]
        assert client.get("/api/v1/telemetry/alert-rules").json()["items"][0]["id"] == rule_id
        invalid = {**body, "clear_value": 10}
        response = client.put(
            f"/api/v1/telemetry/alert-rules/{rule_id}",
            json=invalid,
            headers={"Origin": "http://testserver", "X-CSRF-Token": csrf},
        )
        assert response.status_code == 422
        deleted = client.delete(
            f"/api/v1/telemetry/alert-rules/{rule_id}",
            headers={"Origin": "http://testserver", "X-CSRF-Token": csrf},
        )
        assert deleted.status_code == 204
    engine.dispose()


def test_telemetry_api_bounds_queries_and_export_excludes_untrusted_labels(
    tmp_path: Path,
) -> None:
    settings, engine, factory = runtime(tmp_path)
    now = datetime.now(UTC).replace(microsecond=0)
    key = Ed25519PrivateKey.generate()
    install_license(settings, key, ["metrics.export"], now=now)
    protected = MetricReading(
        entity=EntityReading(
            "drive",
            "wwn:export",
            'Drive "one"',
            labels={"serial": "=HYPERLINK(secret)"},
        ),
        metric_id="io.read.iops",
        observed_at=now,
        value=12,
        quality="available",
        source="test fixture",
        collection_interval_seconds=5,
        labels={"client": "sensitive-name"},
    )
    with factory() as session, session.begin():
        token = issue_setup_token(session)
        ingest(session, [protected])
    app = create_app(settings)
    with TestClient(app, base_url="http://testserver") as client:
        assert client.get("/api/v1/telemetry/current").status_code == 401
        claim(client, token)
        assert client.get("/api/v1/telemetry/current?metric_id=does.not.exist").status_code == 404
        too_large = client.get(
            "/api/v1/telemetry/history",
            params={
                "entity_id": "00000000-0000-0000-0000-000000000000",
                "metric_id": "io.read.iops",
                "start": (now - timedelta(days=800)).isoformat(),
                "end": now.isoformat(),
            },
        )
        assert too_large.status_code == 413
        exported = client.get("/api/v1/telemetry/export/prometheus")
        assert exported.status_code == 200
        assert "hoardarr_io_read_iops" in exported.text
        assert "HYPERLINK" not in exported.text
        assert "sensitive-name" not in exported.text
        assert "Drive" not in exported.text
    engine.dispose()


def test_history_point_budget_and_policy_are_enforced_by_api(tmp_path: Path) -> None:
    settings, engine, factory = runtime(tmp_path)
    now = datetime.now(UTC).replace(microsecond=0)
    with factory() as session, session.begin():
        token = issue_setup_token(session)
        ingest(
            session,
            [
                reading("io.read.iops", index, now - timedelta(seconds=index * 5))
                for index in range(101)
            ],
        )
        entity = session.scalar(select(MetricEntity))
        assert entity is not None
        entity_id = entity.id
    with TestClient(create_app(settings), base_url="http://testserver") as client:
        claim(client, token)
        rejected = client.get(
            "/api/v1/telemetry/history",
            params={
                "entity_id": entity_id,
                "metric_id": "io.read.iops",
                "start": (now - timedelta(minutes=10)).isoformat(),
                "end": now.isoformat(),
                "resolution": "raw",
                "limit": 100,
            },
        )
        assert rejected.status_code == 413
        assert rejected.json()["type"].endswith("point_budget_exceeded")
        policy = client.get("/api/v1/telemetry/settings")
        assert policy.status_code == 200
        assert policy.json()["history"]["maximum_graph_points"] == 1200
        assert policy.json()["storage"]["estimated_bytes_per_day"] > 0
    engine.dispose()


def test_auto_history_uses_retained_resolution_and_describes_aggregation(tmp_path: Path) -> None:
    settings, engine, factory = runtime(tmp_path)
    now = datetime.now(UTC).replace(microsecond=0)
    install_license(
        settings,
        Ed25519PrivateKey.generate(),
        ["metrics.history.extended"],
        now=now,
    )
    observed = now - timedelta(hours=72)
    with factory() as session, session.begin():
        token = issue_setup_token(session)
        ingest(session, [reading("io.read.iops", 42, observed)])
        build_rollups(session, now=now)
        entity = session.scalar(select(MetricEntity))
        assert entity is not None
        entity_id = entity.id
    with TestClient(create_app(settings), base_url="http://testserver") as client:
        claim(client, token)
        response = client.get(
            "/api/v1/telemetry/history",
            params={
                "entity_id": entity_id,
                "metric_id": "io.read.iops",
                "start": (observed - timedelta(minutes=1)).isoformat(),
                "end": now.isoformat(),
                "resolution": "auto",
                "limit": 100,
            },
        )
        assert response.status_code == 200
        document = response.json()
        assert document["requested_resolution"] == "auto"
        assert document["source_resolution"] == "hour"
        assert document["raw"] is False
        assert document["points_returned"] == 1
        assert document["points"][0]["sample_count"] == 1
        assert document["points"][0]["interval_seconds"] == 3600
    engine.dispose()


@pytest.mark.parametrize("count", [1, 24, 60, 120, 240])
def test_scale_ingestion_and_bounded_current_query(tmp_path: Path, count: int) -> None:
    _settings, engine, factory = runtime(tmp_path)
    now = datetime.now(UTC).replace(microsecond=0)
    readings = [
        reading(
            "io.write.iops",
            float(index),
            now,
            stable_id=f"wwn:scale:{index:04d}",
        )
        for index in range(count)
    ]
    with factory() as session, session.begin():
        assert ingest(session, readings)["inserted"] == count
    with factory() as session:
        assert len(current_samples(session, limit=100)) == min(100, count)
    engine.dispose()


def test_anomaly_analysis_uses_one_globally_bounded_history_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _settings, engine, factory = runtime(tmp_path)
    latest = [
        {
            "entity": {"id": f"entity-{index}", "type": "drive", "name": str(index)},
            "metric_id": "drive.temperature",
            "value": 40.0,
            "quality": "available",
        }
        for index in range(telemetry_routes.ANOMALY_MAX_SERIES + 50)
    ]
    monkeypatch.setattr(telemetry_routes, "current_samples", lambda *_args, **_kwargs: latest)
    statements: list[str] = []

    def record_statement(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record_statement)
    try:
        with factory() as session:
            status = SimpleNamespace(allows=lambda _capability: True)
            assert telemetry_routes._active_anomalies(session, status) == []
    finally:
        event.remove(engine, "before_cursor_execute", record_statement)
        engine.dispose()

    assert len(statements) == 1
    assert "row_number() OVER" in statements[0]


def test_analytics_enforces_underlying_metric_entitlement(tmp_path: Path) -> None:
    settings, engine, factory = runtime(tmp_path)
    now = datetime.now(UTC).replace(microsecond=0)
    install_license(
        settings,
        Ed25519PrivateKey.generate(),
        ["metrics.analytics.performance", "metrics.analytics.anomaly"],
        now=now,
    )
    with factory() as session, session.begin():
        token = issue_setup_token(session)
        ingest(
            session,
            [
                reading(
                    "controller.cache.hit_ratio",
                    99.0,
                    now,
                    entity_type="controller",
                    stable_id="controller:test",
                )
            ],
        )

    with TestClient(create_app(settings), base_url="http://testserver") as client:
        claim(client, token)
        response = client.get(
            "/api/v1/telemetry/top",
            params={"metric_id": "controller.cache.hit_ratio"},
        )
        assert response.status_code == 403
        assert response.json()["type"].endswith("entitlement_required")

    engine.dispose()
