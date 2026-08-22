from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from hoardarr.core.config import Settings
from hoardarr.db.models import HardwareSnapshot, MetricSample, Operation, TelemetryState
from hoardarr.storage.inventory import discover_storage_inventory
from hoardarr.storage.telemetry import storage_telemetry
from hoardarr.telemetry.alerts import evaluate_basic_alerts
from hoardarr.telemetry.collectors import HostCollector, StorageCollector, _reading
from hoardarr.telemetry.platform_collectors import LinuxStoragePlatformCollector
from hoardarr.telemetry.samples import EntityReading
from hoardarr.telemetry.store import apply_retention, ingest

LOGGER = logging.getLogger(__name__)


class TelemetryService:
    """Failure-isolated collection coordinator shared by API and worker processes."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.host = HostCollector(interval_seconds=settings.telemetry_fast_interval_seconds)
        self.storage = StorageCollector(
            sampler=storage_telemetry,
            interval_seconds=settings.telemetry_fast_interval_seconds,
            health_interval_seconds=settings.telemetry_device_interval_seconds,
        )
        self.platform = LinuxStoragePlatformCollector(
            interval_seconds=max(30, settings.telemetry_fast_interval_seconds)
        )
        self.lock = threading.Lock()
        self.executor = ThreadPoolExecutor(
            max_workers=settings.telemetry_collector_workers,
            thread_name_prefix="telemetry-provider",
        )
        self.inflight: dict[str, Future[Any]] = {}
        self.last_run = 0.0
        self.last_inventory = 0.0
        self.inventory_cache: dict[str, Any] | None = None
        self.closed = False

    def close(self, *, wait: bool = False) -> None:
        """Release provider threads when the owning worker stops."""

        with self.lock:
            if self.closed:
                return
            self.closed = True
        self.executor.shutdown(wait=wait, cancel_futures=True)

    @staticmethod
    def _provider_state(
        session: Session, name: str, *, status: str, detail: str | None, now: datetime
    ) -> None:
        key = f"collector:{name}"
        state = session.get(TelemetryState, key)
        document = {
            "provider": name,
            "status": status,
            "detail": detail,
            "checked_at": now.isoformat(),
        }
        if state is None:
            session.add(TelemetryState(id=key, state_json=document))
        else:
            state.state_json = document
            state.updated_at = now

    def _run_provider(
        self, name: str, function: Any, timeout_seconds: float
    ) -> tuple[Any, str | None]:
        prior = self.inflight.get(name)
        if prior is not None:
            if not prior.done():
                return [], "provider_busy"
            self.inflight.pop(name, None)
        if len(self.inflight) >= self.settings.telemetry_collector_workers:
            return [], "provider_capacity_exhausted"
        future = self.executor.submit(function)
        self.inflight[name] = future
        try:
            result = future.result(timeout=timeout_seconds)
            self.inflight.pop(name, None)
            return result, None
        except TimeoutError:
            return [], "provider_timeout"
        except Exception as exc:
            self.inflight.pop(name, None)
            LOGGER.warning("Telemetry provider %s failed (%s)", name, type(exc).__name__)
            return [], "provider_failed"

    def _tier_readings(self, session: Session, now: datetime) -> list[Any]:
        operations = session.scalars(
            select(Operation)
            .where(Operation.kind == "storage.transfer")
            .order_by(Operation.created_at.desc())
            .limit(5000)
        ).all()
        if not operations:
            return []
        completed = [item for item in operations if item.status == "succeeded"]
        pending = sum(item.status in {"queued", "running"} for item in operations)
        failed = sum(item.status == "failed" for item in operations)
        bytes_moved = 0
        recoverable = 0
        hardlinks = 0
        for item in completed:
            plan = item.request_json.get("plan", item.request_json)
            if not isinstance(plan, dict):
                continue
            size = plan.get("required_bytes")
            if isinstance(size, int) and size >= 0:
                bytes_moved += size
                result = item.result_json or {}
                if result.get("state") == "retained":
                    recoverable += size
            method = (item.result_json or {}).get("method") or plan.get("method")
            if method == "hardlink":
                hardlinks += 1
        entity = EntityReading("storage_tier", "tier:transfers", "Download tier")
        ratio = hardlinks / len(completed) if completed else None
        values = (
            ("tier.bytes_moved", bytes_moved),
            ("tier.transfer.failures", failed),
            ("tier.transfers.completed", len(completed)),
            ("tier.transfers.pending", pending),
            ("tier.space.recoverable", recoverable),
            ("tier.hardlink.ratio", ratio),
        )
        readings = [
            _reading(
                entity,
                metric_id,
                value,
                observed_at=now,
                source="durable transfer operations",
                interval=max(30, self.settings.telemetry_fast_interval_seconds),
            )
            for metric_id, value in values
        ]
        # Occupancy is tied to a configured durable transfer source identity,
        # never inferred from SSD/HDD media type. A missing source is reported.
        tier_sources: dict[str, str] = {}
        for item in operations:
            plan = item.request_json.get("plan", item.request_json)
            if not isinstance(plan, dict):
                continue
            source = plan.get("source")
            identity = plan.get("source_identity")
            if isinstance(source, str) and isinstance(identity, str) and identity:
                tier_sources[identity] = os.path.dirname(source) or source
        for identity, source in tier_sources.items():
            utilization: float | None = None
            try:
                facts = os.statvfs(source)
                total = facts.f_blocks * facts.f_frsize
                free = facts.f_bavail * facts.f_frsize
                if total > 0:
                    utilization = (total - free) / total * 100
            except OSError:
                pass
            readings.append(
                _reading(
                    EntityReading(
                        "storage_tier",
                        f"tier:{identity}"[:512],
                        "Download tier",
                        labels={"configured_path": source[:512]},
                    ),
                    "tier.occupancy",
                    utilization,
                    observed_at=now,
                    source="configured tier statvfs",
                    interval=max(30, self.settings.telemetry_fast_interval_seconds),
                )
            )
        return readings

    @staticmethod
    def _derived_readings(
        session: Session, readings: list[Any], now: datetime, interval: int
    ) -> list[Any]:
        result: list[Any] = []
        by_entity: dict[str, dict[str, Any]] = {}
        for item in readings:
            by_entity.setdefault(item.entity.stable_id, {})[item.metric_id] = item
            if item.metric_id != "multipath.paths.active":
                continue
            active_group = item.labels.get("active_group")
            if not active_group:
                continue
            key = (
                "multipath_transition:" + hashlib.sha256(item.entity.stable_id.encode()).hexdigest()
            )
            state = session.get(TelemetryState, key)
            previous_group = state.state_json.get("active_group") if state else None
            count = int(state.state_json.get("failovers", 0)) if state else 0
            if previous_group is not None and previous_group != active_group:
                count += 1
            document = {"active_group": active_group, "failovers": count, "at": now.isoformat()}
            if state is None:
                session.add(TelemetryState(id=key, state_json=document))
                session.flush()
            else:
                state.state_json = document
                state.updated_at = now
            result.append(
                _reading(
                    item.entity,
                    "multipath.failovers",
                    count,
                    observed_at=now,
                    source="durable path-group transitions",
                    interval=interval,
                )
            )
        for metrics in by_entity.values():
            reads = metrics.get("io.read.bytes_per_second")
            writes = metrics.get("io.write.bytes_per_second")
            if (
                reads is None
                or writes is None
                or reads.entity.entity_type
                not in {
                    "drive",
                    "pool",
                    "storage_tier",
                }
            ):
                continue
            values = (reads.value, writes.value)
            available = all(isinstance(value, (int, float)) for value in values)
            total = float(reads.value) + float(writes.value) if available else 0.0
            ratio = float(reads.value) / total if available and total > 0 else None
            result.append(
                _reading(
                    reads.entity,
                    "analytics.workload.read_ratio",
                    ratio,
                    observed_at=now,
                    source="read and write byte rates",
                    interval=interval,
                    quality="derived" if ratio is not None else "not_reported",
                    error_code=None if ratio is not None else "no_io_or_source_unavailable",
                    labels={"formula": "read_bytes/(read_bytes+write_bytes)"},
                )
            )
        return result

    def collect(self, session: Session, *, force: bool = False) -> dict[str, Any]:
        with self.lock:
            if self.closed:
                return {"status": "unavailable", "inserted": 0, "providers": {}}
            monotonic_now = time.monotonic()
            if (
                not force
                and monotonic_now - self.last_run < self.settings.telemetry_fast_interval_seconds
            ):
                return {"status": "not_due", "inserted": 0, "providers": {}}
            self.last_run = monotonic_now
            now = datetime.now(UTC)
            snapshot = session.scalar(
                select(HardwareSnapshot).order_by(HardwareSnapshot.captured_at.desc()).limit(1)
            )
            hardware = deepcopy(snapshot.payload_json) if snapshot is not None else None
            if (
                self.inventory_cache is None
                or monotonic_now - self.last_inventory
                >= self.settings.telemetry_hardware_interval_seconds
            ):
                discovered, inventory_error = self._run_provider(
                    "inventory",
                    lambda: discover_storage_inventory(hardware_snapshot=hardware),
                    30.0,
                )
                if isinstance(discovered, dict):
                    self.inventory_cache = discovered
                    self.last_inventory = monotonic_now
            else:
                inventory_error = None
            inventory = deepcopy(self.inventory_cache or {})
            host_readings, host_error = self._run_provider(
                "host", lambda: self.host.collect(observed_at=now), 2.0
            )
            storage_readings, storage_error = self._run_provider(
                "storage",
                lambda: self.storage.collect(
                    hardware_snapshot=hardware,
                    inventory=inventory,
                    observed_at=now,
                ),
                15.0,
            )
            platform_readings, platform_error = self._run_provider(
                "linux-storage-platforms", lambda: self.platform.collect(observed_at=now), 15.0
            )
            provider_errors = {
                "host": host_error,
                "storage": storage_error,
                "inventory": inventory_error,
                "linux-storage-platforms": platform_error,
            }
            service_readings = [
                _reading(
                    EntityReading("service", f"service:{name}", name),
                    "health.overall",
                    "healthy" if error is None else "faulted",
                    observed_at=now,
                    source="telemetry coordinator",
                    interval=self.settings.telemetry_fast_interval_seconds,
                    error_code=error,
                )
                for name, error in provider_errors.items()
            ]
            readings = [
                *host_readings,
                *storage_readings,
                *platform_readings,
                *service_readings,
                *self._tier_readings(session, now),
            ]
            readings.extend(
                self._derived_readings(
                    session, readings, now, self.settings.telemetry_fast_interval_seconds
                )
            )
            previous_id = (
                session.scalar(select(MetricSample.id).order_by(MetricSample.id.desc())) or 0
            )
            result = ingest(session, readings)
            session.flush()
            sample_ids = session.scalars(
                select(MetricSample.id).where(MetricSample.id > previous_id)
            ).all()
            samples = (
                list(session.scalars(select(MetricSample).where(MetricSample.id.in_(sample_ids))))
                if sample_ids
                else []
            )
            alert_result = evaluate_basic_alerts(session, samples)
            providers = {
                "host": "available" if host_error is None else "temporarily_unavailable",
                "storage": "available" if storage_error is None else "temporarily_unavailable",
                "inventory": (
                    "available" if inventory_error is None else "temporarily_unavailable"
                ),
                "linux-storage-platforms": (
                    "available" if platform_error is None else "temporarily_unavailable"
                ),
            }
            for name, error in provider_errors.items():
                self._provider_state(session, name, status=providers[name], detail=error, now=now)
            return {"status": "collected", **result, "alerts": alert_result, "providers": providers}

    def maintain(self, session: Session) -> dict[str, int]:
        return apply_retention(
            session,
            recent_hours=self.settings.telemetry_recent_retention_hours,
            hourly_days=self.settings.telemetry_hourly_retention_days,
            daily_days=self.settings.telemetry_daily_retention_days,
            batch_size=self.settings.telemetry_cleanup_batch_size,
            percentile_sample_limit=self.settings.telemetry_rollup_percentile_samples,
        )


def collect_for_worker(
    session_factory: sessionmaker[Session], settings: Settings, service: TelemetryService
) -> None:
    with session_factory() as session, session.begin():
        service.collect(session)
        state = session.get(TelemetryState, "retention_last_run")
        now = datetime.now(UTC)
        last = None
        if state is not None:
            raw = state.state_json.get("at")
            if isinstance(raw, str):
                try:
                    last = datetime.fromisoformat(raw)
                    if last.tzinfo is None:
                        last = last.replace(tzinfo=UTC)
                except ValueError:
                    last = None
        if last is None or (now - last).total_seconds() >= 3600:
            service.maintain(session)
            document = {"at": now.isoformat()}
            if state is None:
                session.add(TelemetryState(id="retention_last_run", state_json=document))
            else:
                state.state_json = document
                state.updated_at = now
