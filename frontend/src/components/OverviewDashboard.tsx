import { useEffect, useMemo, useRef, useState, type DragEvent, type ReactNode } from "react";
import { api } from "../api/client";
import { appendBounded } from "../liveHistory";
import {
  DASHBOARD_LAYOUT_KEY,
  DASHBOARD_PANEL_IDS,
  DEFAULT_DASHBOARD_PANELS,
  loadDashboardPanels,
  moveDashboardPanel,
  saveDashboardPanels,
  shiftDashboardPanel,
  type DashboardPanelId,
} from "../dashboard";
import type { LogicalStorageDocument, OverviewDocument, ResourceUsageDocument } from "../types";

const RESOURCE_REFRESH_INTERVAL_MS = 2_000;
const OVERVIEW_REFRESH_INTERVAL_MS = 30_000;
const RESOURCE_HISTORY_SAMPLES = 60;

interface ResourceHistoryPoint {
  cpu: number;
  memory: number;
  read: number;
  write: number;
  networkReceived: number;
  networkSent: number;
}

interface NetworkCounterSample {
  capturedAtMs: number;
  received: number;
  sent: number;
}

export function networkRates(
  previous: NetworkCounterSample | null,
  reading: ResourceUsageDocument,
): { sample: NetworkCounterSample; received: number | null; sent: number | null } {
  const capturedAtMs = Date.parse(reading.captured_at);
  const interfaces = reading.network.interfaces.filter((item) => item.up !== false);
  const received = interfaces.reduce((total, item) => total + (item.bytes_received ?? 0), 0);
  const sent = interfaces.reduce((total, item) => total + (item.bytes_sent ?? 0), 0);
  const sample = { capturedAtMs, received, sent };
  const elapsed = previous && Number.isFinite(capturedAtMs)
    ? (capturedAtMs - previous.capturedAtMs) / 1000
    : 0;
  if (!previous || elapsed <= 0 || received < previous.received || sent < previous.sent) {
    return { sample, received: null, sent: null };
  }
  return {
    sample,
    received: (received - previous.received) / elapsed,
    sent: (sent - previous.sent) / elapsed,
  };
}

const PANEL_NAMES: Record<DashboardPanelId, string> = {
  system: "System",
  performance: "Server Usage",
  "storage-performance": "Storage Activity",
  storage: "Storage",
  "drive-health": "Drive Health",
  network: "Network",
  neighbors: "Connected Switches & Devices",
  alerts: "Alerts",
  activity: "Recent Activity",
  applications: "Applications",
  shares: "Shares",
};

function readStoredPanels(): DashboardPanelId[] {
  try {
    return loadDashboardPanels(window.localStorage.getItem(DASHBOARD_LAYOUT_KEY));
  } catch {
    return [...DEFAULT_DASHBOARD_PANELS];
  }
}

function formatBytes(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "Not reported";
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  let amount = value;
  let unit = 0;
  while (amount >= 1024 && unit < units.length - 1) {
    amount /= 1024;
    unit += 1;
  }
  return `${amount >= 100 || unit === 0 ? amount.toFixed(0) : amount.toFixed(1)} ${units[unit]}`;
}

function formatDate(value: string | null | undefined): string {
  if (!value) return "Not reported";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Not reported" : date.toLocaleString();
}

function formatUptime(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "Not reported";
  const days = Math.floor(seconds / 86_400);
  const hours = Math.floor((seconds % 86_400) / 3_600);
  const minutes = Math.floor((seconds % 3_600) / 60);
  return [days ? `${days}d` : "", hours ? `${hours}h` : "", `${minutes}m`].filter(Boolean).join(" ");
}

function formatSpeed(mbps: number | null): string {
  if (mbps === null) return "Speed not reported";
  return mbps >= 1000 ? `${mbps / 1000} Gb/s` : `${mbps} Mb/s`;
}

function formatRate(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${formatBytes(value)}/s`;
}

function formatNumber(value: number | null | undefined, suffix = ""): string {
  return value === null || value === undefined ? "—" : `${value.toLocaleString(undefined, { maximumFractionDigits: 1 })}${suffix}`;
}

function MetricBar({ label, value, detail }: { label: string; value: number | null; detail: string }) {
  const width = value === null ? 0 : Math.max(0, Math.min(100, value));
  return <div className="overview-meter"><div className="overview-meter-label"><span>{label}</span><strong>{value === null ? "Not reported" : `${value.toFixed(1)}%`}</strong></div><div className="overview-meter-track" aria-hidden="true"><span style={{ width: `${width}%` }} /></div><small>{detail}</small></div>;
}

function DefinitionList({ items }: { items: Array<[string, ReactNode]> }) {
  return <dl className="overview-details">{items.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>;
}

function EmptyReading({ children }: { children: ReactNode }) {
  return <div className="overview-unavailable"><span aria-hidden="true">—</span><p>{children}</p></div>;
}

function miniPath(values: number[], maximum: number): string {
  if (!values.length) return "";
  return values.map((value, index) => {
    const x = values.length === 1 ? 100 : index * 100 / (values.length - 1);
    const y = 24 - Math.min(24, value * 24 / Math.max(1, maximum));
    return `${index ? "L" : "M"}${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(" ");
}

function MiniGraph({ label, primary, secondary, percent = false }: { label: string; primary: number[]; secondary?: number[]; percent?: boolean }) {
  const maximum = percent ? 100 : Math.max(1, ...primary, ...(secondary ?? []));
  return <figure className="overview-live-chart">
    <figcaption>{label}</figcaption>
    <svg viewBox="0 0 100 24" preserveAspectRatio="none" role="img" aria-label={`${label} live history`}>
      <path className="chart-primary" d={miniPath(primary, maximum)} />
      {secondary && <path className="chart-secondary" d={miniPath(secondary, maximum)} />}
    </svg>
  </figure>;
}

function panelBody(panel: DashboardPanelId, data: OverviewDocument | null, resources: ResourceUsageDocument | null, loadError: string | null, history: ResourceHistoryPoint[]): ReactNode {
  if (panel === "performance") {
    const cpu = resources?.cpu ?? data?.system.cpu;
    const memory = resources?.memory ?? data?.system.memory;
    const systemVolume = resources?.storage.system_volume ?? data?.system.boot_volume;
    if (!cpu || !memory) return <EmptyReading>{loadError ?? "Waiting for the first live resource reading."}</EmptyReading>;
    return <div className="overview-stack">
      <MetricBar label="Processor" value={cpu.used_percent} detail={`${cpu.physical_cores ?? "Not reported"} physical cores · ${cpu.logical_processors ?? "Not reported"} logical processors`} />
      <MetricBar label="Memory" value={memory.used_percent} detail={`${formatBytes(memory.used_bytes)} used of ${formatBytes(memory.total_bytes)}`} />
      <MetricBar label="System disk" value={systemVolume?.used_percent ?? null} detail={systemVolume ? `${formatBytes(systemVolume.used_bytes)} used of ${formatBytes(systemVolume.total_bytes)} at ${systemVolume.mountpoint}` : "System disk usage was not reported"} />
      {history.length > 0 && <div className="overview-chart-grid"><MiniGraph label="Processor" primary={history.map((item) => item.cpu)} percent /><MiniGraph label="Memory" primary={history.map((item) => item.memory)} percent /></div>}
    </div>;
  }
  if (panel === "storage-performance") {
    const performance = resources?.storage.performance;
    if (!performance || performance.summary.sample_seconds === null) return <EmptyReading>{loadError ?? "Collecting the first storage reading."}</EmptyReading>;
    const metrics = performance.summary;
    const active = (metrics.read_bytes_per_second ?? 0) + (metrics.write_bytes_per_second ?? 0) > 0;
    return <><div className="storage-kpi-grid compact">
        <div><span>Current activity</span><strong>{active ? "Active" : "Idle"}</strong></div>
        <div><span>Read</span><strong>{formatRate(metrics.read_bytes_per_second)}</strong></div>
        <div><span>Write</span><strong>{formatRate(metrics.write_bytes_per_second)}</strong></div>
        <div><span>Writes today</span><strong>{formatBytes(metrics.writes_today_bytes)}</strong></div>
      </div>
      {history.length > 0 && <div className="storage-overview-chart"><MiniGraph label="Bandwidth" primary={history.map((item) => item.read)} secondary={history.map((item) => item.write)} /></div>}
      <details><summary>Advanced details</summary><div className="storage-kpi-grid compact"><div><span>Read IOPS</span><strong>{formatNumber(metrics.read_iops)}</strong></div><div><span>Write IOPS</span><strong>{formatNumber(metrics.write_iops)}</strong></div><div><span>Read wait</span><strong>{formatNumber(metrics.read_wait_ms, " ms")}</strong></div><div><span>Write wait</span><strong>{formatNumber(metrics.write_wait_ms, " ms")}</strong></div><div><span>Busy</span><strong>{formatNumber(metrics.utilization_percent, "%")}</strong></div></div></details>
    </>;
  }
  if (!data) return <EmptyReading>{loadError ?? "Waiting for the first live reading."}</EmptyReading>;
  if (panel === "system") {
    return <DefinitionList items={[
      ["Server", data.system.hostname ?? "Not reported"],
      ["Hoardarr", data.system.version || "Not reported"],
      ["Uptime", formatUptime(data.system.uptime_seconds)],
      ["Started", formatDate(data.system.booted_at)],
      ["Database", data.system.database_ready ? <span className="overview-state good">Ready</span> : <span className="overview-state bad">Not ready</span>],
    ]} />;
  }
  if (panel === "storage") {
    if (!data.storage.snapshot) return <EmptyReading>No hardware scan has been completed. Storage totals are not reported.</EmptyReading>;
    return <DefinitionList items={[
      ["Detected drives", data.storage.drive_count ?? "Not reported"],
      ["Raw capacity", formatBytes(data.storage.raw_capacity_bytes)],
      ["Latest scan", formatDate(data.storage.snapshot.captured_at)],
      ["Scan source", data.storage.snapshot.source || "Not reported"],
      ["Pools", data.storage.pools.status === "not_configured" ? "Not configured" : `${data.storage.pools.items.length}`],
    ]} />;
  }
  if (panel === "drive-health") {
    const health = data.storage.health;
    if (!data.storage.snapshot) return <EmptyReading>No hardware scan has been completed. Drive health is not reported.</EmptyReading>;
    if (!health) return <EmptyReading>The latest hardware scan did not report drive health.</EmptyReading>;
    return <div className="health-counts">
      <div><strong className="good">{health.healthy}</strong><span>Healthy</span></div>
      <div><strong className="warning">{health.warning}</strong><span>Warning</span></div>
      <div><strong className="bad">{health.critical}</strong><span>Critical</span></div>
      <div><strong>{health.unknown}</strong><span>Not reported</span></div>
    </div>;
  }
  if (panel === "network") {
    if (!data.network.interfaces.length) return <EmptyReading>No network interfaces were reported by the host.</EmptyReading>;
    return <div className="overview-stack"><div className="overview-list">{data.network.interfaces.map((item) => <div key={item.name} className="overview-list-row"><div><strong>{item.name}</strong><small>{formatSpeed(item.speed_mbps)} · MTU {item.mtu ?? "not reported"}</small></div><div className="overview-list-values"><span className={`overview-state ${item.up === true ? "good" : item.up === false ? "bad" : ""}`}>{item.up === true ? "Up" : item.up === false ? "Down" : "Not reported"}</span><small>↓ {formatBytes(item.bytes_received)} · ↑ {formatBytes(item.bytes_sent)}</small></div></div>)}</div>{history.length > 0 && <MiniGraph label="Network bandwidth" primary={history.map((item) => item.networkReceived)} secondary={history.map((item) => item.networkSent)} />}</div>;
  }
  if (panel === "neighbors") {
    const discovery = data.network.discovery;
    if (!discovery.neighbors.length) return <EmptyReading>{discovery.detail ?? "No LLDP or CDP neighbors were reported."}</EmptyReading>;
    return <div className="overview-list">{discovery.neighbors.map((neighbor, index) => {
      const device = neighbor.device_name ?? neighbor.chassis_id ?? "Unnamed device";
      const remotePort = neighbor.port_id ?? neighbor.port_description ?? "Port not reported";
      const management = neighbor.management_addresses.join(", ") || "Management address not reported";
      return <div key={`${neighbor.local_interface}-${neighbor.protocol_variant}-${neighbor.chassis_id ?? device}-${neighbor.port_id ?? index}`} className="overview-list-row"><div><strong>{device}</strong><small>{neighbor.protocol_variant} · {neighbor.local_interface} → {remotePort}{neighbor.port_description && neighbor.port_description !== remotePort ? ` · ${neighbor.port_description}` : ""}</small></div><div className="overview-list-values"><span className="overview-state good">Connected</span><small>{management}{neighbor.age ? ` · seen ${neighbor.age} ago` : ""}</small></div></div>;
    })}</div>;
  }
  if (panel === "alerts") {
    if (!data.alerts.length) return <div className="overview-clear"><span aria-hidden="true">✓</span><div><strong>No active alerts</strong><small>The live API did not report any current alerts.</small></div></div>;
    return <div className="overview-list">{data.alerts.map((alert, index) => <div key={`${alert.source}-${alert.operation_id ?? index}`} className={`overview-alert ${alert.severity}`}><strong>{alert.severity}</strong><span>{alert.message}</span></div>)}</div>;
  }
  if (panel === "activity") {
    if (!data.activity.operations.length) return <EmptyReading>No operations have been recorded.</EmptyReading>;
    return <div className="overview-list">{data.activity.operations.map((operation) => <div key={operation.id} className="overview-list-row"><div><strong>{operation.kind.replaceAll(".", " ")}</strong><small>{formatDate(operation.created_at)}</small></div><span className={`overview-state ${operation.status === "succeeded" ? "good" : ["failed", "needs_attention"].includes(operation.status) ? "bad" : ""}`}>{operation.status.replaceAll("_", " ")}</span></div>)}</div>;
  }
  if (panel === "applications") {
    if (!data.applications.connections.length) return <EmptyReading>No applications are connected.</EmptyReading>;
    return <div className="overview-list">{data.applications.connections.map((connection) => <div key={connection.id} className="overview-list-row"><div><strong>{connection.name}</strong><small>{connection.adapter}{connection.product_version ? ` · ${connection.product_version}` : ""}</small></div><span className="overview-state">{connection.status.replaceAll("_", " ")}</span></div>)}</div>;
  }
  return data.storage.shares.status === "not_configured" ? <EmptyReading>Shares are not configured.</EmptyReading> : <DefinitionList items={[["Shares", data.storage.shares.items.length]]} />;
}

export function OverviewDashboard({ onOpenStorage }: { onOpenStorage?: (storageId: string) => void } = {}) {
  const [data, setData] = useState<OverviewDocument | null>(null);
  const [resources, setResources] = useState<ResourceUsageDocument | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [resourceError, setResourceError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [customizing, setCustomizing] = useState(false);
  const [panels, setPanels] = useState<DashboardPanelId[]>(readStoredPanels);
  const [draggedPanel, setDraggedPanel] = useState<DashboardPanelId | null>(null);
  const [resourceHistory, setResourceHistory] = useState<ResourceHistoryPoint[]>([]);
  const [logicalStorage, setLogicalStorage] = useState<LogicalStorageDocument[]>([]);
  const resourceRequestActive = useRef(false);
  const previousNetworkCounters = useRef<NetworkCounterSample | null>(null);
  const hiddenPanels = useMemo(() => DASHBOARD_PANEL_IDS.filter((panel) => !panels.includes(panel)), [panels]);

  async function refreshOverview() {
    try {
      const [reading, storage] = await Promise.all([
        api.overview(),
        api.logicalStorage().catch(() => [] as LogicalStorageDocument[]),
      ]);
      setData(reading);
      setLogicalStorage(storage);
      setLoadError(null);
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : "The live Overview reading could not be loaded.");
    }
  }

  async function refreshResources() {
    if (resourceRequestActive.current) return;
    resourceRequestActive.current = true;
    try {
      const reading = await api.resourceUsage();
      setResources(reading);
      const network = networkRates(previousNetworkCounters.current, reading);
      previousNetworkCounters.current = network.sample;
      const storagePerformance = reading.storage.performance;
      if (storagePerformance?.summary.sample_seconds !== null && storagePerformance?.summary.sample_seconds !== undefined) {
        setResourceHistory((current) => appendBounded(current, {
          cpu: reading.cpu.used_percent ?? 0,
          memory: reading.memory.used_percent ?? 0,
          read: storagePerformance.summary.read_bytes_per_second ?? 0,
          write: storagePerformance.summary.write_bytes_per_second ?? 0,
          networkReceived: network.received ?? 0,
          networkSent: network.sent ?? 0,
        }, RESOURCE_HISTORY_SAMPLES));
      }
      setResourceError(null);
    } catch (error) {
      setResourceError(error instanceof Error ? error.message : "Live resource usage could not be loaded.");
    } finally {
      resourceRequestActive.current = false;
    }
  }

  async function refreshAll() {
    setLoading(true);
    try {
      await Promise.all([refreshOverview(), refreshResources()]);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refreshAll();
    const resourcesTimer = window.setInterval(() => {
      if (document.visibilityState === "visible") void refreshResources();
    }, RESOURCE_REFRESH_INTERVAL_MS);
    const overviewTimer = window.setInterval(() => {
      if (document.visibilityState === "visible") void refreshOverview();
    }, OVERVIEW_REFRESH_INTERVAL_MS);
    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible") void refreshResources();
    };
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => {
      window.clearInterval(resourcesTimer);
      window.clearInterval(overviewTimer);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, []);

  useEffect(() => {
    try {
      window.localStorage.setItem(DASHBOARD_LAYOUT_KEY, saveDashboardPanels(panels));
    } catch {
      // The layout remains usable for this session when browser storage is unavailable.
    }
  }, [panels]);

  function dropOn(event: DragEvent<HTMLElement>, destination: DashboardPanelId) {
    event.preventDefault();
    if (draggedPanel) setPanels((current) => moveDashboardPanel(current, draggedPanel, destination));
    setDraggedPanel(null);
  }

  return <section className="overview-dashboard" aria-label="Overview dashboard">
    <div className="overview-actions">
      <div className={`overview-source ${loadError || resourceError ? "unavailable" : ""}`} role="status">
        <span aria-hidden="true" />
        {loading && !data && !resources ? "Requesting live data…" : loadError || resourceError ? "Live data unavailable" : `Live reading · ${formatDate(resources?.captured_at ?? data?.captured_at)}`}
      </div>
      <div>
        <button type="button" className="button button-secondary" onClick={() => void refreshAll()} disabled={loading}>{loading ? "Refreshing…" : "Refresh"}</button>
        <button type="button" className={`button ${customizing ? "button-primary" : "button-secondary"}`} aria-pressed={customizing} onClick={() => setCustomizing((value) => !value)}>{customizing ? "Done" : "Customize Dashboard"}</button>
      </div>
    </div>

    {customizing && <div className="dashboard-customize">
      <div><strong>Add or rearrange panels</strong><p>Drag a panel by its handle, use the arrow buttons, or remove it. Dashboard choices are saved in this browser.</p></div>
      <div className="dashboard-add-list">{hiddenPanels.length ? hiddenPanels.map((panel) => <button type="button" key={panel} onClick={() => setPanels((current) => [...current, panel])}>+ {PANEL_NAMES[panel]}</button>) : <span>All panels are shown.</span>}</div>
      <button type="button" className="text-button dashboard-reset" onClick={() => setPanels([...DEFAULT_DASHBOARD_PANELS])}>Reset layout</button>
    </div>}

    {logicalStorage.some((storage) => storage.redundancy_capable !== false) && <section className="overview-redundancy-strip" aria-label="Storage redundancy status">
      <div><strong>Storage redundancy</strong><span>{logicalStorage.filter((storage) => storage.redundancy_capable !== false).some((storage) => ["reduced_redundancy", "failed_over", "no_path"].includes(storage.topology_state)) ? "Needs attention" : "Healthy"}</span></div>
      <div className="overview-redundancy-items">{logicalStorage.filter((storage) => storage.redundancy_capable !== false).map((storage) => <button type="button" key={storage.id} onClick={() => onOpenStorage?.(storage.id)}><strong>{storage.name}</strong><span>{storage.topology_state.replaceAll("_", " ")} · {storage.redundancy_summary?.healthy_paths ?? storage.paths.filter((path) => path.active).length}/{storage.paths.length} paths healthy</span></button>)}</div>
    </section>}

    {panels.length ? <div className="dashboard-grid">{panels.map((panel, index) => <article
      className={`dashboard-panel ${customizing ? "is-customizing" : ""}`}
      key={panel}
      draggable={customizing}
      onDragStart={() => setDraggedPanel(panel)}
      onDragEnd={() => setDraggedPanel(null)}
      onDragOver={(event) => { if (customizing) event.preventDefault(); }}
      onDrop={(event) => dropOn(event, panel)}
    >
      <header><div className="dashboard-panel-heading">{customizing && <span className="dashboard-drag-handle" aria-hidden="true">☷</span>}<h2>{PANEL_NAMES[panel]}</h2></div>{customizing && <div className="dashboard-panel-controls"><button type="button" aria-label={`Move ${PANEL_NAMES[panel]} earlier`} disabled={index === 0} onClick={() => setPanels((current) => shiftDashboardPanel(current, panel, -1))}>←</button><button type="button" aria-label={`Move ${PANEL_NAMES[panel]} later`} disabled={index === panels.length - 1} onClick={() => setPanels((current) => shiftDashboardPanel(current, panel, 1))}>→</button><button type="button" aria-label={`Remove ${PANEL_NAMES[panel]}`} onClick={() => setPanels((current) => current.filter((item) => item !== panel))}>×</button></div>}</header>
      <div className="dashboard-panel-body">{panelBody(panel, data, resources, loadError ?? resourceError, resourceHistory)}</div>
    </article>)}</div> : <div className="dashboard-empty"><h2>No panels are shown</h2><p>Choose Customize Dashboard to add the panels you want.</p><button type="button" className="button button-primary" onClick={() => setCustomizing(true)}>Add panels</button></div>}
  </section>;
}
