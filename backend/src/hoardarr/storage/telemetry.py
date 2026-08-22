from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psutil

CounterProvider = Callable[[], Mapping[str, Any]]
Clock = Callable[[], float]
SmartReader = Callable[[str, int | None], dict[str, Any]]


def _number(value: object) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _counter_document(counter: object) -> dict[str, int]:
    return {
        name: max(0, _number(getattr(counter, name, 0)) or 0)
        for name in (
            "read_count",
            "write_count",
            "read_bytes",
            "write_bytes",
            "read_time",
            "write_time",
            "busy_time",
        )
    }


def _delta(current: Mapping[str, int], previous: Mapping[str, int], name: str) -> int:
    return max(0, current.get(name, 0) - previous.get(name, 0))


def _rate(value: int, seconds: float) -> float | None:
    return round(value / seconds, 2) if seconds > 0 else None


def _wait(milliseconds: int, operations: int) -> float | None:
    return round(milliseconds / operations, 2) if operations > 0 else None


def _drive_metrics(
    current: Mapping[str, int], previous: Mapping[str, int] | None, seconds: float
) -> dict[str, float | int | None]:
    if previous is None or seconds <= 0:
        return {
            "read_bytes_per_second": None,
            "write_bytes_per_second": None,
            "read_iops": None,
            "write_iops": None,
            "read_wait_ms": None,
            "write_wait_ms": None,
            "utilization_percent": None,
        }
    reads = _delta(current, previous, "read_count")
    writes = _delta(current, previous, "write_count")
    return {
        "read_bytes_per_second": _rate(_delta(current, previous, "read_bytes"), seconds),
        "write_bytes_per_second": _rate(_delta(current, previous, "write_bytes"), seconds),
        "read_iops": _rate(reads, seconds),
        "write_iops": _rate(writes, seconds),
        "read_wait_ms": _wait(_delta(current, previous, "read_time"), reads),
        "write_wait_ms": _wait(_delta(current, previous, "write_time"), writes),
        "utilization_percent": round(
            min(100.0, _delta(current, previous, "busy_time") / (seconds * 10)), 2
        ),
    }


def _smartctl_endurance(device: str, logical_sector_bytes: int | None) -> dict[str, Any]:
    executable = shutil.which("smartctl", path="/usr/sbin:/usr/bin:/sbin:/bin")
    if executable is None:
        return {"lifetime_writes_bytes": None, "remaining_percent": None, "source": None}
    try:
        result = subprocess.run(
            [executable, "-a", "-j", device],
            check=False,
            shell=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=5,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"},
        )
        document = json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return {"lifetime_writes_bytes": None, "remaining_percent": None, "source": None}
    return _parse_smart_endurance(document, logical_sector_bytes)


def _parse_smart_endurance(document: object, logical_sector_bytes: int | None) -> dict[str, Any]:
    if not isinstance(document, Mapping):
        return {"lifetime_writes_bytes": None, "remaining_percent": None, "source": None}

    nvme = document.get("nvme_smart_health_information_log")
    if isinstance(nvme, Mapping):
        units = _number(nvme.get("data_units_written"))
        read_units = _number(nvme.get("data_units_read"))
        used = _number(nvme.get("percentage_used"))
        critical = _number(nvme.get("critical_warning"))
        result = {
            "lifetime_writes_bytes": units * 512_000 if units is not None else None,
            "remaining_percent": max(0, min(100, 100 - used)) if used is not None else None,
            "source": "NVMe SMART",
        }
        optional = {
            "lifetime_reads_bytes": read_units * 512_000 if read_units is not None else None,
            "percentage_used": used,
            "available_spare": _number(nvme.get("available_spare")),
            "available_spare_threshold": _number(nvme.get("available_spare_threshold")),
            "media_errors": _number(nvme.get("media_errors")),
            "unsafe_shutdowns": _number(nvme.get("unsafe_shutdowns")),
            "power_cycles": _number(nvme.get("power_cycles")),
            "controller_busy_time": _number(nvme.get("controller_busy_time")),
            "error_log_entries": _number(nvme.get("num_err_log_entries")),
            "temperature": _number(nvme.get("temperature")),
            "critical_warning": critical,
            "health_status": "critical" if critical else "healthy" if critical == 0 else None,
        }
        result.update({name: value for name, value in optional.items() if value is not None})
        return result

    table = document.get("ata_smart_attributes")
    rows = table.get("table") if isinstance(table, Mapping) else None
    lifetime_writes: int | None = None
    remaining: int | None = None
    attributes: dict[str, int] = {}
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            name = str(row.get("name", "")).casefold()
            raw = row.get("raw")
            raw_value = _number(raw.get("value")) if isinstance(raw, Mapping) else None
            normalized = _number(row.get("value"))
            if raw_value is not None:
                attributes[name] = raw_value
                if name in {"total_lbas_written", "total_host_sector_write"}:
                    lifetime_writes = raw_value * (logical_sector_bytes or 512)
                elif name in {"host_writes_32mib", "host_writes_32mb"}:
                    lifetime_writes = raw_value * 32 * 1024 * 1024
                elif name in {"lifetime_writes_gib", "host_writes_gib"}:
                    lifetime_writes = raw_value * 1024 * 1024 * 1024
            if normalized is not None and name in {
                "media_wearout_indicator",
                "percent_lifetime_remain",
                "ssd_life_left",
            }:
                remaining = max(0, min(100, normalized))
    return {
        "lifetime_writes_bytes": lifetime_writes,
        "lifetime_reads_bytes": None,
        "remaining_percent": remaining,
        "percentage_used": 100 - remaining if remaining is not None else None,
        "temperature": attributes.get("temperature_celsius")
        or attributes.get("airflow_temperature_cel"),
        "power_on_hours": attributes.get("power_on_hours"),
        "reallocated_sectors": attributes.get("reallocated_sector_ct"),
        "pending_sectors": attributes.get("current_pending_sector"),
        "uncorrectable_sectors": attributes.get("offline_uncorrectable"),
        "interface_crc_errors": attributes.get("udma_crc_error_count"),
        "command_timeouts": attributes.get("command_timeout"),
        "health_status": (
            "healthy"
            if isinstance(document.get("smart_status"), Mapping)
            and document["smart_status"].get("passed") is True
            else "critical"
            if isinstance(document.get("smart_status"), Mapping)
            and document["smart_status"].get("passed") is False
            else None
        ),
        "source": (
            "ATA SMART"
            if lifetime_writes is not None or remaining is not None or attributes
            else None
        ),
    }


class StorageTelemetrySampler:
    def __init__(
        self,
        *,
        state_path: Path = Path("/var/lib/hoardarr/storage-telemetry.json"),
        counters: CounterProvider | None = None,
        clock: Clock = time.time,
        smart_reader: SmartReader = _smartctl_endurance,
    ) -> None:
        self.state_path = state_path
        self.counters = counters or (lambda: psutil.disk_io_counters(perdisk=True, nowrap=True))
        self.clock = clock
        self.smart_reader = smart_reader
        self.lock = threading.Lock()
        self.previous: dict[str, dict[str, int]] = {}
        self.previous_at: float | None = None
        self.day = ""
        self.baselines: dict[str, int] = {}
        self.accrued: dict[str, int] = {}
        self.last_seen: dict[str, int] = {}
        self.last_persisted = 0.0
        self.smart_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._load_state()

    def _load_state(self) -> None:
        try:
            document = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return
        if not isinstance(document, Mapping):
            return
        self.day = str(document.get("day", ""))
        for field, target in (
            ("baselines", self.baselines),
            ("accrued", self.accrued),
            ("last_seen", self.last_seen),
        ):
            value = document.get(field)
            if isinstance(value, Mapping):
                target.update(
                    {
                        str(name): count
                        for name, raw in value.items()
                        if (count := _number(raw)) is not None
                    }
                )

    def _persist(self, now: float) -> None:
        if now - self.last_persisted < 300:
            return
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = self.state_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(
                    {
                        "day": self.day,
                        "baselines": self.baselines,
                        "accrued": self.accrued,
                        "last_seen": self.last_seen,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            os.chmod(temporary, 0o600)
            temporary.replace(self.state_path)
            self.last_persisted = now
        except OSError:
            pass

    def _today_writes(self, name: str, current: int, today: str) -> int:
        if self.day != today:
            self.day = today
            self.baselines = {}
            self.accrued = {}
            self.last_seen = {}
        baseline = self.baselines.setdefault(name, current)
        previous_seen = self.last_seen.get(name)
        if current < baseline or (previous_seen is not None and current < previous_seen):
            self.accrued[name] = self.accrued.get(name, 0) + max(0, (previous_seen or 0) - baseline)
            self.baselines[name] = current
            baseline = current
        self.last_seen[name] = current
        return self.accrued.get(name, 0) + max(0, current - baseline)

    def _endurance(
        self, devices: list[tuple[str, int | None]], now: float
    ) -> dict[str, dict[str, Any]]:
        stale = [
            (device, sector)
            for device, sector in devices
            if now - self.smart_cache.get(device, (0, {}))[0] >= 300
        ]
        with ThreadPoolExecutor(max_workers=min(4, len(stale) or 1)) as pool:
            futures = {
                pool.submit(self.smart_reader, device, sector): device
                for device, sector in stale[:8]
            }
            for future, device in futures.items():
                try:
                    self.smart_cache[device] = (now, future.result())
                except Exception:
                    self.smart_cache[device] = (
                        now,
                        {"lifetime_writes_bytes": None, "remaining_percent": None, "source": None},
                    )
        return {device: self.smart_cache.get(device, (0, {}))[1] for device, _sector in devices}

    @staticmethod
    def _counter_name(device_name: str, counters: Mapping[str, Any]) -> str | None:
        name = Path(device_name).name
        if name in counters:
            return name
        # Partition names reduce to their physical block device when Linux only
        # exposes counters for the parent disk.
        candidates = [
            candidate
            for candidate in counters
            if name.startswith(candidate)
            and (name[len(candidate) :].isdigit() or name[len(candidate) :].startswith("p"))
        ]
        return max(candidates, key=len) if candidates else None

    @staticmethod
    def _partition_devices() -> list[tuple[str, str]]:
        try:
            return [(item.mountpoint, item.device) for item in psutil.disk_partitions(all=True)]
        except OSError:
            return []

    def _pool_counter_names(
        self, pool: Mapping[str, Any], counters: Mapping[str, Any]
    ) -> list[str]:
        requested = [str(item) for item in pool.get("device_names", []) if item]
        branches = [str(item) for item in pool.get("branches", []) if item]
        partitions = self._partition_devices()
        for branch in branches:
            matches = [
                (mountpoint, device)
                for mountpoint, device in partitions
                if branch == mountpoint or branch.startswith(mountpoint.rstrip("/") + "/")
            ]
            if matches:
                requested.append(max(matches, key=lambda item: len(item[0]))[1])
        names = {
            resolved
            for requested_name in requested
            if (resolved := self._counter_name(requested_name, counters)) is not None
        }
        return sorted(names)

    def sample(
        self,
        *,
        hardware_snapshot: Mapping[str, Any] | None,
        pools: list[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        with self.lock:
            now = self.clock()
            today = datetime.fromtimestamp(now, UTC).date().isoformat()
            raw = self.counters() or {}
            current = {str(name): _counter_document(counter) for name, counter in raw.items()}
            seconds = max(0.0, now - self.previous_at) if self.previous_at is not None else 0.0
            disks = (
                hardware_snapshot.get("disks", []) if isinstance(hardware_snapshot, Mapping) else []
            )
            disk_documents = [disk for disk in disks if isinstance(disk, Mapping)]
            storage_disks = [disk for disk in disk_documents if disk.get("system_disk") is not True]
            system_names = {
                str(disk.get("kernel_name"))
                for disk in disk_documents
                if disk.get("system_disk") is True and disk.get("kernel_name")
            }
            smart_devices = [
                (
                    str(disk.get("kernel_path")),
                    _number((disk.get("sector_sizes") or {}).get("logical_bytes"))
                    if isinstance(disk.get("sector_sizes"), Mapping)
                    else None,
                )
                for disk in storage_disks
                if disk.get("rotational") is False and isinstance(disk.get("kernel_path"), str)
            ]
            endurance = self._endurance(smart_devices, now)
            drives: list[dict[str, Any]] = []
            included_names: set[str] = set()
            for disk in storage_disks:
                name = str(disk.get("kernel_name") or "")
                if not name or name not in current:
                    continue
                included_names.add(name)
                counters = current[name]
                metrics = _drive_metrics(counters, self.previous.get(name), seconds)
                device = str(disk.get("kernel_path") or f"/dev/{name}")
                identity = disk.get("identity") if isinstance(disk.get("identity"), Mapping) else {}
                drives.append(
                    {
                        "id": str(disk.get("id") or name),
                        "device": device,
                        "device_name": name,
                        "model": " ".join(
                            str(value).strip()
                            for value in (disk.get("vendor"), disk.get("model"))
                            if value
                        ),
                        "serial": identity.get("serial"),
                        "rotational": disk.get("rotational"),
                        "system_disk": disk.get("system_disk") is True,
                        "metrics": metrics,
                        "writes_today_bytes": self._today_writes(
                            f"write:{name}", counters["write_bytes"], today
                        ),
                        "reads_today_bytes": self._today_writes(
                            f"read:{name}", counters["read_bytes"], today
                        ),
                        "os_write_bytes_since_boot": counters["write_bytes"],
                        "os_read_bytes_since_boot": counters["read_bytes"],
                        "os_busy_time_ms_since_boot": counters["busy_time"],
                        "endurance": endurance.get(
                            device,
                            {
                                "lifetime_writes_bytes": None,
                                "remaining_percent": None,
                                "source": None,
                            },
                        ),
                    }
                )

            aggregate_current = {
                field: sum(
                    values[field] for name, values in current.items() if name in included_names
                )
                for field in _counter_document(object())
            }
            aggregate_previous = {
                field: sum(
                    self.previous.get(name, {}).get(field, current[name][field])
                    for name in included_names
                )
                for field in aggregate_current
            }
            summary = _drive_metrics(
                aggregate_current,
                aggregate_previous if self.previous_at is not None else None,
                seconds,
            )
            summary["writes_today_bytes"] = sum(item["writes_today_bytes"] for item in drives)
            summary["sample_seconds"] = round(seconds, 3) if seconds else None

            pool_documents = []
            for pool in pools or []:
                names = [
                    name
                    for name in self._pool_counter_names(pool, current)
                    if name not in system_names
                ]
                pool_current = {
                    field: sum(current[name][field] for name in names)
                    for field in _counter_document(object())
                }
                pool_previous = {
                    field: sum(
                        self.previous.get(name, {}).get(field, current[name][field])
                        for name in names
                    )
                    for field in pool_current
                }
                pool_documents.append(
                    {
                        "id": pool.get("id"),
                        "name": pool.get("name"),
                        "type": pool.get("type"),
                        "writes_today_bytes": sum(
                            self._today_writes(f"write:{name}", current[name]["write_bytes"], today)
                            for name in names
                        )
                        if names
                        else None,
                        "reads_today_bytes": sum(
                            self._today_writes(f"read:{name}", current[name]["read_bytes"], today)
                            for name in names
                        )
                        if names
                        else None,
                        "metrics": _drive_metrics(
                            pool_current,
                            pool_previous if self.previous_at is not None else None,
                            seconds,
                        )
                        if names
                        else None,
                        "device_names": names,
                        "status": "available" if names else "not_reported",
                    }
                )

            for drive in drives:
                drive["pool_ids"] = [
                    str(pool["id"])
                    for pool in pool_documents
                    if drive["device_name"] in pool["device_names"]
                ]

            self.previous = current
            self.previous_at = now
            self._persist(now)
            return {
                "captured_at": datetime.fromtimestamp(now, UTC),
                "source": "linux_block_counters",
                "summary": summary,
                "drives": drives,
                "pools": pool_documents,
            }


storage_telemetry = StorageTelemetrySampler()
