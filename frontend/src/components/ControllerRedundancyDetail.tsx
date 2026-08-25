import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import { historyEnvelopeValues, historyMeanValues, nullablePath, qualityHasValue, qualityLabel } from "../metricHistory";
import type {
  LogicalStorageDocument,
  MetricEntity,
  MetricHistoryDocument,
  MetricSampleDocument,
  StorageRedundancyEventDocument,
  StorageRedundancySettings,
} from "../types";
import { humanCapacity } from "../policy";
import { Notice, StatusBadge } from "./ui";

type DetailTab = "overview" | "paths" | "performance" | "events" | "settings";
type Action = "add" | "remove" | "replace" | "configure";

const METRICS = [
  { id: "io.read.bytes_per_second", label: "Read throughput", unit: "B/s" },
  { id: "io.write.bytes_per_second", label: "Write throughput", unit: "B/s" },
  { id: "io.read.iops", label: "Read IOPS", unit: "ops/s" },
  { id: "io.write.iops", label: "Write IOPS", unit: "ops/s" },
  { id: "io.read.latency", label: "Read latency", unit: "ms" },
  { id: "io.write.latency", label: "Write latency", unit: "ms" },
] as const;
const PATH_COUNT_METRICS = [
  { id: "storage.paths.healthy", label: "Healthy paths", unit: "count" },
  { id: "storage.paths.failed", label: "Failed paths", unit: "count" },
] as const;

function display(value: unknown): string {
  if (value === null || value === undefined || value === "") return "Not reported";
  return String(value);
}

function stateLabel(storage: LogicalStorageDocument): string {
  return ({
    fully_redundant: "Fully redundant",
    reduced_redundancy: "Reduced redundancy",
    failed_over: "Failed over",
    no_path: "No path",
    single_path: "Single path",
  } as Record<string, string>)[storage.topology_state] ?? storage.topology_state;
}

function formatMetric(sample: MetricSampleDocument | undefined): string {
  if (!sample || sample.value === null || !qualityHasValue(sample.quality)) return "Not reported";
  if (typeof sample.value === "string") return sample.value;
  if (sample.unit === "bytes_per_second") return `${humanCapacity(sample.value)}/s`;
  if (sample.unit === "milliseconds") return `${sample.value.toFixed(1)} ms`;
  if (sample.unit === "operations_per_second") return `${sample.value.toFixed(0)} IOPS`;
  return `${sample.value}`;
}

function PathGraph({
  label,
  unit,
  series,
  events,
}: {
  label: string;
  unit: string;
  series: Array<{ entity: MetricEntity; history: MetricHistoryDocument }>;
  events: StorageRedundancyEventDocument[];
}) {
  const start = series[0]?.history.start ? new Date(series[0].history.start).getTime() : 0;
  const end = series[0]?.history.end ? new Date(series[0].history.end).getTime() : 0;
  const annotations = events.filter((event) => [
    "controller_failover",
    "path_failed",
    "path_recovered",
    "node_storage_unavailable",
    "storage_transitioned",
    "node_recovered",
    "redundancy_restored",
  ].includes(event.event_type));
  const values = series.flatMap((item) => historyMeanValues(item.history)).filter((value): value is number => value !== null);
  const domain = values.length ? { minimum: Math.min(...values), maximum: Math.max(...values) } : undefined;
  return <figure className="redundancy-graph">
    <figcaption><strong>{label}</strong><span>{unit} · {series.length > 1 ? "per path; not summed" : "reported entity"}</span></figcaption>
    {series.length === 0 ? <p className="empty-state compact-empty">No stored path readings.</p> : <>
      <svg viewBox="0 0 100 30" preserveAspectRatio="none" role="img" aria-label={`${label} by controller path`}>
        {series.map((item, index) => {
          const envelope = historyEnvelopeValues(item.history);
          return <g key={item.entity.id}>
            {envelope && <><path className={`path-series rollup-boundary series-${index % 8}`} d={nullablePath(envelope.minimum, 30, 2, domain)} /><path className={`path-series rollup-boundary series-${index % 8}`} d={nullablePath(envelope.maximum, 30, 2, domain)} /></>}
            <path className={`path-series series-${index % 8}`} d={nullablePath(historyMeanValues(item.history), 30, 2, domain)} />
          </g>;
        })}
        {end > start && annotations.map((event) => {
          const at = new Date(event.occurred_at).getTime();
          if (at < start || at > end) return null;
          const x = (at - start) / (end - start) * 100;
          return <line className="failover-marker" key={event.id} x1={x} x2={x} y1="0" y2="30"><title>{event.event_type.replaceAll("_", " ")} · {new Date(event.occurred_at).toLocaleString()}</title></line>;
        })}
      </svg>
      <div className="graph-legend">{series.map((item, index) => <span key={item.entity.id}><i className={`series-${index % 8}`} />{item.entity.display_name}</span>)}</div>
      <details className="graph-diagnostics"><summary>Graph sources and resolution</summary><ul>{series.map((item) => <li key={item.entity.id}><strong>{item.entity.display_name}</strong>: {item.history.metric_source ?? item.history.points.find((point) => point.source)?.source ?? "Source not reported"} · {item.history.source_resolution ?? item.history.resolution} · {item.history.raw === false ? "aggregated buckets with minimum/maximum boundaries" : "raw observations"} · {item.history.points.length} points</li>)}</ul></details>
      {annotations.length > 0 && <small>Vertical markers show failover, path loss, and recovery events.</small>}
    </>}
  </figure>;
}

function LogicalMetric({ label, sample }: { label: string; sample: MetricSampleDocument | undefined }) {
  return <article><small>{label}</small><strong>{formatMetric(sample)}</strong><span className={`metric-quality quality-${sample?.quality ?? "not_reported"}`}>{sample ? qualityLabel(sample.quality) : "Not reported"}</span><small>{sample ? `${sample.source} · observed ${new Date(sample.timestamp).toLocaleString()} · ${sample.collection_interval_seconds}s interval` : "No authoritative logical-storage observation"}</small></article>;
}

function PathLiveMetrics({ entity, samples }: { entity: MetricEntity; samples: Map<string, MetricSampleDocument> }) {
  const metricIds = ["io.read.bytes_per_second", "io.write.bytes_per_second", "io.read.iops", "io.read.latency"];
  const readings = metricIds.map((metricId) => samples.get(`${entity.id}:${metricId}`));
  const provenance = readings.find((sample) => sample);
  return <div className="path-live-kpis" aria-label={`${entity.display_name} per-path metrics`}>
    <span>{formatMetric(readings[0])} read</span>
    <span>{formatMetric(readings[1])} write</span>
    <span>{formatMetric(readings[2])} read</span>
    <span>{formatMetric(readings[3])} latency</span>
    <small>Per-path only · values are not a logical-storage total</small>
    <small>{provenance ? `Source: ${provenance.source} · observed ${new Date(provenance.timestamp).toLocaleString()} · ${provenance.collection_interval_seconds}s interval` : "Source not reported · no per-path observation"}</small>
  </div>;
}

function Topology({ storage }: { storage: LogicalStorageDocument }) {
  return <div className="redundancy-topology" role="img" aria-label={`${storage.name} controller and path topology`}>
    <div className="topology-storage"><strong>{storage.name}</strong><small>{storage.presentation_device}</small></div>
    <div className="topology-trunk" aria-hidden="true" />
    <div className="topology-path-grid">{storage.paths.map((path, index) => <div className={`topology-path ${path.active ? "is-active" : "is-failed"}`} key={path.id}>
      <span className="topology-line" aria-hidden="true" />
      <strong>{path.controller?.model ?? `Controller path ${index + 1}`}</strong>
      <span>{path.kernel_path}</span>
      <small>{path.protocol || "Not reported"} · {path.optimized === true ? "Optimized" : path.optimized === false ? "Non-optimized" : "Optimization not reported"}</small>
      <StatusBadge status={path.active ? "healthy" : path.state || "unavailable"} />
    </div>)}</div>
  </div>;
}

export function ControllerRedundancyDetail({
  storage,
  onAction,
}: {
  storage: LogicalStorageDocument;
  onAction: (action: Action, settings?: StorageRedundancySettings) => void;
}) {
  const [tab, setTab] = useState<DetailTab>("overview");
  const [events, setEvents] = useState<StorageRedundancyEventDocument[]>([]);
  const [entities, setEntities] = useState<MetricEntity[]>([]);
  const [logicalEntity, setLogicalEntity] = useState<MetricEntity | null>(null);
  const [current, setCurrent] = useState<MetricSampleDocument[]>([]);
  const [histories, setHistories] = useState<Record<string, MetricHistoryDocument>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [settings, setSettings] = useState<StorageRedundancySettings | null>(storage.redundancy_settings ?? null);
  const historyCache = useRef<{ storageId: string; loadedAt: number } | null>(null);

  useEffect(() => setSettings(storage.redundancy_settings ?? null), [storage.id, storage.redundancy_settings]);

  useEffect(() => {
    setHistories({});
    historyCache.current = null;
  }, [storage.id]);

  useEffect(() => {
    const controller = new AbortController();
    let requestActive = false;
    const load = async () => {
      if (requestActive) return;
      requestActive = true;
      setLoading(true);
      try {
        const [nextEvents, nextEntities, nextLogicalEntities, nextCurrent, nextLogicalCurrent] = await Promise.all([
          api.storageRedundancyEvents(storage.id, controller.signal),
          api.metricEntities("storage_path", controller.signal),
          api.metricEntities("logical_storage", controller.signal),
          api.currentMetrics({ entityType: "storage_path" }, controller.signal),
          api.currentMetrics({ entityType: "logical_storage" }, controller.signal),
        ]);
        if (controller.signal.aborted) return;
        const pathEntities = nextEntities.filter((entity) => entity.topology.storage_entity_id === storage.id);
        setEvents(nextEvents);
        setEntities(pathEntities);
        setLogicalEntity(nextLogicalEntities.find((entity) => entity.topology.storage_entity_id === storage.id) ?? null);
        setCurrent([
          ...nextCurrent.items.filter((sample) => sample.entity.topology.storage_entity_id === storage.id),
          ...nextLogicalCurrent.items.filter((sample) => sample.entity.topology.storage_entity_id === storage.id),
        ]);
        setError(null);
      } catch (requestError) {
        if (!controller.signal.aborted) setError(requestError instanceof Error ? requestError.message : "Controller history could not be loaded.");
      } finally {
        requestActive = false;
        if (!controller.signal.aborted) setLoading(false);
      }
    };
    void load();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void load();
    }, 5_000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [storage.id]);

  useEffect(() => {
    if (tab !== "performance" || entities.length === 0) return;
    const cached = historyCache.current;
    if (cached?.storageId === storage.id && Date.now() - cached.loadedAt < 30_000) return;
    const controller = new AbortController();
    const end = new Date();
    const start = new Date(end.getTime() - 24 * 60 * 60 * 1000);
    const requests = [
      ...entities.flatMap((entity) => METRICS.map((metric) => ({ entity, metric }))),
      ...(logicalEntity ? PATH_COUNT_METRICS.map((metric) => ({ entity: logicalEntity, metric })) : []),
    ];
    void Promise.all(requests.map(async ({ entity, metric }) => {
      const history = await api.metricHistory({
        entityId: entity.id,
        metricId: metric.id,
        start: start.toISOString(),
        end: end.toISOString(),
        resolution: "auto",
        maximumPoints: 240,
        signal: controller.signal,
      });
      return [`${entity.id}:${metric.id}`, history] as const;
    })).then((pairs) => {
      if (!controller.signal.aborted) {
        setHistories(Object.fromEntries(pairs));
        historyCache.current = { storageId: storage.id, loadedAt: Date.now() };
      }
    }).catch((requestError) => {
      if (!controller.signal.aborted) setError(requestError instanceof Error ? requestError.message : "Path graphs could not be loaded.");
    });
    return () => controller.abort();
  }, [entities, logicalEntity, storage.id, tab]);

  const samples = useMemo(() => new Map(current.map((sample) => [`${sample.entity.id}:${sample.metric_id}`, sample])), [current]);
  const summary = storage.redundancy_summary ?? {
    healthy_paths: storage.paths.filter((path) => path.active).length,
    active_paths: storage.paths.filter((path) => path.active).length,
    failed_paths: storage.paths.filter((path) => !path.active).length,
    failovers_today: 0,
    last_failover: null,
    time_degraded_seconds: 0,
  };
  const logicalSample = (metricId: string): MetricSampleDocument | undefined => logicalEntity
    ? samples.get(`${logicalEntity.id}:${metricId}`)
    : undefined;
  const nodeRole = storage.ownership_state
    ? storage.ownership_state.replaceAll("_", " ")
    : "Not reported";

  return <section className="redundancy-management" aria-label={`${storage.name} controller redundancy`}>
    <header className="redundancy-hero">
      <div><small>ADVANCED STORAGE</small><h2>{storage.name}</h2><p>{storage.mountpoint} · {humanCapacity(storage.capacity_bytes)}{storage.node_name ? ` · ${storage.node_name}` : ""}{storage.storage_scope === "external_shared" ? " · Shared storage" : ""}</p></div>
      <div className="redundancy-hero-state"><StatusBadge status={stateLabel(storage)} /><strong>{summary.healthy_paths} / {storage.paths.length} paths healthy</strong></div>
    </header>
    <nav className="redundancy-tabs" aria-label="Controller redundancy sections">{([
      ["overview", "Overview"], ["paths", "Controllers & paths"], ["performance", "Performance"], ["events", "Events"], ["settings", "Advanced settings"],
    ] as Array<[DetailTab, string]>).map(([value, label]) => <button type="button" aria-current={tab === value ? "page" : undefined} className={tab === value ? "is-active" : ""} key={value} onClick={() => setTab(value)}>{label}</button>)}</nav>
    {error && <Notice tone="warning" title="Some controller details are unavailable">{error}</Notice>}
    {loading && <p>Loading controller and path history…</p>}
    {storage.topology_state === "failed_over" && <Notice tone="warning" title="Storage has failed over">Storage is online through an alternate controller path. The mount and application paths have not changed.</Notice>}
    {storage.topology_state === "no_path" && <Notice tone="danger" title="Storage has no usable path">The logical storage is currently inaccessible.</Notice>}

    {tab === "overview" && <>
      <div className="redundancy-kpis">
        {storage.storage_scope === "external_shared" && <article><small>Current node</small><strong>{storage.node_name ?? "Not reported"}</strong></article>}
        {storage.storage_scope === "external_shared" && <article><small>Storage role</small><strong>{nodeRole}</strong></article>}
        {storage.storage_scope === "external_shared" && <article><small>Peer node</small><strong>{storage.peer_node ?? "Not reported"}</strong></article>}
        <article><small>Redundancy</small><strong>{stateLabel(storage)}</strong></article>
        <article><small>Healthy paths</small><strong>{summary.healthy_paths} / {storage.paths.length}</strong></article>
        <article><small>Active paths</small><strong>{summary.active_paths}</strong></article>
        <article><small>Failed paths</small><strong>{summary.failed_paths}</strong></article>
        <article><small>Failovers today</small><strong>{summary.failovers_today}</strong></article>
        <article><small>Last failover</small><strong>{summary.last_failover ? new Date(summary.last_failover).toLocaleString() : "Never"}</strong></article>
        <article><small>Time degraded</small><strong>{summary.time_degraded_seconds ? `${Math.floor(summary.time_degraded_seconds / 3600)}h ${Math.floor(summary.time_degraded_seconds % 3600 / 60)}m` : "None"}</strong></article>
        <LogicalMetric label="Logical read throughput" sample={logicalSample("io.read.bytes_per_second")} />
        <LogicalMetric label="Logical write throughput" sample={logicalSample("io.write.bytes_per_second")} />
        <LogicalMetric label="Logical read IOPS" sample={logicalSample("io.read.iops")} />
        <LogicalMetric label="Logical write IOPS" sample={logicalSample("io.write.iops")} />
        <LogicalMetric label="Logical read latency" sample={logicalSample("io.read.latency")} />
        <LogicalMetric label="Logical write latency" sample={logicalSample("io.write.latency")} />
      </div>
      <p className="field-hint">Logical totals and response time are shown only from an authoritative logical-storage observation. Per-path counters below are never summed because active paths can observe the same I/O.</p>
      {storage.storage_scope === "external_shared" && <p className="field-hint">This view shows telemetry collected by {storage.node_name ?? "this node"}. Open {storage.peer_node ?? "the peer node"} to compare its live activity; Hoardarr does not infer peer IO or ownership from this node's counters.</p>}
      <Topology storage={storage} />
      <div className="redundancy-actions">
        <button type="button" className="button button-primary" onClick={() => onAction("add")}>Add redundant path</button>
        {storage.paths.length > 1 && <button type="button" className="button button-secondary" onClick={() => onAction("replace")}>Replace controller/path</button>}
        {storage.paths.length > 1 && <button type="button" className="button button-danger" onClick={() => onAction("remove")}>Remove redundancy</button>}
      </div>
    </>}

    {tab === "paths" && <div className="controller-card-grid">{storage.paths.map((path, index) => <article className="controller-card" key={path.id}>
      <header><div><small>CONTROLLER {index + 1}</small><h3>{path.controller?.model ?? "Controller not reported"}</h3></div><StatusBadge status={path.active ? "healthy" : path.state} /></header>
      <dl>
        <div><dt>Controller ID</dt><dd><code>{path.controller?.stable_identity ?? "Not reported"}</code></dd></div>
        <div><dt>Provider</dt><dd>{display(path.controller?.provider)}</dd></div>
        <div><dt>Vendor</dt><dd>{display(path.controller?.state?.vendor)}</dd></div>
        <div><dt>Firmware</dt><dd>{display(path.controller?.state?.firmware)}</dd></div>
        <div><dt>Protocol</dt><dd>{display(path.protocol)}</dd></div>
        <div><dt>Linux path</dt><dd><code>{path.kernel_path}</code></dd></div>
        <div><dt>Path identity</dt><dd><code>{path.stable_path_identity}</code></dd></div>
        <div><dt>WWID</dt><dd><code>{storage.stable_identity}</code></dd></div>
        <div><dt>Access</dt><dd>{path.optimized === true ? "Optimized" : path.optimized === false ? "Non-optimized" : "Not reported"}</dd></div>
        <div><dt>Negotiated speed</dt><dd>{display(path.metadata?.negotiated_speed)}</dd></div>
        <div><dt>Capable speed</dt><dd>{display(path.metadata?.capable_speed)}</dd></div>
        <div><dt>H:C:T:L</dt><dd>{display(path.metadata?.hctl)}</dd></div>
        <div><dt>Initiator</dt><dd>{display(path.metadata?.initiator)}</dd></div>
        <div><dt>Target</dt><dd>{display(path.metadata?.target)}</dd></div>
      </dl>
      {entities.filter((entity) => entity.stable_id.endsWith(path.stable_path_identity)).map((entity) => <PathLiveMetrics key={entity.id} entity={entity} samples={samples} />)}
    </article>)}</div>}

    {tab === "performance" && <div className="redundancy-graphs">{METRICS.map((metric) => <PathGraph key={metric.id} label={metric.label} unit={metric.unit} events={events} series={entities.map((entity) => ({ entity, history: histories[`${entity.id}:${metric.id}`] })).filter((item): item is { entity: MetricEntity; history: MetricHistoryDocument } => Boolean(item.history))} />)}
      {PATH_COUNT_METRICS.map((metric) => <PathGraph key={metric.id} label={metric.label} unit={metric.unit} events={events} series={logicalEntity && histories[`${logicalEntity.id}:${metric.id}`] ? [{ entity: logicalEntity, history: histories[`${logicalEntity.id}:${metric.id}`] }] : []} />)}
      <section className="path-state-history"><h3>Path state history</h3>{events.length === 0 ? <p>No controller-path changes have been recorded.</p> : <ol>{events.filter((event) => event.path_id || event.event_type.includes("redundancy") || event.event_type.includes("failover")).slice(0, 20).map((event) => <li key={event.id}><time>{new Date(event.occurred_at).toLocaleString()}</time><strong>{event.event_type.replaceAll("_", " ")}</strong><span>{event.previous_state ? `${event.previous_state} → ` : ""}{event.resulting_state}</span></li>)}</ol>}</section>
    </div>}

    {tab === "events" && <section className="redundancy-events"><h3>Controller and path events</h3>{events.length === 0 ? <p className="empty-state compact-empty">No controller-path changes recorded.</p> : <ol>{events.map((event) => <li key={event.id}><span className={`event-dot event-${event.event_type}`} aria-hidden="true" /><div><strong>{event.event_type.replaceAll("_", " ")}</strong><time>{new Date(event.occurred_at).toLocaleString()}</time><p>{event.previous_state ? `${event.previous_state} → ` : ""}{event.resulting_state}{event.operation_id ? ` · Operation ${event.operation_id}` : ""}</p></div></li>)}</ol>}</section>}

    {tab === "settings" && settings && <form className="redundancy-settings" onSubmit={(event) => { event.preventDefault(); onAction("configure", settings); }}>
      <Notice tone="info" title="Recommended settings">Hoardarr uses provider-safe path grouping and failover behavior. Choose Customize to change supported multipath values.</Notice>
      <label><input type="radio" name="redundancy-mode" checked={settings.mode === "recommended"} onChange={() => setSettings({ ...settings, mode: "recommended" })} /> Use storage-recommended settings</label>
      <label><input type="radio" name="redundancy-mode" checked={settings.mode === "custom"} onChange={() => setSettings({ ...settings, mode: "custom" })} /> Customize</label>
      {settings.mode === "custom" && <div className="settings-grid">
        <label>Path grouping<select value={settings.path_grouping_policy} onChange={(event) => setSettings({ ...settings, path_grouping_policy: event.target.value as StorageRedundancySettings["path_grouping_policy"] })}><option value="group_by_prio">Honor optimized path groups</option><option value="failover">Use one path at a time</option><option value="multibus">Use all active paths</option></select><small>Linux: {settings.path_grouping_policy}</small></label>
        <label>Path selector<select value={settings.path_selector} onChange={(event) => setSettings({ ...settings, path_selector: event.target.value as StorageRedundancySettings["path_selector"] })}><option value="service-time 0">Prefer service time</option><option value="round-robin 0">Round robin</option><option value="queue-length 0">Prefer shorter queue</option></select><small>Linux: {settings.path_selector}</small></label>
        <label>Failback<select value={settings.failback} onChange={(event) => setSettings({ ...settings, failback: event.target.value as StorageRedundancySettings["failback"] })}><option value="followover">Follow the active controller</option><option value="immediate">Return immediately</option><option value="manual">Manual</option></select><small>Linux: {settings.failback}</small></label>
        <label>When every path is lost<select value={settings.no_path_retry} onChange={(event) => setSettings({ ...settings, no_path_retry: event.target.value as StorageRedundancySettings["no_path_retry"] })}><option value="fail">Return an error</option><option value="queue_30">Wait for 30 checks</option><option value="queue">Wait without a limit</option></select><small>Linux: {settings.no_path_retry}</small></label>
      </div>}
      <fieldset><legend>Alerts</legend>{(["alert_on_reduced", "alert_on_failover", "alert_on_path_flapping", "alert_on_total_loss"] as const).map((field) => <label key={field}><input type="checkbox" checked={settings[field]} onChange={(event) => setSettings({ ...settings, [field]: event.target.checked })} /> {field.replace("alert_on_", "").replaceAll("_", " ")}</label>)}</fieldset>
      <details><summary>Exact resulting settings</summary><pre>{JSON.stringify(settings, null, 2)}</pre></details>
      <button className="button button-primary" type="submit" disabled={storage.paths.length < 2}>Review and apply settings</button>
    </form>}
  </section>;
}
