import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import { appendBounded } from "../liveHistory";
import type { StoragePerformanceMetrics, StorageTelemetryDocument } from "../types";
import { Card, Notice } from "./ui";

const REFRESH_MS = 2_000;
const HISTORY_SAMPLES = 60;

interface HistoryPoint {
  at: number;
  read: number | null;
  write: number | null;
  readIops: number | null;
  writeIops: number | null;
  readWait: number | null;
  writeWait: number | null;
}

function bytes(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "Not reported";
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  let amount = value;
  let index = 0;
  while (amount >= 1024 && index < units.length - 1) {
    amount /= 1024;
    index += 1;
  }
  return `${amount >= 100 || index === 0 ? amount.toFixed(0) : amount.toFixed(1)} ${units[index]}`;
}

function number(value: number | null | undefined, suffix = ""): string {
  return value === null || value === undefined
    ? "Not reported"
    : `${value.toLocaleString(undefined, { maximumFractionDigits: 1 })}${suffix}`;
}

function rate(value: number | null | undefined): string {
  return value === null || value === undefined ? "Not reported" : `${bytes(value)}/s`;
}

function Kpis({ metrics, writesToday }: { metrics: StoragePerformanceMetrics; writesToday: number }) {
  return <div className="storage-kpi-grid">
    <div><span>Read</span><strong>{rate(metrics.read_bytes_per_second)}</strong></div>
    <div><span>Write</span><strong>{rate(metrics.write_bytes_per_second)}</strong></div>
    <div><span>Read IOPS</span><strong>{number(metrics.read_iops)}</strong></div>
    <div><span>Write IOPS</span><strong>{number(metrics.write_iops)}</strong></div>
    <div><span>Read wait</span><strong>{number(metrics.read_wait_ms, " ms")}</strong></div>
    <div><span>Write wait</span><strong>{number(metrics.write_wait_ms, " ms")}</strong></div>
    <div><span>Writes today</span><strong>{bytes(writesToday)}</strong></div>
    <div><span>Busy</span><strong>{number(metrics.utilization_percent, "%")}</strong></div>
  </div>;
}

function path(values: Array<number | null>, maximum: number): string {
  if (!values.some((value) => value !== null)) return "";
  let connected = false;
  return values.map((value, index) => {
    if (value === null) {
      connected = false;
      return "";
    }
    const x = values.length === 1 ? 100 : index * 100 / (values.length - 1);
    const y = 36 - Math.min(36, value * 36 / maximum);
    const command = connected ? "L" : "M";
    connected = true;
    return `${command}${x.toFixed(2)},${y.toFixed(2)}`;
  }).filter(Boolean).join(" ");
}

function LiveGraph({ title, primaryLabel, secondaryLabel, primary, secondary, history, formatter }: {
  title: string;
  primaryLabel: string;
  secondaryLabel: string;
  primary: Array<number | null>;
  secondary: Array<number | null>;
  history: HistoryPoint[];
  formatter: (value: number) => string;
}) {
  const available = [...primary, ...secondary].filter((value): value is number => value !== null);
  const observedMaximum = Math.max(0, ...available);
  const maximum = Math.max(1, observedMaximum);
  const unavailable = [...primary, ...secondary].filter((value) => value === null).length;
  return <figure className="storage-live-chart">
    <figcaption><strong>{title}</strong><span>{available.length ? `Peak ${formatter(observedMaximum)}` : "Peak not reported"}</span></figcaption>
    {available.length ? <svg viewBox="0 0 100 36" preserveAspectRatio="none" role="img" aria-label={`${title}, live session history${unavailable ? ` with ${unavailable} unavailable values shown as gaps` : ""}`}>
      <line x1="0" y1="18" x2="100" y2="18" />
      <path className="chart-primary" d={path(primary, maximum)} />
      <path className="chart-secondary" d={path(secondary, maximum)} />
    </svg> : <p className="empty-state compact-empty">No reported samples in this live session.</p>}
    <div className="storage-chart-legend"><span className="primary">{primaryLabel}</span><span className="secondary">{secondaryLabel}</span><small>Live session only · {REFRESH_MS / 1000}s target cadence · up to {HISTORY_SAMPLES} samples · linux_block_counters{history.length ? ` · ${new Date(history[0].at).toLocaleTimeString()}–${new Date(history.at(-1)?.at ?? history[0].at).toLocaleTimeString()}` : ""}</small></div>
  </figure>;
}

export function storageLoadState(metrics: StoragePerformanceMetrics): { label: string; tone: string; methodology: string } {
  const waits = [metrics.read_wait_ms, metrics.write_wait_ms].filter((value): value is number => value !== null);
  const wait = waits.length ? Math.max(...waits) : null;
  const methodology = "Derived from this live Linux block-counter sample: severe response delay at ≥100 ms; response delay at ≥30 ms; busy at ≥80% utilization; idle only when both read and write throughput are reported and total <1 KiB/s; otherwise active when reported throughput is ≥1 KiB/s. Missing inputs are not treated as zero.";
  if (wait !== null && wait >= 100) return { label: "Severe response delay", tone: "bad", methodology };
  if (wait !== null && wait >= 30) return { label: "Response delay", tone: "warning", methodology };
  if (metrics.utilization_percent !== null && metrics.utilization_percent >= 80) return { label: "Busy", tone: "warning", methodology };
  const read = metrics.read_bytes_per_second;
  const write = metrics.write_bytes_per_second;
  if (read !== null && write !== null) {
    return read + write < 1024
      ? { label: "Idle", tone: "", methodology }
      : { label: "Active", tone: "good", methodology };
  }
  if ((read ?? 0) >= 1024 || (write ?? 0) >= 1024) return { label: "Active", tone: "good", methodology };
  return { label: "Not reported", tone: "", methodology };
}

export function StoragePerformance() {
  const [data, setData] = useState<StorageTelemetryDocument | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<HistoryPoint[]>([]);
  const active = useRef(false);
  const lifetimeController = useRef<AbortController | null>(null);

  async function refresh() {
    if (active.current) return;
    active.current = true;
    try {
      const reading = await api.storageTelemetry(lifetimeController.current?.signal);
      setData(reading);
      if (reading.summary.sample_seconds !== null) {
        setHistory((current) => appendBounded(current, {
          at: Date.now(),
          read: reading.summary.read_bytes_per_second,
          write: reading.summary.write_bytes_per_second,
          readIops: reading.summary.read_iops,
          writeIops: reading.summary.write_iops,
          readWait: reading.summary.read_wait_ms,
          writeWait: reading.summary.write_wait_ms,
        }, HISTORY_SAMPLES));
      }
      setError(null);
    } catch (caught) {
      if (!(caught instanceof DOMException && caught.name === "AbortError")) {
        setHistory((current) => appendBounded(current, {
          at: Date.now(), read: null, write: null, readIops: null, writeIops: null, readWait: null, writeWait: null,
        }, HISTORY_SAMPLES));
        setError(caught instanceof Error ? caught.message : "Storage performance could not be loaded.");
      }
    } finally {
      active.current = false;
    }
  }

  useEffect(() => {
    lifetimeController.current = new AbortController();
    void refresh();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void refresh();
    }, REFRESH_MS);
    return () => {
      lifetimeController.current?.abort();
      lifetimeController.current = null;
      window.clearInterval(timer);
    };
  }, []);

  const state = useMemo(() => data ? storageLoadState(data.summary) : null, [data]);
  const individualDrives = data?.drives.filter((drive) => drive.pool_ids.length === 0) ?? [];
  const pooledDrives = data?.drives.filter((drive) => drive.pool_ids.length > 0) ?? [];

  return <Card title="Storage performance" description="Live readings refresh every two seconds.">
    {error && <Notice tone="warning" title="Live readings unavailable">{error}</Notice>}
    {state && <div className={`storage-simple-state ${state.tone}`}><span aria-hidden="true" />{state.label}</div>}
    {state && <details className="metric-methodology"><summary>How this live state is calculated</summary><p>{state.methodology}</p><dl><div><dt>Source</dt><dd>{data?.source ?? "Not reported"}</dd></div><div><dt>Observed</dt><dd>{data?.captured_at ? new Date(data.captured_at).toLocaleString() : "Not reported"}</dd></div><div><dt>Classification</dt><dd>Derived live-session state</dd></div></dl></details>}
    {!data || data.summary.sample_seconds === null
      ? <p>Collecting the first storage reading…</p>
      : <Kpis metrics={data.summary} writesToday={data.summary.writes_today_bytes} />}

    {history.length > 0 && <div className="storage-live-charts">
      <LiveGraph title="Bandwidth" primaryLabel="Read" secondaryLabel="Write" primary={history.map((item) => item.read)} secondary={history.map((item) => item.write)} history={history} formatter={(value) => `${bytes(value)}/s`} />
      <LiveGraph title="Operations" primaryLabel="Read" secondaryLabel="Write" primary={history.map((item) => item.readIops)} secondary={history.map((item) => item.writeIops)} history={history} formatter={(value) => `${number(value)} IOPS`} />
      <LiveGraph title="Response time" primaryLabel="Read" secondaryLabel="Write" primary={history.map((item) => item.readWait)} secondary={history.map((item) => item.writeWait)} history={history} formatter={(value) => `${number(value)} ms`} />
    </div>}

    <details className="storage-performance-details">
      <summary>Drive and pool details</summary>
      <h3>Individual drives</h3>
      {individualDrives.length ? <div className="table-scroll"><table className="data-table"><thead><tr><th>Drive</th><th>Read / write</th><th>IOPS</th><th>Wait</th><th>Writes today</th><th>Lifetime writes</th><th>Endurance left</th></tr></thead><tbody>
        {individualDrives.map((drive) => <tr key={drive.id}>
          <td><code>{drive.device}</code><small className="cell-detail">{drive.model || drive.serial || "Model not reported"}{drive.system_disk ? " · System" : ""}</small></td>
          <td>{rate(drive.metrics.read_bytes_per_second)} / {rate(drive.metrics.write_bytes_per_second)}</td>
          <td>{number(drive.metrics.read_iops)} / {number(drive.metrics.write_iops)}</td>
          <td>{number(drive.metrics.read_wait_ms, " ms")} / {number(drive.metrics.write_wait_ms, " ms")}</td>
          <td>{bytes(drive.writes_today_bytes)}</td>
          <td>{bytes(drive.endurance.lifetime_writes_bytes)}</td>
          <td>{number(drive.endurance.remaining_percent, "%")}</td>
        </tr>)}
      </tbody></table></div> : <p>No independent drives were reported.</p>}

      <h3>Pool details</h3>
      {data?.pools.length ? <div className="table-scroll"><table className="data-table"><thead><tr><th>Pool</th><th>Read / write</th><th>IOPS</th><th>Wait</th><th>Writes today</th></tr></thead><tbody>
        {data.pools.map((pool) => <tr key={pool.id}>
          <td>{pool.name}<small className="cell-detail">{pool.type}</small></td>
          <td>{pool.metrics ? `${rate(pool.metrics.read_bytes_per_second)} / ${rate(pool.metrics.write_bytes_per_second)}` : "Not reported"}</td>
          <td>{pool.metrics ? `${number(pool.metrics.read_iops)} / ${number(pool.metrics.write_iops)}` : "Not reported"}</td>
          <td>{pool.metrics ? `${number(pool.metrics.read_wait_ms, " ms")} / ${number(pool.metrics.write_wait_ms, " ms")}` : "Not reported"}</td>
          <td>{bytes(pool.writes_today_bytes)}</td>
        </tr>)}
      </tbody></table></div> : <p>No pools are configured.</p>}

      {pooledDrives.length > 0 && <details className="storage-member-details"><summary>Pool member drives</summary><div className="table-scroll"><table className="data-table"><thead><tr><th>Drive</th><th>Pool</th><th>Read / write</th><th>Writes today</th><th>Endurance left</th></tr></thead><tbody>
        {pooledDrives.map((drive) => <tr key={drive.id}><td><code>{drive.device}</code></td><td>{drive.pool_ids.map((id) => data?.pools.find((pool) => pool.id === id)?.name ?? id).join(", ")}</td><td>{rate(drive.metrics.read_bytes_per_second)} / {rate(drive.metrics.write_bytes_per_second)}</td><td>{bytes(drive.writes_today_bytes)}</td><td>{number(drive.endurance.remaining_percent, "%")}</td></tr>)}
      </tbody></table></div></details>}
    </details>
  </Card>;
}
