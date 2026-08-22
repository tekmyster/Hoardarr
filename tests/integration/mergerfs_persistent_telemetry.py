#!/usr/bin/env python3
"""Headless collector and evidence reporter for the disposable mergerFS workload."""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from hoardarr.api.app import create_app
from hoardarr.auth.service import issue_setup_token
from hoardarr.core.config import Settings
from hoardarr.db.engine import create_database_engine, create_session_factory
from hoardarr.db.migrate import upgrade_database
from hoardarr.db.models import MetricEntity, MetricRollup, MetricSample
from hoardarr.storage.telemetry import StorageTelemetrySampler
from hoardarr.telemetry.collectors import HostCollector, StorageCollector
from hoardarr.telemetry.service import TelemetryService
from hoardarr.telemetry.store import build_rollups, history, ingest
from sqlalchemy import func, select


def runtime(database: Path):  # type: ignore[no-untyped-def]
    url = f"sqlite:///{database.as_posix()}"
    upgrade_database(url)
    engine = create_database_engine(url)
    return engine, create_session_factory(engine)


def collect(args: argparse.Namespace) -> int:
    config = json.loads(args.config.read_text(encoding="utf-8"))
    engine, factory = runtime(args.database)
    sampler = StorageTelemetrySampler(
        state_path=args.database.with_suffix(".counters.json"),
        smart_reader=lambda _device, _sector: {
            "lifetime_writes_bytes": None,
            "remaining_percent": None,
            "source": None,
        },
    )
    storage = StorageCollector(sampler=sampler, interval_seconds=1, health_interval_seconds=60)
    host = HostCollector(interval_seconds=1)
    deadline = time.monotonic() + args.seconds
    cycles = 0
    while time.monotonic() < deadline:
        now = datetime.now(UTC)
        readings = [
            *host.collect(observed_at=now),
            *storage.collect(
                hardware_snapshot=config["hardware"],
                inventory=config["inventory"],
                observed_at=now,
            ),
        ]
        with factory() as session, session.begin():
            readings.extend(TelemetryService._derived_readings(session, readings, now, 1))
            ingest(session, readings)
        cycles += 1
        time.sleep(1)
    engine.dispose()
    print(json.dumps({"collector_cycles": cycles, "api_clients": 0}))
    return 0


def _distribution(branches: list[str]) -> list[dict[str, int | str]]:
    result = []
    for branch in branches:
        files = [item for item in Path(branch).rglob("*") if item.is_file()]
        result.append(
            {
                "member": branch,
                "files": len(files),
                "bytes": sum(item.stat().st_size for item in files),
            }
        )
    return result


def _diskstats(path: Path, names: set[str]) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        fields = line.split()
        if len(fields) < 14 or fields[2] not in names:
            continue
        try:
            result[fields[2]] = {
                "reads": int(fields[3]),
                "read_sectors": int(fields[5]),
                "read_ms": int(fields[6]),
                "writes": int(fields[7]),
                "write_sectors": int(fields[9]),
                "write_ms": int(fields[10]),
                "io_ms": int(fields[12]),
                "weighted_io_ms": int(fields[13]),
            }
        except ValueError:
            continue
    return result


def report(args: argparse.Namespace) -> int:
    config = json.loads(args.config.read_text(encoding="utf-8"))
    phases = json.loads(args.phases.read_text(encoding="utf-8"))
    names = {Path(item["kernel_path"]).name for item in config["hardware"]["disks"]}
    before = _diskstats(args.independent / "diskstats-before.txt", names)
    after = _diskstats(args.independent / "diskstats-after.txt", names)
    deltas = {
        name: {
            key: max(0, values[key] - before.get(name, {}).get(key, values[key]))
            for key in values
        }
        for name, values in after.items()
    }
    engine, factory = runtime(args.database)
    with factory() as session, session.begin():
        # Build a real rollup from the collected samples without deleting raw data.
        rollups = build_rollups(session, now=datetime.now(UTC) + timedelta(hours=2))
    with factory() as session:
        minimum, maximum, total = session.execute(
            select(
                func.min(MetricSample.observed_at),
                func.max(MetricSample.observed_at),
                func.count(MetricSample.id),
            )
        ).one()
        pool = session.scalar(
            select(MetricEntity).where(MetricEntity.stable_id == "mergerfs:workload")
        )
        if pool is None:
            raise RuntimeError("mergerFS pool telemetry entity was not persisted")
        metric_counts = dict(
            session.execute(
                select(MetricSample.metric_id, func.count(MetricSample.id))
                .where(MetricSample.entity_id == pool.id)
                .group_by(MetricSample.metric_id)
            ).all()
        )
        graph = history(
            session,
            entity_id=pool.id,
            metric_id="io.write.bytes_per_second",
            start=minimum.replace(tzinfo=UTC),
            end=(maximum + timedelta(seconds=1)).replace(tzinfo=UTC),
            resolution="raw",
            limit=1200,
        )
        disconnect_start = datetime.fromisoformat(phases["browser_disconnected_start"])
        disconnect_end = datetime.fromisoformat(phases["browser_reconnected_at"])
        disconnected_samples = int(
            session.scalar(
                select(func.count(MetricSample.id)).where(
                    MetricSample.observed_at >= disconnect_start,
                    MetricSample.observed_at <= disconnect_end,
                )
            )
            or 0
        )
        writes = session.execute(
            select(
                MetricSample.entity_id,
                func.min(MetricSample.value),
                func.max(MetricSample.value),
            )
            .where(MetricSample.metric_id == "io.write.today", MetricSample.value.is_not(None))
            .group_by(MetricSample.entity_id)
        ).all()
        rollup_count = int(session.scalar(select(func.count()).select_from(MetricRollup)) or 0)
    settings = Settings(
        environment="test",
        database_url=f"sqlite:///{args.database.as_posix()}",
        secret_key_file=args.database.with_suffix(".api-secret.key"),
        secure_cookies=False,
        installation_identity_file=args.database.with_suffix(".machine-id"),
        telemetry_license_file=args.database.with_suffix(".license.json"),
        telemetry_license_trust_file=args.database.with_suffix(".license-trust.json"),
    )
    settings.installation_identity_file.write_text("mergerfs-workload-runner\n", encoding="utf-8")
    with factory() as session, session.begin():
        setup_token = issue_setup_token(session)
    with TestClient(create_app(settings), base_url="http://testserver") as client:
        claimed = client.post(
            "/api/v1/setup/claim",
            headers={"Origin": "http://testserver"},
            json={"token": setup_token, "username": "workload", "password": "test"},
        )
        if claimed.status_code != 201:
            raise RuntimeError(f"history API setup failed: {claimed.status_code}")
        api_response = client.get(
            "/api/v1/telemetry/history",
            params={
                "entity_id": pool.id,
                "metric_id": "io.write.bytes_per_second",
                "start": minimum.replace(tzinfo=UTC).isoformat(),
                "end": (maximum + timedelta(seconds=1)).replace(tzinfo=UTC).isoformat(),
                "resolution": "raw",
                "limit": 1200,
            },
        )
        if api_response.status_code != 200:
            raise RuntimeError(f"history API reconstruction failed: {api_response.status_code}")
        api_graph = api_response.json()
    distribution = _distribution(config["branches"])
    if any(int(item["files"]) == 0 for item in distribution):
        raise RuntimeError("workload did not place files on every mergerFS member")
    if disconnected_samples == 0:
        raise RuntimeError("no telemetry persisted while every browser/API client was absent")
    if not any(
        (maximum_value or 0) > (minimum_value or 0)
        for _, minimum_value, maximum_value in writes
    ):
        raise RuntimeError("writes-today did not increase during the controlled workload")
    if not all(item.get("write_sectors", 0) > 0 for item in deltas.values()) or len(deltas) != 4:
        raise RuntimeError("independent /proc/diskstats did not observe writes on all four members")
    if not any(item.get("read_sectors", 0) > 0 for item in deltas.values()):
        raise RuntimeError("independent /proc/diskstats did not observe the read workload")
    required = {
        "io.read.bytes_per_second",
        "io.write.bytes_per_second",
        "io.read.iops",
        "io.write.iops",
        "io.read.latency",
        "io.write.latency",
        "io.utilization",
        "io.write.today",
    }
    if not required.issubset(metric_counts):
        raise RuntimeError(f"pool metrics missing: {sorted(required - set(metric_counts))}")
    result = {
        "status": "VERIFIED IN ISOLATION",
        "environment": config["environment"],
        "devices": config["hardware"]["disks"],
        "filesystem": "ext4",
        "mergerfs_policy": config["policy"],
        "mountpoint": config["mountpoint"],
        "members": distribution,
        "phases": phases,
        "samples": int(total),
        "first_sample": minimum.isoformat(),
        "last_sample": maximum.isoformat(),
        "samples_while_browser_disconnected": disconnected_samples,
        "historical_graph_points_after_reconnect": graph["points_returned"],
        "history_api_points_after_reconnect": api_graph["points_returned"],
        "history_api_resolution": api_graph["source_resolution"],
        "pool_metric_counts": metric_counts,
        "independent_linux": {
            "diskstats_deltas": deltas,
            "iostat_samples": len(
                (args.independent / "iostat.txt").read_text(encoding="utf-8").splitlines()
            ),
            "vmstat_samples": len(
                (args.independent / "vmstat.txt").read_text(encoding="utf-8").splitlines()
            ),
            "df": (args.independent / "df-after.txt").read_text(encoding="utf-8").strip(),
            "du_bytes": int(
                (args.independent / "du-after.txt").read_text(encoding="utf-8").split()[0]
            ),
            "comparison": "same devices and phase window; sampling-window differences allowed",
        },
        "writes_today_ranges": [
            {"entity_id": entity, "first": first, "last": last}
            for entity, first, last in writes
        ],
        "rollups_created": rollups["rollups_created"],
        "rollups_retained": rollup_count,
        "service_restart": "history retained and collection resumed in a second process",
        "browser_owner_of_history": False,
        "cleanup": "performed by the enclosing disposable-loop harness",
    }
    args.output.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(json.dumps(result, indent=2, default=str))
    engine.dispose()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    collector = sub.add_parser("collect")
    collector.add_argument("--database", type=Path, required=True)
    collector.add_argument("--config", type=Path, required=True)
    collector.add_argument("--seconds", type=int, required=True)
    reporter = sub.add_parser("report")
    reporter.add_argument("--database", type=Path, required=True)
    reporter.add_argument("--config", type=Path, required=True)
    reporter.add_argument("--phases", type=Path, required=True)
    reporter.add_argument("--output", type=Path, required=True)
    reporter.add_argument("--independent", type=Path, required=True)
    args = parser.parse_args()
    return collect(args) if args.command == "collect" else report(args)


if __name__ == "__main__":
    raise SystemExit(main())
