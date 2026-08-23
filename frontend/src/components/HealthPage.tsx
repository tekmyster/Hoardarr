import { useCallback, useEffect, useMemo, useState } from "react";
import { api, drivesFromSnapshot } from "../api/client";
import type {
  ConnectivityServiceDocument,
  HardwareSnapshot,
  StorageInventory,
  StorageTelemetryDocument,
} from "../types";
import { Card, Notice, Spinner, StatusBadge } from "./ui";

const REFRESH_MS = 15_000;

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : "Health data could not be loaded.";
}

function numberLabel(value: number | null, suffix = ""): string {
  return value === null ? "Not reported" : `${value.toLocaleString()}${suffix}`;
}

function bytesLabel(value: number | null): string {
  if (value === null) return "Not reported";
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  let amount = value;
  let index = 0;
  while (amount >= 1000 && index < units.length - 1) {
    amount /= 1000;
    index += 1;
  }
  return `${amount.toFixed(index > 2 ? 2 : 1)} ${units[index]}`;
}

export function HealthPage() {
  const [snapshot, setSnapshot] = useState<HardwareSnapshot | null>(null);
  const [inventory, setInventory] = useState<StorageInventory | null>(null);
  const [telemetry, setTelemetry] = useState<StorageTelemetryDocument | null>(null);
  const [services, setServices] = useState<ConnectivityServiceDocument[]>([]);
  const [errors, setErrors] = useState<string[]>([]);
  const [busy, setBusy] = useState(true);

  const refresh = useCallback(async () => {
    const results = await Promise.allSettled([
      api.latestHardwareSnapshot(),
      api.storageInventory(),
      api.storageTelemetry(),
      api.connectivityServices(),
    ] as const);
    const nextErrors: string[] = [];
    const [snapshotResult, inventoryResult, telemetryResult, servicesResult] = results;
    if (snapshotResult.status === "fulfilled") setSnapshot(snapshotResult.value);
    else nextErrors.push(errorText(snapshotResult.reason));
    if (inventoryResult.status === "fulfilled") setInventory(inventoryResult.value);
    else nextErrors.push(errorText(inventoryResult.reason));
    if (telemetryResult.status === "fulfilled") setTelemetry(telemetryResult.value);
    else nextErrors.push(errorText(telemetryResult.reason));
    if (servicesResult.status === "fulfilled") setServices(servicesResult.value);
    else nextErrors.push(errorText(servicesResult.reason));
    setErrors([...new Set(nextErrors)]);
    setBusy(false);
  }, []);

  useEffect(() => {
    let stopped = false;
    let timer: number | undefined;
    async function poll(): Promise<void> {
      await refresh();
      if (!stopped) timer = window.setTimeout(() => void poll(), REFRESH_MS);
    }
    void poll();
    return () => {
      stopped = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [refresh]);

  const drives = useMemo(() => snapshot ? drivesFromSnapshot(snapshot) : [], [snapshot]);
  const endurance = useMemo(() => new Map(
    (telemetry?.drives ?? []).map((drive) => [drive.id, drive.endurance] as const),
  ), [telemetry]);
  const alerts = useMemo(() => {
    const items: string[] = [];
    drives.forEach((drive) => {
      if (drive.healthStatus === "critical" || drive.healthStatus === "warning") {
        items.push(`${drive.vendor} ${drive.model} (${drive.serial}) reports ${drive.healthStatus}.`);
      }
      drive.tests.filter((test) => test.status === "failed").forEach((test) => {
        items.push(`${drive.vendor} ${drive.model} (${drive.serial}) failed ${test.label}.`);
      });
    });
    (inventory?.pools.items ?? []).forEach((pool) => {
      if (/degrad|fault|fail|offline|unavail/i.test(pool.status)) {
        items.push(`${pool.name} reports ${pool.status}.`);
      }
    });
    (inventory?.controllers?.items ?? []).forEach((controller) => {
      if (controller.health === "failed" || controller.health === "needs_attention") {
        items.push(`${controller.model} controller reports ${controller.health}.`);
      }
    });
    services.filter((service) => service.status === "error").forEach((service) => {
      items.push(`${service.name} (${service.protocol.toUpperCase()}) needs attention.`);
    });
    return items;
  }, [drives, inventory, services]);

  return <div className="health-page">
    <div className="section-actions">
      <button className="button button-secondary" type="button" disabled={busy} onClick={() => { setBusy(true); void refresh(); }}>Refresh</button>
    </div>
    {errors.map((error) => <Notice key={error} tone="warning" title="Some health data is unavailable">{error}</Notice>)}
    {busy && !snapshot && !inventory ? <Spinner label="Loading health" /> : <>
      <Card title="Alerts" description="Current drive, pool, and storage-access warnings.">
        {alerts.length ? <ul className="health-alert-list">{alerts.map((alert) => <li key={alert}>{alert}</li>)}</ul> : <div className="empty-state compact-empty"><h3>No reported alerts</h3><p>Missing health data is shown separately and is not treated as healthy.</p></div>}
      </Card>
      <Card title="Drives" description={snapshot ? `Last scan ${new Date(snapshot.captured_at).toLocaleString()}` : "No hardware scan is available."}>
        {!snapshot ? <div className="empty-state compact-empty"><h3>No hardware scan</h3><p>Run storage discovery to load drive health.</p></div> : !drives.length ? <div className="empty-state compact-empty"><h3>No data drives detected</h3><p>The system drive is intentionally excluded.</p></div> : <div className="table-scroll"><table className="data-table"><thead><tr><th>Drive</th><th>Health</th><th>Temperature</th><th>Power-on hours</th><th>Lifetime writes</th><th>Endurance remaining</th><th>Latest test</th></tr></thead><tbody>{drives.map((drive) => {
          const temperature = drive.metrics.find((metric) => metric.name === "temperature" || metric.name === "temperature_celsius");
          const power = drive.metrics.find((metric) => metric.name === "power_on_hours");
          const driveEndurance = endurance.get(drive.id);
          const latestTest = drive.tests[drive.tests.length - 1];
          return <tr key={drive.id}><td><strong>{drive.vendor} {drive.model}</strong><small className="cell-detail">{drive.serial}</small></td><td><StatusBadge status={drive.healthStatus === "unknown" ? "Not reported" : drive.healthStatus} /></td><td>{temperature?.available && typeof temperature.value === "number" ? `${temperature.value} °C` : "Not reported"}</td><td>{power?.available && typeof power.value === "number" ? numberLabel(power.value, " h") : "Not reported"}</td><td>{bytesLabel(driveEndurance?.lifetime_writes_bytes ?? null)}</td><td>{numberLabel(driveEndurance?.remaining_percent ?? null, "%")}</td><td>{latestTest ? <><StatusBadge status={latestTest.status} /><small className="cell-detail">{latestTest.label}</small></> : "Not reported"}</td></tr>;
        })}</tbody></table></div>}
      </Card>
      <Card title="Controllers and enclosures" description="Health reported by installed controller and enclosure tools.">
        {!inventory?.controllers?.items.length && !inventory?.enclosures?.items.length ? <div className="empty-state compact-empty"><h3>Not reported</h3><p>No supported controller or enclosure health provider returned data.</p></div> : <div className="table-scroll"><table className="data-table"><thead><tr><th>Hardware</th><th>Provider</th><th>Health</th><th>Temperature</th><th>Cooling</th><th>Power / voltage</th></tr></thead><tbody>{inventory.controllers.items.map((controller) => <tr key={`${controller.provider}:${controller.id}`}><td><strong>{controller.model}</strong><small className="cell-detail">{controller.serial ?? "Not reported"}</small></td><td>{controller.provider}</td><td><StatusBadge status={controller.health} /></td><td>Not reported</td><td>Not reported</td><td>Not reported</td></tr>)}{inventory.enclosures.items.map((enclosure) => <tr key={`${enclosure.provider}:${enclosure.path}`}><td><strong>{enclosure.descriptor}</strong><small className="cell-detail">{enclosure.id}</small></td><td>{enclosure.provider}</td><td><StatusBadge status={enclosure.health} /></td><td>{typeof enclosure.temperature_c === "number" ? `${enclosure.temperature_c} °C` : "Not reported"}</td><td>{typeof enclosure.fan_rpm === "number" ? `${enclosure.fan_rpm.toLocaleString()} RPM · ${enclosure.fan_count} reported` : "Not reported"}</td><td>{Array.isArray(enclosure.power_supplies) ? `${enclosure.power_supplies.join(", ")} · ${Array.isArray(enclosure.voltages) ? enclosure.voltages.map((value) => `${value} V`).join(", ") : "voltage not reported"}` : "Not reported"}</td></tr>)}</tbody></table></div>}
      </Card>
      <Card title="Pools" description="Live pool state and capacity.">
        {!inventory?.pools.items.length ? <div className="empty-state compact-empty"><h3>No pools configured</h3><p>Individual drives are listed above.</p></div> : <div className="table-scroll"><table className="data-table"><thead><tr><th>Pool</th><th>Type</th><th>Status</th><th>Maintenance</th><th>Progress</th><th>Used</th><th>Free</th><th>Members</th></tr></thead><tbody>{inventory.pools.items.map((pool) => <tr key={pool.id}><td><strong>{pool.name}</strong></td><td>{pool.type}</td><td><StatusBadge status={pool.status} /></td><td>{pool.maintenance ?? "Not reported"}</td><td>{typeof pool.progress_percent === "number" ? `${pool.progress_percent}%` : "Not reported"}</td><td>{bytesLabel(pool.used_bytes)}</td><td>{bytesLabel(pool.free_bytes)}</td><td>{numberLabel(pool.members)}</td></tr>)}</tbody></table></div>}
      </Card>
      <Card title="Storage Access" description="SMB, NFS, iSCSI, and FCoE service state.">
        {!services.length ? <div className="empty-state compact-empty"><h3>No storage access configured</h3><p>Add a service from Storage Access.</p></div> : <div className="table-scroll"><table className="data-table"><thead><tr><th>Name</th><th>Protocol</th><th>Status</th><th>Path</th></tr></thead><tbody>{services.map((service) => {
          const path = typeof service.config.path === "string" ? service.config.path : typeof service.config.backing_path === "string" ? service.config.backing_path : "Not reported";
          return <tr key={service.id}><td>{service.name}</td><td>{service.protocol.toUpperCase()}</td><td><StatusBadge status={service.status} /></td><td><code>{path}</code></td></tr>;
        })}</tbody></table></div>}
      </Card>
    </>}
  </div>;
}
