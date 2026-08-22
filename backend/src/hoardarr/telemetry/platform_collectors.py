from __future__ import annotations

import contextlib
import json
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hoardarr.telemetry.collectors import ResetSafeCounterRates, _health_value, _reading
from hoardarr.telemetry.samples import EntityReading, MetricReading

MAX_PROVIDER_OUTPUT = 4 * 1024 * 1024


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None


def _command(program: str, args: list[str], *, timeout: int = 10) -> str | None:
    executable = shutil.which(program)
    if not executable:
        return None
    try:
        process = subprocess.Popen(
            [executable, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
        )
    except OSError:
        return None
    output = bytearray()
    exceeded = threading.Event()

    def drain() -> None:
        assert process.stdout is not None
        while chunk := process.stdout.read(64 * 1024):
            remaining = MAX_PROVIDER_OUTPUT + 1 - len(output)
            if remaining > 0:
                output.extend(chunk[:remaining])
            if len(output) > MAX_PROVIDER_OUTPUT:
                exceeded.set()
                with contextlib.suppress(OSError):
                    process.kill()
                return

    reader = threading.Thread(target=drain, name="telemetry-command-output", daemon=True)
    reader.start()
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        reader.join(timeout=1)
        return None
    reader.join(timeout=1)
    if reader.is_alive():
        process.kill()
        process.wait()
        return None
    if returncode != 0 or exceeded.is_set():
        return None
    return output.decode("utf-8", errors="replace")


def parse_zpool_list(output: str) -> list[dict[str, Any]]:
    if len(output) > MAX_PROVIDER_OUTPUT:
        raise ValueError("zpool output is too large")
    rows = []
    for line in output.splitlines():
        fields = line.split("\t")
        if len(fields) != 6:
            continue
        name, size, allocated, free, fragmentation, health = fields
        if not name or not all(value.isdigit() for value in (size, allocated, free)):
            continue
        rows.append(
            {
                "name": name[:256],
                "size": int(size),
                "allocated": int(allocated),
                "free": int(free),
                "fragmentation": (
                    float(fragmentation.rstrip("%"))
                    if fragmentation.rstrip("%").isdigit()
                    else None
                ),
                "health": health[:128],
            }
        )
    return rows


def parse_zpool_status_errors(output: str) -> dict[str, dict[str, int]]:
    """Parse only the authoritative READ/WRITE/CKSUM columns from zpool status -p."""
    if len(output) > MAX_PROVIDER_OUTPUT:
        raise ValueError("zpool output is too large")
    pool_name: str | None = None
    in_config = False
    result: dict[str, dict[str, int]] = {}
    for raw in output.splitlines():
        stripped = raw.strip()
        if stripped.startswith("pool:"):
            pool_name = stripped.split(":", 1)[1].strip()[:256]
        elif stripped.startswith("config:"):
            in_config = True
        elif in_config and stripped.startswith("errors:"):
            in_config = False
        elif in_config and pool_name:
            fields = stripped.split()
            if len(fields) >= 5 and fields[0] == pool_name:
                try:
                    result[pool_name] = {
                        "read": max(0, int(fields[-3])),
                        "write": max(0, int(fields[-2])),
                        "checksum": max(0, int(fields[-1])),
                    }
                except ValueError:
                    continue
    return result


def read_sas_phys(sys_root: Path) -> list[dict[str, Any]]:
    """Read Linux SAS transport counters without guessing absent attributes."""
    root = sys_root / "class/sas_phy"
    if not root.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for phy in sorted(root.glob("phy-*")):
        sas_address = _read(phy / "sas_address") or _read(phy / "device/sas_address")
        identifier = _read(phy / "phy_identifier") or phy.name

        def integer(name: str, current: Path = phy) -> int | None:
            value = _read(current / name)
            try:
                return max(0, int(value)) if value is not None else None
            except ValueError:
                return None

        rows.append(
            {
                "id": f"sas:{sas_address or phy.name}:{identifier}",
                "name": phy.name,
                "sas_address": sas_address,
                "invalid_dwords": integer("invalid_dword_count"),
                "disparity_errors": integer("running_disparity_error_count"),
                "loss_of_sync": integer("loss_of_dword_sync_count"),
                "reset_problems": integer("phy_reset_problem_count"),
                "negotiated_rate": _read(phy / "negotiated_linkrate"),
                "maximum_rate": _read(phy / "maximum_linkrate_hw"),
            }
        )
    return rows


def parse_ses_metrics(output: str) -> dict[str, Any]:
    """Normalize explicitly reported sg_ses JSON sensor elements."""
    if len(output) > MAX_PROVIDER_OUTPUT:
        raise ValueError("SES output is too large")
    try:
        document = json.loads(output)
    except json.JSONDecodeError as exc:
        raise ValueError("SES output is not valid JSON") from exc
    if not isinstance(document, Mapping) or not isinstance(document.get("elements"), list):
        raise ValueError("SES elements are missing")
    temperatures: list[float] = []
    fans: list[int] = []
    psu_states: list[str] = []
    voltages: list[float] = []
    locate = False
    fault = False
    expander_states: list[str] = []
    slots = 0
    for raw in document["elements"]:
        if not isinstance(raw, Mapping):
            continue
        kind = str(raw.get("element_type") or "").casefold()
        status = str(raw.get("status") or "Not reported")[:128]
        if "temperature" in kind and isinstance(raw.get("temperature_c"), (int, float)):
            temperatures.append(float(raw["temperature_c"]))
        elif "cooling" in kind and isinstance(raw.get("speed_rpm"), (int, float)):
            fans.append(max(0, int(raw["speed_rpm"])))
        elif "power supply" in kind:
            psu_states.append(status)
        elif "voltage" in kind and isinstance(raw.get("voltage_v"), (int, float)):
            voltages.append(float(raw["voltage_v"]))
        elif "expander" in kind:
            expander_states.append(status)
        elif kind in {"array device slot", "device slot"}:
            slots += 1
            locate = locate or raw.get("identify") is True
            fault = fault or raw.get("fault") is True
    descriptor = document.get("enclosure_descriptor")
    logical_id = document.get("enclosure_logical_identifier") or document.get(
        "primary_enclosure_logical_identifier"
    )
    if (
        not isinstance(logical_id, str)
        or re.fullmatch(r"(?:0x|naa\.)?[0-9A-Fa-f]{16,64}", logical_id) is None
    ):
        logical_id = None
    return {
        "id": logical_id.casefold() if logical_id else None,
        "descriptor": str(descriptor)[:256]
        if isinstance(descriptor, str) and descriptor
        else "Not reported",
        "health": str(document.get("status") or "Not reported")[:128],
        "temperature_c": max(temperatures) if temperatures else None,
        "fan_rpm": max(fans) if fans else None,
        "fan_count": len(fans),
        "psu_states": psu_states,
        "voltages": voltages,
        "locate": locate,
        "fault": fault,
        "expander_states": expander_states,
        "slot_count": slots,
    }


MD_PROGRESS = re.compile(r"(?:recovery|resync|reshape|check)\s*=\s*([0-9.]+)%")


def parse_mdstat(output: str) -> list[dict[str, Any]]:
    if len(output) > MAX_PROVIDER_OUTPUT:
        raise ValueError("mdstat output is too large")
    lines = output.splitlines()
    arrays = []
    index = 0
    while index < len(lines):
        match = re.match(r"^(md\d+)\s*:\s*(\S+)\s+(raid\d+)\s+(.+)$", lines[index])
        if not match:
            index += 1
            continue
        name, state, level, members = match.groups()
        details = " ".join(lines[index + 1 : index + 3])
        bitmap = re.search(r"\[([U_]+)\]", details)
        marks = bitmap.group(1) if bitmap else ""
        progress = MD_PROGRESS.search(details)
        arrays.append(
            {
                "name": name,
                "state": "degraded" if "_" in marks else state,
                "level": level,
                "active": marks.count("U") if marks else len(re.findall(r"\[\d+\]", members)),
                "failed": marks.count("_") if marks else 0,
                "spare": len(re.findall(r"\(S\)", members)),
                "progress": float(progress.group(1)) if progress else None,
            }
        )
        index += 1
    return arrays


def parse_multipath_json(output: str) -> list[dict[str, Any]]:
    if len(output) > MAX_PROVIDER_OUTPUT:
        raise ValueError("multipath output is too large")
    try:
        document = json.loads(output)
    except json.JSONDecodeError as exc:
        raise ValueError("multipath output is not valid JSON") from exc
    maps = document.get("maps") if isinstance(document, Mapping) else None
    if not isinstance(maps, list):
        raise ValueError("multipath maps are missing")
    result = []
    for item in maps:
        if not isinstance(item, Mapping):
            continue
        paths = item.get("paths")
        path_groups = item.get("path_groups") or item.get("pathgroups")
        active_group: str | None = None
        normalized_paths: list[dict[str, Any]] = []
        if isinstance(path_groups, list):
            grouped_paths: list[Any] = []
            for index, group in enumerate(path_groups):
                if not isinstance(group, Mapping):
                    continue
                candidates = group.get("paths")
                if isinstance(candidates, list):
                    grouped_paths.extend(candidates)
                state = str(group.get("status") or group.get("dm_st") or "").casefold()
                group_identity = str(
                    group.get("group") or group.get("id") or group.get("priority") or index
                )[:128]
                if active_group is None and state in {"active", "enabled", "ready"}:
                    active_group = group_identity
                for path in candidates if isinstance(candidates, list) else []:
                    if isinstance(path, Mapping):
                        normalized_paths.append(
                            {
                                "kernel_name": str(
                                    path.get("dev") or path.get("device") or path.get("name") or ""
                                )[:128],
                                "state": str(
                                    path.get("dm_st")
                                    or path.get("chk_st")
                                    or path.get("state")
                                    or "unknown"
                                ).casefold()[:64],
                                "group": group_identity,
                                "optimized": state in {"active", "enabled", "ready"},
                            }
                        )
            paths = grouped_paths
        if not isinstance(paths, list):
            paths = []
        if not normalized_paths:
            for path in paths:
                if not isinstance(path, Mapping):
                    continue
                normalized_paths.append(
                    {
                        "kernel_name": str(
                            path.get("dev") or path.get("device") or path.get("name") or ""
                        )[:128],
                        "state": str(
                            path.get("dm_st")
                            or path.get("chk_st")
                            or path.get("state")
                            or "unknown"
                        ).casefold()[:64],
                        "group": None,
                        "optimized": None,
                    }
                )
        states = [
            str(path.get("dm_st", "")).casefold() for path in paths if isinstance(path, Mapping)
        ]
        result.append(
            {
                "wwid": str(item.get("uuid") or item.get("wwid") or "")[:512],
                "name": str(item.get("name") or item.get("alias") or "multipath")[:256],
                "active": sum(state in {"active", "ready"} for state in states),
                "failed": sum(state in {"failed", "faulty", "offline"} for state in states),
                "policy": str(item.get("selector") or "Not reported")[:128],
                "active_group": active_group,
                "paths": normalized_paths,
            }
        )
    return [item for item in result if item["wwid"]]


class LinuxStoragePlatformCollector:
    name = "linux-storage-platforms"

    def __init__(
        self,
        *,
        interval_seconds: int = 30,
        proc_root: Path = Path("/proc"),
        sys_root: Path = Path("/sys"),
    ) -> None:
        self.interval_seconds = interval_seconds
        self.proc_root = proc_root
        self.sys_root = sys_root
        self.rates = ResetSafeCounterRates(maximum_elapsed_seconds=interval_seconds * 6)
        self.last_multipath_maps: list[dict[str, Any]] = []

    def collect(self, observed_at: datetime | None = None) -> list[MetricReading]:
        now = observed_at or datetime.now(UTC)
        readings: list[MetricReading] = []
        readings.extend(self._zfs(now))
        readings.extend(self._md(now))
        readings.extend(self._multipath(now))
        readings.extend(self._sas(now))
        readings.extend(self._ses(now))
        readings.extend(self._fc(now))
        return readings

    def _zfs(self, now: datetime) -> list[MetricReading]:
        output = _command(
            "zpool", ["list", "-Hp", "-o", "name,size,allocated,free,fragmentation,health"]
        )
        if output is None:
            return []
        readings = []
        status_output = _command("zpool", ["status", "-p"])
        try:
            error_counters = parse_zpool_status_errors(status_output or "")
        except ValueError:
            error_counters = {}
        for pool in parse_zpool_list(output):
            entity = EntityReading(
                "pool", f"zfs:{pool['name']}", str(pool["name"]), labels={"type": "zfs"}
            )
            for metric_id, key in (
                ("capacity.total", "size"),
                ("capacity.used", "allocated"),
                ("capacity.free", "free"),
                ("pool.fragmentation", "fragmentation"),
            ):
                readings.append(
                    _reading(
                        entity,
                        metric_id,
                        pool[key],
                        observed_at=now,
                        source="zpool list",
                        interval=self.interval_seconds,
                    )
                )
            readings.append(
                _reading(
                    entity,
                    "health.overall",
                    _health_value(pool["health"]),
                    observed_at=now,
                    source="zpool list",
                    interval=self.interval_seconds,
                )
            )
            errors = error_counters.get(str(pool["name"]), {})
            for metric_id, key in (
                ("pool.errors.read", "read"),
                ("pool.errors.write", "write"),
                ("pool.errors.checksum", "checksum"),
            ):
                readings.append(
                    _reading(
                        entity,
                        metric_id,
                        errors.get(key),
                        observed_at=now,
                        source="zpool status -p",
                        interval=self.interval_seconds,
                    )
                )
        arc = self.proc_root / "spl/kstat/zfs/arcstats"
        values: dict[str, int] = {}
        for line in (_read(arc) or "").splitlines():
            fields = line.split()
            if (
                len(fields) >= 3
                and fields[0] in {"size", "hits", "misses"}
                and fields[-1].isdigit()
            ):
                values[fields[0]] = int(fields[-1])
        if values:
            host = EntityReading("host", "host:local", "Hoardarr host")
            readings.append(
                _reading(
                    host,
                    "zfs.arc.size",
                    values.get("size"),
                    observed_at=now,
                    source=str(arc),
                    interval=self.interval_seconds,
                )
            )
            total = values.get("hits", 0) + values.get("misses", 0)
            readings.append(
                _reading(
                    host,
                    "zfs.arc.hit_ratio",
                    values.get("hits", 0) / total if total else None,
                    observed_at=now,
                    source=str(arc),
                    interval=self.interval_seconds,
                )
            )
        return readings

    def _md(self, now: datetime) -> list[MetricReading]:
        content = _read(self.proc_root / "mdstat")
        if content is None:
            return []
        readings = []
        for array in parse_mdstat(content):
            entity = EntityReading(
                "md_array",
                f"md:{array['name']}",
                str(array["name"]),
                labels={"level": str(array["level"])},
            )
            for metric_id, key in (
                ("pool.members.active", "active"),
                ("pool.members.failed", "failed"),
                ("pool.members.spare", "spare"),
                ("pool.rebuild.progress", "progress"),
            ):
                readings.append(
                    _reading(
                        entity,
                        metric_id,
                        array[key],
                        observed_at=now,
                        source="/proc/mdstat",
                        interval=self.interval_seconds,
                    )
                )
            readings.append(
                _reading(
                    entity,
                    "health.overall",
                    _health_value(array["state"]),
                    observed_at=now,
                    source="/proc/mdstat",
                    interval=self.interval_seconds,
                )
            )
        return readings

    def _multipath(self, now: datetime) -> list[MetricReading]:
        output = _command("multipathd", ["show", "maps", "json"])
        if output is None:
            self.last_multipath_maps = []
            return []
        readings = []
        try:
            maps = parse_multipath_json(output)
        except ValueError:
            self.last_multipath_maps = []
            return []
        self.last_multipath_maps = maps
        for item in maps:
            entity = EntityReading(
                "multipath_device",
                f"multipath:{item['wwid']}",
                str(item["name"]),
                labels={"policy": str(item["policy"])},
            )
            readings.append(
                _reading(
                    entity,
                    "multipath.paths.active",
                    item["active"],
                    observed_at=now,
                    source="multipathd",
                    interval=self.interval_seconds,
                    labels=(
                        {"active_group": str(item["active_group"])}
                        if item.get("active_group") is not None
                        else {}
                    ),
                )
            )
            readings.append(
                _reading(
                    entity,
                    "multipath.paths.failed",
                    item["failed"],
                    observed_at=now,
                    source="multipathd",
                    interval=self.interval_seconds,
                )
            )
            state = "degraded" if item["failed"] else "healthy"
            readings.append(
                _reading(
                    entity,
                    "health.overall",
                    state,
                    observed_at=now,
                    source="multipathd",
                    interval=self.interval_seconds,
                )
            )
        return readings

    def _sas(self, now: datetime) -> list[MetricReading]:
        readings: list[MetricReading] = []
        for item in read_sas_phys(self.sys_root):
            entity = EntityReading(
                "storage_path",
                str(item["id"]),
                str(item["name"]),
                labels={
                    "sas_address": str(item["sas_address"] or "Not reported"),
                    "negotiated_link_rate": str(item["negotiated_rate"] or "Not reported"),
                    "maximum_link_rate": str(item["maximum_rate"] or "Not reported"),
                    "loss_of_dword_sync": str(
                        item["loss_of_sync"] if item["loss_of_sync"] is not None else "Not reported"
                    ),
                    "phy_reset_problems": str(
                        item["reset_problems"]
                        if item["reset_problems"] is not None
                        else "Not reported"
                    ),
                },
            )
            for metric_id, key in (
                ("sas.phy.invalid_dwords", "invalid_dwords"),
                ("sas.phy.disparity_errors", "disparity_errors"),
            ):
                readings.append(
                    _reading(
                        entity,
                        metric_id,
                        item[key],
                        observed_at=now,
                        source="Linux SAS transport sysfs",
                        interval=max(300, self.interval_seconds),
                    )
                )
        return readings

    def _ses(self, now: datetime) -> list[MetricReading]:
        root = self.sys_root / "class/enclosure"
        if not root.is_dir() or shutil.which("sg_ses") is None:
            return []
        by_id: dict[str, dict[str, Any]] = {}
        for enclosure in sorted(root.iterdir()):
            generic = next(iter(enclosure.glob("device/scsi_generic/*")), None)
            if generic is None:
                continue
            output = _command("sg_ses", ["--json", f"/dev/{generic.name}"], timeout=15)
            if output is None:
                continue
            try:
                item = parse_ses_metrics(output)
            except ValueError:
                continue
            stable_id = item.get("id")
            if not isinstance(stable_id, str):
                # SES display descriptors and kernel enclosure numbers are not
                # permanent identities. Do not conflate or accumulate entities
                # when the enclosure does not report a logical identifier.
                continue
            entry = by_id.setdefault(stable_id, {**item, "paths": 0})
            entry["paths"] = int(entry["paths"]) + 1
        readings: list[MetricReading] = []
        for stable_id, item in by_id.items():
            entity = EntityReading(
                "enclosure",
                f"ses:{stable_id}"[:512],
                str(item["descriptor"])[:256],
                labels={
                    "fan_count": str(item["fan_count"]),
                    "psu_states": ",".join(item["psu_states"])[:512] or "Not reported",
                    "voltages": ",".join(map(str, item["voltages"]))[:512] or "Not reported",
                    "locate": str(item["locate"]).lower(),
                    "fault": str(item["fault"]).lower(),
                    "expander_states": ",".join(item["expander_states"])[:512] or "Not reported",
                    "slot_count": str(item["slot_count"]),
                },
            )
            for metric_id, value in (
                ("health.overall", _health_value(item["health"])),
                ("enclosure.temperature", item["temperature_c"]),
                ("enclosure.fan.speed", item["fan_rpm"]),
                ("enclosure.path.redundancy", item["paths"]),
            ):
                readings.append(
                    _reading(
                        entity,
                        metric_id,
                        value,
                        observed_at=now,
                        source="sg_ses JSON",
                        interval=max(300, self.interval_seconds),
                    )
                )
        return readings

    def _fc(self, now: datetime) -> list[MetricReading]:
        root = self.sys_root / "class/fc_host"
        if not root.exists():
            return []
        readings = []
        for host in sorted(root.glob("host*")):
            wwpn = _read(host / "port_name")
            entity = EntityReading(
                "fc_port",
                f"fc:{wwpn or host.name}",
                host.name,
                labels={
                    "wwpn": wwpn or "Not reported",
                    "wwnn": _read(host / "node_name") or "Not reported",
                },
            )
            state = _read(host / "port_state")
            readings.append(
                _reading(
                    entity,
                    "health.overall",
                    _health_value(state),
                    observed_at=now,
                    source="FC sysfs",
                    interval=self.interval_seconds,
                )
            )
            speed = _read(host / "speed")
            speed_match = re.search(r"([0-9.]+)\s*Gbit", speed or "", re.I)
            readings.append(
                _reading(
                    entity,
                    "network.link.speed",
                    float(speed_match.group(1)) * 1_000_000_000 if speed_match else None,
                    observed_at=now,
                    source="FC sysfs",
                    interval=self.interval_seconds,
                )
            )
            stats = host / "statistics"
            counters = {
                name: int(value)
                for name in (
                    "rx_words",
                    "tx_words",
                    "invalid_crc_count",
                    "link_failure_count",
                    "loss_of_signal_count",
                    "loss_of_sync_count",
                )
                if (value := _read(stats / name)) is not None and value.isdigit()
            }
            rates = self.rates.update(
                host.name,
                identity=wwpn or host.name,
                timestamp=time.monotonic(),
                counters={
                    "rx": counters.get("rx_words", 0) * 4,
                    "tx": counters.get("tx_words", 0) * 4,
                },
            )
            for metric_id, value in (
                ("network.receive.bytes_per_second", rates.get("rx")),
                ("network.transmit.bytes_per_second", rates.get("tx")),
                ("network.errors", counters.get("invalid_crc_count")),
                ("fc.link.failures", counters.get("link_failure_count")),
                ("fc.loss.of.signal", counters.get("loss_of_signal_count")),
                ("fc.loss.of.sync", counters.get("loss_of_sync_count")),
            ):
                readings.append(
                    _reading(
                        entity,
                        metric_id,
                        value,
                        observed_at=now,
                        source="FC sysfs",
                        interval=self.interval_seconds,
                    )
                )
        return readings
