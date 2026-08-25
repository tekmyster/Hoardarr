from __future__ import annotations

import os
import socket
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psutil

from hoardarr.storage.telemetry import StorageTelemetrySampler
from hoardarr.telemetry.samples import EntityReading, MetricReading


def _reading(
    entity: EntityReading,
    metric_id: str,
    value: float | int | str | None,
    *,
    observed_at: datetime,
    source: str,
    interval: int,
    quality: str | None = None,
    error_code: str | None = None,
    labels: dict[str, str] | None = None,
) -> MetricReading:
    selected_quality = quality or ("available" if value is not None else "not_reported")
    return MetricReading(
        entity=entity,
        metric_id=metric_id,
        observed_at=observed_at,
        value=value,
        quality=selected_quality,  # type: ignore[arg-type]
        source=source,
        collection_interval_seconds=interval,
        labels=labels or {},
        error_code=error_code,
    )


@dataclass
class _CounterSnapshot:
    timestamp: float
    identity: str
    values: dict[str, int]


class ResetSafeCounterRates:
    """Turn stable-identity counters into rates without reset/clock-jump spikes."""

    def __init__(self, *, maximum_elapsed_seconds: float = 300.0) -> None:
        self.maximum_elapsed_seconds = maximum_elapsed_seconds
        self.previous: dict[str, _CounterSnapshot] = {}

    def update(
        self,
        key: str,
        *,
        identity: str,
        timestamp: float,
        counters: Mapping[str, int],
    ) -> dict[str, float | None]:
        previous = self.previous.get(key)
        current = {name: max(0, int(value)) for name, value in counters.items()}
        self.previous[key] = _CounterSnapshot(timestamp, identity, current)
        elapsed = timestamp - previous.timestamp if previous else 0.0
        if (
            previous is None
            or previous.identity != identity
            or elapsed <= 0
            or elapsed > self.maximum_elapsed_seconds
            or any(current[name] < previous.values.get(name, current[name]) for name in current)
        ):
            return {name: None for name in current}
        return {
            name: (current[name] - previous.values.get(name, current[name])) / elapsed
            for name in current
        }


class HostCollector:
    name = "host"

    def __init__(self, *, interval_seconds: int = 5) -> None:
        self.interval_seconds = interval_seconds
        self.network_rates = ResetSafeCounterRates()

    def collect(self, *, observed_at: datetime | None = None) -> list[MetricReading]:
        timestamp = (observed_at or datetime.now(UTC)).astimezone(UTC)
        epoch = timestamp.timestamp()
        hostname = socket.gethostname()
        host = EntityReading("host", f"host:{hostname}", hostname)
        memory = psutil.virtual_memory()
        swap = psutil.swap_memory()
        try:
            load = os.getloadavg()
        except (AttributeError, OSError):
            load = (None, None, None)
        readings = [
            _reading(
                host,
                "host.cpu.utilization",
                psutil.cpu_percent(interval=None),
                observed_at=timestamp,
                source="psutil",
                interval=self.interval_seconds,
            ),
            _reading(
                host,
                "host.load.1m",
                load[0],
                observed_at=timestamp,
                source="os.getloadavg",
                interval=self.interval_seconds,
            ),
            _reading(
                host,
                "host.load.5m",
                load[1],
                observed_at=timestamp,
                source="os.getloadavg",
                interval=self.interval_seconds,
            ),
            _reading(
                host,
                "host.load.15m",
                load[2],
                observed_at=timestamp,
                source="os.getloadavg",
                interval=self.interval_seconds,
            ),
            _reading(
                host,
                "host.memory.used",
                memory.used,
                observed_at=timestamp,
                source="psutil",
                interval=self.interval_seconds,
            ),
            _reading(
                host,
                "host.memory.available",
                memory.available,
                observed_at=timestamp,
                source="psutil",
                interval=self.interval_seconds,
            ),
            _reading(
                host,
                "host.memory.utilization",
                memory.percent,
                observed_at=timestamp,
                source="psutil",
                interval=self.interval_seconds,
            ),
            _reading(
                host,
                "host.swap.utilization",
                swap.percent,
                observed_at=timestamp,
                source="psutil",
                interval=self.interval_seconds,
            ),
            _reading(
                host,
                "host.uptime",
                max(0, epoch - psutil.boot_time()),
                observed_at=timestamp,
                source="psutil",
                interval=self.interval_seconds,
            ),
        ]
        root = Path.cwd().anchor or "/"
        try:
            usage = psutil.disk_usage(root)
        except OSError:
            usage = None
        for metric_id, value in (
            ("capacity.total", usage.total if usage else None),
            ("capacity.used", usage.used if usage else None),
            ("capacity.free", usage.free if usage else None),
            ("capacity.utilization", usage.percent if usage else None),
        ):
            readings.append(
                _reading(
                    host,
                    metric_id,
                    value,
                    observed_at=timestamp,
                    source="psutil.disk_usage",
                    interval=max(60, self.interval_seconds),
                    quality=None if usage else "temporarily_unavailable",
                    error_code=None if usage else "system_filesystem_unavailable",
                )
            )
        stats = psutil.net_if_stats()
        counters = psutil.net_io_counters(pernic=True)
        for name in sorted(set(stats) | set(counters)):
            state = stats.get(name)
            counter = counters.get(name)
            entity = EntityReading(
                "network_interface",
                f"net:{name}",
                name,
                labels={"interface": name},
                topology={"host": host.stable_id},
            )
            rates = self.network_rates.update(
                name,
                identity=name,
                timestamp=epoch,
                counters={
                    "bytes_received": counter.bytes_recv if counter else 0,
                    "bytes_sent": counter.bytes_sent if counter else 0,
                    "packets_received": counter.packets_recv if counter else 0,
                    "packets_sent": counter.packets_sent if counter else 0,
                },
            )
            readings.extend(
                [
                    _reading(
                        entity,
                        "network.link.up",
                        int(state.isup) if state else None,
                        observed_at=timestamp,
                        source="psutil",
                        interval=self.interval_seconds,
                    ),
                    _reading(
                        entity,
                        "network.link.speed",
                        state.speed * 1_000_000 if state and state.speed > 0 else None,
                        observed_at=timestamp,
                        source="psutil",
                        interval=self.interval_seconds,
                    ),
                    _reading(
                        entity,
                        "network.receive.bytes_per_second",
                        rates["bytes_received"],
                        observed_at=timestamp,
                        source="psutil counters",
                        interval=self.interval_seconds,
                    ),
                    _reading(
                        entity,
                        "network.transmit.bytes_per_second",
                        rates["bytes_sent"],
                        observed_at=timestamp,
                        source="psutil counters",
                        interval=self.interval_seconds,
                    ),
                    _reading(
                        entity,
                        "network.receive.packets_per_second",
                        rates["packets_received"],
                        observed_at=timestamp,
                        source="psutil counters",
                        interval=self.interval_seconds,
                    ),
                    _reading(
                        entity,
                        "network.transmit.packets_per_second",
                        rates["packets_sent"],
                        observed_at=timestamp,
                        source="psutil counters",
                        interval=self.interval_seconds,
                    ),
                    _reading(
                        entity,
                        "network.errors",
                        (counter.errin + counter.errout) if counter else None,
                        observed_at=timestamp,
                        source="psutil counters",
                        interval=self.interval_seconds,
                    ),
                    _reading(
                        entity,
                        "network.drops",
                        (counter.dropin + counter.dropout) if counter else None,
                        observed_at=timestamp,
                        source="psutil counters",
                        interval=self.interval_seconds,
                    ),
                ]
            )
        return readings


def _health_value(raw: object) -> str | None:
    if isinstance(raw, bool):
        return "healthy" if raw else "faulted"
    if isinstance(raw, str) and raw.strip():
        value = raw.strip().lower().replace(" ", "_")[:128]
        if value in {"unknown", "not_reported", "not-reported", "n/a", "none"}:
            return None
        return value
    return None


def _hardware_metric(disk: Mapping[str, Any], *names: str) -> float | int | None:
    metrics = disk.get("metrics")
    if not isinstance(metrics, list):
        return None
    wanted = {name.casefold() for name in names}
    for metric in metrics:
        if not isinstance(metric, Mapping) or str(metric.get("name", "")).casefold() not in wanted:
            continue
        value = metric.get("value")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
    return None


def _first_reported(primary: object, fallback: float | int | None) -> float | int | None:
    if isinstance(primary, (int, float)) and not isinstance(primary, bool):
        return primary
    return fallback


def _reported_number(value: object) -> float | int | None:
    """Keep provider display sentinels out of normalized numeric metrics."""

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return None


def _queue_depth(device_name: str, root: Path = Path("/sys/class/block")) -> int | None:
    try:
        fields = (root / device_name / "inflight").read_text(encoding="utf-8").split()
        values = [int(value) for value in fields]
    except (OSError, ValueError):
        return None
    return sum(values) if values else None


def _weighted_io_time(device_name: str, path: Path = Path("/proc/diskstats")) -> int | None:
    """Return Linux's cumulative weighted milliseconds for one exact block device."""
    if not device_name or not path.is_file():
        return None
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in lines:
        fields = line.split()
        # major minor name plus the eleven original diskstats fields. The final
        # original field is cumulative weighted milliseconds spent doing I/O.
        if len(fields) < 14 or fields[2] != device_name:
            continue
        try:
            return max(0, int(fields[13]))
        except ValueError:
            return None
    return None


def mergerfs_imbalance(
    branches: list[str], *, statvfs: Any | None = None
) -> tuple[float | None, list[dict[str, int]]]:
    """Return utilization spread in percentage points and exact branch facts."""
    members: list[dict[str, int]] = []
    provider = statvfs or getattr(os, "statvfs", None)
    if provider is None:
        return None, []
    for branch in branches:
        try:
            values = provider(branch)
        except OSError:
            continue
        total = values.f_blocks * values.f_frsize
        free = values.f_bavail * values.f_frsize
        if total <= 0:
            continue
        members.append({"total": total, "free": free})
    if len(members) < 2:
        return None, members
    utilization = [(item["total"] - item["free"]) / item["total"] * 100 for item in members]
    return round(max(utilization) - min(utilization), 4), members


class StorageCollector:
    name = "storage"

    def __init__(
        self,
        *,
        sampler: StorageTelemetrySampler,
        interval_seconds: int = 5,
        health_interval_seconds: int = 300,
    ) -> None:
        self.sampler = sampler
        self.interval_seconds = interval_seconds
        self.health_interval_seconds = health_interval_seconds

    def collect(
        self,
        *,
        hardware_snapshot: Mapping[str, Any] | None,
        inventory: Mapping[str, Any],
        observed_at: datetime | None = None,
    ) -> list[MetricReading]:
        timestamp = (observed_at or datetime.now(UTC)).astimezone(UTC)
        pools_document = inventory.get("pools")
        pools = pools_document.get("items", []) if isinstance(pools_document, Mapping) else []
        pool_items = [item for item in pools if isinstance(item, Mapping)]
        telemetry = self.sampler.sample(hardware_snapshot=hardware_snapshot, pools=pool_items)
        hostname = socket.gethostname()
        host = EntityReading("host", f"host:{hostname}", hostname)
        readings: list[MetricReading] = []
        summary = telemetry["summary"]
        for metric_id, key in (
            ("io.read.bytes_per_second", "read_bytes_per_second"),
            ("io.write.bytes_per_second", "write_bytes_per_second"),
            ("io.read.iops", "read_iops"),
            ("io.write.iops", "write_iops"),
            ("io.read.latency", "read_wait_ms"),
            ("io.write.latency", "write_wait_ms"),
            ("io.utilization", "utilization_percent"),
        ):
            readings.append(
                _reading(
                    host,
                    metric_id,
                    summary.get(key),
                    observed_at=timestamp,
                    source="Linux block counters",
                    interval=self.interval_seconds,
                )
            )
        disks = hardware_snapshot.get("disks", []) if isinstance(hardware_snapshot, Mapping) else []
        disk_by_id = {
            str(item.get("id")): item
            for item in disks
            if isinstance(item, Mapping) and item.get("id")
        }
        storage_capacity = 0
        unallocated_capacity = 0
        assigned_names = {
            name
            for pool in pool_items
            for name in pool.get("device_names", [])
            if isinstance(name, str)
        }
        for disk in disk_by_id.values():
            if disk.get("system_disk") is True:
                continue
            capacity = disk.get("capacity_bytes")
            if isinstance(capacity, int) and capacity >= 0:
                storage_capacity += capacity
                if disk.get("kernel_name") not in assigned_names:
                    unallocated_capacity += capacity
        readings.extend(
            [
                _reading(
                    host,
                    "storage.raw_capacity",
                    storage_capacity,
                    observed_at=timestamp,
                    source="hardware inventory",
                    interval=self.health_interval_seconds,
                ),
                _reading(
                    host,
                    "storage.unallocated_capacity",
                    unallocated_capacity,
                    observed_at=timestamp,
                    source="storage inventory",
                    interval=self.health_interval_seconds,
                ),
            ]
        )
        for drive in telemetry["drives"]:
            entity = EntityReading(
                "drive",
                str(drive["id"]),
                str(drive.get("model") or drive.get("device") or drive["id"]),
                labels={"device": str(drive.get("device_name") or "")},
                topology={
                    "host": host.stable_id,
                    **({"pool": str(drive["pool_ids"][0])} if drive.get("pool_ids") else {}),
                },
            )
            metrics = drive["metrics"]
            for metric_id, key in (
                ("io.read.bytes_per_second", "read_bytes_per_second"),
                ("io.write.bytes_per_second", "write_bytes_per_second"),
                ("io.read.iops", "read_iops"),
                ("io.write.iops", "write_iops"),
                ("io.read.latency", "read_wait_ms"),
                ("io.write.latency", "write_wait_ms"),
                ("io.utilization", "utilization_percent"),
            ):
                readings.append(
                    _reading(
                        entity,
                        metric_id,
                        metrics.get(key),
                        observed_at=timestamp,
                        source="Linux block counters",
                        interval=self.interval_seconds,
                    )
                )
            readings.append(
                _reading(
                    entity,
                    "io.write.today",
                    drive.get("writes_today_bytes"),
                    observed_at=timestamp,
                    source="reset-safe local counter baseline",
                    interval=self.interval_seconds,
                )
            )
            readings.append(
                _reading(
                    entity,
                    "io.read.today",
                    drive.get("reads_today_bytes"),
                    observed_at=timestamp,
                    source="reset-safe local counter baseline",
                    interval=self.interval_seconds,
                )
            )
            readings.append(
                _reading(
                    entity,
                    "io.busy_time",
                    drive.get("os_busy_time_ms_since_boot"),
                    observed_at=timestamp,
                    source="Linux block counters",
                    interval=self.interval_seconds,
                )
            )
            readings.append(
                _reading(
                    entity,
                    "io.queue.depth",
                    _queue_depth(str(drive.get("device_name") or "")),
                    observed_at=timestamp,
                    source="Linux sysfs inflight",
                    interval=self.interval_seconds,
                )
            )
            readings.append(
                _reading(
                    entity,
                    "io.weighted_time",
                    _weighted_io_time(str(drive.get("device_name") or "")),
                    observed_at=timestamp,
                    source="/proc/diskstats",
                    interval=self.interval_seconds,
                )
            )
            endurance = (
                drive.get("endurance") if isinstance(drive.get("endurance"), Mapping) else {}
            )
            readings.extend(
                [
                    _reading(
                        entity,
                        "drive.lifetime_host_writes",
                        endurance.get("lifetime_writes_bytes"),
                        observed_at=timestamp,
                        source=str(endurance.get("source") or "SMART/NVMe"),
                        interval=self.health_interval_seconds,
                    ),
                    _reading(
                        entity,
                        "drive.endurance_remaining",
                        endurance.get("remaining_percent"),
                        observed_at=timestamp,
                        source=str(endurance.get("source") or "SMART/NVMe"),
                        interval=self.health_interval_seconds,
                    ),
                    _reading(
                        entity,
                        "drive.lifetime_host_reads",
                        endurance.get("lifetime_reads_bytes"),
                        observed_at=timestamp,
                        source=str(endurance.get("source") or "SMART/NVMe"),
                        interval=self.health_interval_seconds,
                    ),
                ]
            )
            source_disk = disk_by_id.get(str(drive["id"]), {})
            health = source_disk.get("health") if isinstance(source_disk, Mapping) else None
            health_value = _health_value(
                health.get("status")
                if isinstance(health, Mapping)
                else source_disk.get("health_status")
                if isinstance(source_disk, Mapping)
                else None
            )
            if health_value is None:
                health_value = _health_value(endurance.get("health_status"))
            readings.append(
                _reading(
                    entity,
                    "health.overall",
                    health_value,
                    observed_at=timestamp,
                    source="hardware health provider",
                    interval=self.health_interval_seconds,
                )
            )
            for metric_id, names in (
                ("drive.temperature", ("temperature", "temperature_celsius")),
                ("drive.power_on_hours", ("power_on_hours",)),
                ("drive.reallocated_sectors", ("reallocated_sectors", "reallocated_sector_count")),
                ("drive.pending_sectors", ("pending_sectors", "current_pending_sector")),
                ("drive.uncorrectable_sectors", ("uncorrectable_sectors", "offline_uncorrectable")),
                ("drive.media_errors", ("media_errors", "media_and_data_integrity_errors")),
                ("drive.interface_crc_errors", ("interface_crc_errors", "udma_crc_error_count")),
                ("drive.command_timeouts", ("command_timeouts",)),
                ("drive.unsafe_shutdowns", ("unsafe_shutdowns",)),
                ("drive.percentage_used", ("percentage_used",)),
                ("drive.available_spare", ("available_spare",)),
                ("drive.available_spare_threshold", ("available_spare_threshold",)),
            ):
                smart_key = {
                    "drive.temperature": "temperature",
                    "drive.power_on_hours": "power_on_hours",
                    "drive.reallocated_sectors": "reallocated_sectors",
                    "drive.pending_sectors": "pending_sectors",
                    "drive.uncorrectable_sectors": "uncorrectable_sectors",
                    "drive.media_errors": "media_errors",
                    "drive.interface_crc_errors": "interface_crc_errors",
                    "drive.command_timeouts": "command_timeouts",
                    "drive.unsafe_shutdowns": "unsafe_shutdowns",
                    "drive.percentage_used": "percentage_used",
                    "drive.available_spare": "available_spare",
                    "drive.available_spare_threshold": "available_spare_threshold",
                }[metric_id]
                readings.append(
                    _reading(
                        entity,
                        metric_id,
                        _first_reported(
                            endurance.get(smart_key), _hardware_metric(source_disk, *names)
                        ),
                        observed_at=timestamp,
                        source="hardware health provider",
                        interval=self.health_interval_seconds,
                    )
                )
            for metric_id, key in (
                ("drive.nvme.critical_warning", "critical_warning"),
                ("drive.nvme.controller_busy_time", "controller_busy_time"),
                ("drive.nvme.power_cycles", "power_cycles"),
                ("drive.nvme.error_log_entries", "error_log_entries"),
            ):
                readings.append(
                    _reading(
                        entity,
                        metric_id,
                        endurance.get(key),
                        observed_at=timestamp,
                        source=str(endurance.get("source") or "SMART/NVMe"),
                        interval=self.health_interval_seconds,
                    )
                )
        for pool in telemetry["pools"]:
            entity = EntityReading(
                "pool",
                str(pool.get("id")),
                str(pool.get("name") or pool.get("id")),
                labels={"type": str(pool.get("type") or "Not reported")},
                topology={"host": host.stable_id},
            )
            metrics = pool.get("metrics") if isinstance(pool.get("metrics"), Mapping) else {}
            for metric_id, key in (
                ("io.read.bytes_per_second", "read_bytes_per_second"),
                ("io.write.bytes_per_second", "write_bytes_per_second"),
                ("io.read.iops", "read_iops"),
                ("io.write.iops", "write_iops"),
                ("io.read.latency", "read_wait_ms"),
                ("io.write.latency", "write_wait_ms"),
                ("io.utilization", "utilization_percent"),
            ):
                readings.append(
                    _reading(
                        entity,
                        metric_id,
                        metrics.get(key),
                        observed_at=timestamp,
                        source="mapped Linux block counters",
                        interval=self.interval_seconds,
                    )
                )
            readings.append(
                _reading(
                    entity,
                    "io.write.today",
                    pool.get("writes_today_bytes"),
                    observed_at=timestamp,
                    source="reset-safe local counter baseline",
                    interval=self.interval_seconds,
                )
            )
            readings.append(
                _reading(
                    entity,
                    "io.read.today",
                    pool.get("reads_today_bytes"),
                    observed_at=timestamp,
                    source="reset-safe local counter baseline",
                    interval=self.interval_seconds,
                )
            )
            inventory_pool = next(
                (item for item in pool_items if item.get("id") == pool.get("id")), {}
            )
            for metric_id, key in (
                ("capacity.total", "total_bytes"),
                ("capacity.used", "used_bytes"),
                ("capacity.free", "free_bytes"),
                ("pool.scrub.progress", "progress_percent"),
            ):
                readings.append(
                    _reading(
                        entity,
                        metric_id,
                        _reported_number(inventory_pool.get(key)),
                        observed_at=timestamp,
                        source="pool provider",
                        interval=max(30, self.interval_seconds),
                    )
                )
            total = inventory_pool.get("total_bytes")
            used = inventory_pool.get("used_bytes")
            utilization = (
                used / total * 100
                if isinstance(total, int) and total > 0 and isinstance(used, int)
                else None
            )
            readings.append(
                _reading(
                    entity,
                    "capacity.utilization",
                    utilization,
                    observed_at=timestamp,
                    source="pool provider",
                    interval=max(30, self.interval_seconds),
                )
            )
            if str(inventory_pool.get("type", "")).casefold() == "mergerfs":
                branches = [
                    str(item)
                    for item in inventory_pool.get("branches", [])
                    if isinstance(item, str)
                ]
                imbalance, member_capacity = mergerfs_imbalance(branches)
                readings.append(
                    _reading(
                        EntityReading(
                            "mergerfs_pool",
                            str(pool.get("id")),
                            str(pool.get("name") or pool.get("id")),
                            labels={"members": str(len(branches))},
                            topology={"host": host.stable_id},
                        ),
                        "mergerfs.distribution.imbalance",
                        imbalance,
                        observed_at=timestamp,
                        source="member statvfs",
                        interval=max(60, self.interval_seconds),
                        labels={
                            "formula": "max_member_utilization_minus_min_member_utilization",
                            "reported_members": str(len(member_capacity)),
                        },
                    )
                )
            readings.append(
                _reading(
                    entity,
                    "health.overall",
                    _health_value(inventory_pool.get("status")),
                    observed_at=timestamp,
                    source="pool provider",
                    interval=max(30, self.interval_seconds),
                )
            )
        controllers_document = inventory.get("controllers")
        controller_items = (
            controllers_document.get("items", [])
            if isinstance(controllers_document, Mapping)
            else []
        )
        for controller in controller_items:
            if not isinstance(controller, Mapping):
                continue
            provider = str(controller.get("provider") or "controller provider")
            stable_id = ":".join(
                (
                    "controller",
                    provider,
                    str(controller.get("serial") or controller.get("id") or "not-reported"),
                )
            )
            entity = EntityReading(
                "controller",
                stable_id,
                str(controller.get("model") or stable_id),
                labels={"provider": provider},
                topology={"host": host.stable_id},
            )
            readings.extend(
                [
                    _reading(
                        entity,
                        "health.overall",
                        _health_value(controller.get("health")),
                        observed_at=timestamp,
                        source=provider,
                        interval=self.health_interval_seconds,
                    ),
                    _reading(
                        entity,
                        "controller.temperature",
                        controller.get("temperature_c"),
                        observed_at=timestamp,
                        source=provider,
                        interval=self.health_interval_seconds,
                    ),
                    _reading(
                        entity,
                        "controller.cache.hit_ratio",
                        controller.get("cache_hit_ratio"),
                        observed_at=timestamp,
                        source=provider,
                        interval=self.health_interval_seconds,
                    ),
                    _reading(
                        entity,
                        "controller.cache.state",
                        _health_value(controller.get("cache_state")),
                        observed_at=timestamp,
                        source=provider,
                        interval=self.health_interval_seconds,
                    ),
                    _reading(
                        entity,
                        "controller.battery.state",
                        _health_value(controller.get("battery_state")),
                        observed_at=timestamp,
                        source=provider,
                        interval=self.health_interval_seconds,
                    ),
                ]
            )
        topology = inventory.get("topology")
        nodes = topology.get("nodes", []) if isinstance(topology, Mapping) else []
        for node in nodes:
            if not isinstance(node, Mapping) or node.get("kind") != "enclosure":
                continue
            stable_id = str(node.get("id") or "")
            if not stable_id:
                continue
            entity = EntityReading(
                "enclosure",
                stable_id,
                str(node.get("label") or stable_id),
                labels={"address": str(node.get("address") or "Not reported")},
                topology={"host": host.stable_id},
            )
            readings.append(
                _reading(
                    entity,
                    "health.overall",
                    _health_value(node.get("status")),
                    observed_at=timestamp,
                    source="SES/sysfs topology",
                    interval=self.health_interval_seconds,
                )
            )
            for metric_id, key in (
                ("enclosure.temperature", "temperature_c"),
                ("enclosure.fan.speed", "fan_rpm"),
                ("enclosure.path.redundancy", "active_paths"),
            ):
                readings.append(
                    _reading(
                        entity,
                        metric_id,
                        node.get(key),
                        observed_at=timestamp,
                        source=str(node.get("provider") or "SES/sysfs topology"),
                        interval=self.health_interval_seconds,
                    )
                )
        return readings
