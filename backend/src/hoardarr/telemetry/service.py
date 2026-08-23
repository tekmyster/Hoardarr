from __future__ import annotations

import hashlib
import logging
import os
import shutil
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from hoardarr.core.config import Settings
from hoardarr.db.models import (
    HardwareSnapshot,
    MetricSample,
    Operation,
    StorageBackend,
    StorageEntity,
    StorageGroup,
    StoragePath,
    TelemetryState,
)
from hoardarr.storage.inventory import discover_storage_inventory
from hoardarr.storage.redundancy import reconcile_storage_path_health
from hoardarr.storage.telemetry import storage_telemetry
from hoardarr.telemetry.alerts import evaluate_basic_alerts
from hoardarr.telemetry.collectors import HostCollector, StorageCollector, _reading
from hoardarr.telemetry.platform_collectors import LinuxStoragePlatformCollector
from hoardarr.telemetry.samples import EntityReading, MetricReading
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
        self.last_platform = 0.0
        self.last_platform_error: str | None = None
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
        # Occupancy is tied only to explicit Storage Group cache/landing membership,
        # never inferred from media type or the presence of a past transfer.
        tier_sources = session.execute(
            select(StorageBackend, StorageGroup)
            .join(StorageGroup, StorageGroup.id == StorageBackend.storage_group_id)
            .where(
                StorageBackend.role.in_(("cache", "landing")),
                StorageBackend.lifecycle_state.notin_(("retired", "reuse_ready")),
            )
            .order_by(StorageBackend.id)
            .limit(256)
        ).all()
        for backend, group in tier_sources:
            source = backend.namespace_path
            utilization: float | None = None
            if isinstance(source, str) and source:
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
                        f"tier:{backend.stable_identity}"[:512],
                        f"{group.name} download tier"[:256],
                        labels={
                            "configured_path": (source or "")[:512],
                            "storage_group_id": group.id,
                            "role": backend.role,
                        },
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
            if (
                force
                or self.last_platform == 0.0
                or monotonic_now - self.last_platform
                >= self.settings.telemetry_hardware_interval_seconds
            ):
                platform_readings, platform_error = self._run_provider(
                    "linux-storage-platforms", lambda: self.platform.collect(observed_at=now), 15.0
                )
                if platform_error != "provider_busy":
                    self.last_platform = monotonic_now
                    self.last_platform_error = platform_error
                if platform_error is None:
                    reconcile_storage_path_health(
                        session,
                        self.platform.last_multipath_maps,
                    )
            else:
                platform_readings = []
                platform_error = self.last_platform_error
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
            readings.extend(self._storage_path_readings(session, storage_readings, now))
            readings.extend(self._logical_storage_readings(session, storage_readings, now))
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

    @staticmethod
    def _logical_counter_value(
        session: Session,
        *,
        entity_id: str,
        metric_id: str,
        source_device: str,
        source_value: float,
        now: datetime,
    ) -> float:
        state_id = f"logical-io:{entity_id}"
        state = session.get(TelemetryState, state_id)
        document = dict(state.state_json) if state is not None else {}
        day = now.astimezone(UTC).date().isoformat()
        counters = document.get("counters") if document.get("day") == day else {}
        counters = dict(counters) if isinstance(counters, dict) else {}
        previous = counters.get(metric_id)
        previous = previous if isinstance(previous, dict) else {}
        prior_source = previous.get("source")
        prior_source_value = previous.get("source_value")
        prior_logical_value = previous.get("logical_value")
        offset = previous.get("offset")
        offset = float(offset) if isinstance(offset, (int, float)) else 0.0
        if (
            prior_source != source_device
            or not isinstance(prior_source_value, (int, float))
            or source_value < float(prior_source_value)
        ):
            offset = (
                float(prior_logical_value) if isinstance(prior_logical_value, (int, float)) else 0.0
            )
        logical_value = offset + source_value
        counters[metric_id] = {
            "source": source_device,
            "source_value": source_value,
            "offset": offset,
            "logical_value": logical_value,
        }
        payload = {"day": day, "counters": counters}
        if state is None:
            session.add(TelemetryState(id=state_id, state_json=payload))
            session.flush()
        else:
            state.state_json = payload
            state.updated_at = now
        return logical_value

    def _logical_storage_readings(
        self,
        session: Session,
        storage_readings: list[MetricReading],
        now: datetime,
    ) -> list[MetricReading]:
        """Keep logical-storage history stable while its Linux path changes."""

        by_device: dict[str, list[MetricReading]] = {}
        for reading in storage_readings:
            if reading.entity.entity_type != "drive":
                continue
            device_name = reading.entity.labels.get("device")
            if device_name:
                by_device.setdefault(device_name, []).append(reading)
        result: list[MetricReading] = []
        copied_metrics = {
            "io.read.bytes_per_second",
            "io.write.bytes_per_second",
            "io.read.iops",
            "io.write.iops",
            "io.read.latency",
            "io.write.latency",
            "io.utilization",
            "io.queue.depth",
            "io.read.today",
            "io.write.today",
        }
        for storage in session.scalars(select(StorageEntity)):
            resolved = os.path.realpath(storage.presentation_device)
            candidate_names = [
                os.path.basename(resolved),
                os.path.basename(storage.presentation_device),
            ]
            paths = list(
                session.scalars(
                    select(StoragePath).where(StoragePath.storage_entity_id == storage.id)
                )
            )
            candidate_names.extend(
                os.path.basename(path.kernel_path) for path in paths if path.active
            )
            source_name = next((name for name in candidate_names if name in by_device), None)
            entity = EntityReading(
                "logical_storage",
                f"logical-storage:{storage.stable_identity}",
                storage.name,
                labels={"topology_state": storage.topology_state},
                topology={
                    "storage_entity_id": storage.id,
                    "path_count": str(len(paths)),
                },
            )
            if source_name is not None:
                for reading in by_device[source_name]:
                    if reading.metric_id not in copied_metrics:
                        continue
                    value = reading.value
                    if reading.metric_id in {"io.read.today", "io.write.today"} and isinstance(
                        value, (int, float)
                    ):
                        value = self._logical_counter_value(
                            session,
                            entity_id=storage.id,
                            metric_id=reading.metric_id,
                            source_device=source_name,
                            source_value=float(value),
                            now=now,
                        )
                    result.append(
                        _reading(
                            entity,
                            reading.metric_id,
                            value,
                            observed_at=now,
                            source=f"{reading.source} via {source_name}"[:128],
                            interval=reading.collection_interval_seconds,
                            quality=reading.quality,
                            error_code=reading.error_code,
                            labels={**reading.labels, "source_device": source_name},
                        )
                    )
            try:
                filesystem = shutil.disk_usage(storage.mountpoint)
                total = filesystem.total
                free = filesystem.free
                used = filesystem.used
                capacity_values: tuple[tuple[str, float | int], ...] = (
                    ("capacity.total", total),
                    ("capacity.used", used),
                    ("capacity.free", free),
                    ("capacity.utilization", used / total * 100 if total else 0.0),
                )
                for metric_id, value in capacity_values:
                    result.append(
                        _reading(
                            entity,
                            metric_id,
                            value,
                            observed_at=now,
                            source="statvfs logical mount",
                            interval=max(60, self.settings.telemetry_fast_interval_seconds),
                        )
                    )
            except OSError:
                pass
            result.append(
                _reading(
                    entity,
                    "health.overall",
                    {
                        "fully_redundant": "healthy",
                        "single_path": "healthy",
                        "reduced_redundancy": "degraded",
                        "failed_over": "degraded",
                        "no_path": "faulted",
                    }.get(storage.topology_state, "unknown"),
                    observed_at=now,
                    source="durable logical storage topology",
                    interval=max(30, self.settings.telemetry_fast_interval_seconds),
                )
            )
            for metric_id, value in (
                ("storage.paths.healthy", sum(path.active for path in paths)),
                ("storage.paths.failed", sum(not path.active for path in paths)),
            ):
                result.append(
                    _reading(
                        entity,
                        metric_id,
                        value,
                        observed_at=now,
                        source="durable multipath topology",
                        interval=max(30, self.settings.telemetry_fast_interval_seconds),
                    )
                )
        return result

    def _storage_path_readings(
        self,
        session: Session,
        storage_readings: list[MetricReading],
        now: datetime,
    ) -> list[MetricReading]:
        """Attach physical-path counters to durable path IDs instead of /dev names."""

        by_device: dict[str, list[MetricReading]] = {}
        for reading in storage_readings:
            if reading.entity.entity_type != "drive":
                continue
            device_name = reading.entity.labels.get("device")
            if device_name:
                by_device.setdefault(device_name, []).append(reading)
        copied_metrics = {
            "io.read.bytes_per_second",
            "io.write.bytes_per_second",
            "io.read.iops",
            "io.write.iops",
            "io.read.latency",
            "io.write.latency",
            "io.utilization",
            "io.queue.depth",
        }
        result: list[MetricReading] = []
        for path in session.scalars(select(StoragePath)):
            storage = session.get(StorageEntity, path.storage_entity_id)
            if storage is None:
                continue
            device_name = os.path.basename(path.kernel_path)
            entity = EntityReading(
                "storage_path",
                f"storage-path:{path.stable_path_identity}"[:512],
                path.kernel_path,
                labels={
                    "device": device_name,
                    "protocol": path.protocol,
                    "state": path.state,
                    "optimized": (
                        "not_reported" if path.optimized is None else str(path.optimized).lower()
                    ),
                },
                topology={
                    "storage_entity_id": storage.id,
                    "logical_storage": f"logical-storage:{storage.stable_identity}"[:512],
                    **(
                        {"controller_id": path.controller_id}
                        if path.controller_id is not None
                        else {}
                    ),
                },
            )
            for reading in by_device.get(device_name, []):
                if reading.metric_id not in copied_metrics:
                    continue
                result.append(
                    _reading(
                        entity,
                        reading.metric_id,
                        reading.value,
                        observed_at=now,
                        source=f"{reading.source} via {device_name}"[:128],
                        interval=reading.collection_interval_seconds,
                        quality=reading.quality,
                        error_code=reading.error_code,
                    )
                )
            result.extend(
                (
                    _reading(
                        entity,
                        "storage.path.state",
                        path.state or "unknown",
                        observed_at=now,
                        source="durable multipath topology",
                        interval=max(30, self.settings.telemetry_fast_interval_seconds),
                    ),
                    _reading(
                        entity,
                        "health.overall",
                        (
                            "healthy"
                            if path.active
                            else "faulted"
                            if path.state in {"failed", "faulty", "offline", "missing"}
                            else "degraded"
                        ),
                        observed_at=now,
                        source="durable multipath topology",
                        interval=max(30, self.settings.telemetry_fast_interval_seconds),
                    ),
                )
            )
        return result

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
