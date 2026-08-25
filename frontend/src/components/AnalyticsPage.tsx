import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import { historyEnvelopeValues, historyHasCategoricalValues, historyMeanValues, nullablePath, qualityHasValue, qualityLabel, sampleClassification, stateTimeline } from "../metricHistory";
import type { EntitlementDocument, LatencyAnalyticsDocument, MetricAlertDocument, MetricDefinition, MetricEntity, MetricHistoryDocument, MetricSampleDocument, TelemetryForecastDocument, TelemetrySettingsDocument } from "../types";
import { Notice } from "./ui";

const REFRESH_MS = 5_000;
const MAX_DISPLAY_POINTS = 800;

function displayValue(value: number | string | null, unit: string): string {
  if (value === null) return "Not reported";
  if (typeof value === "string") return value.replaceAll("_", " ");
  if (unit === "bytes" || unit === "bytes_per_second") {
    const suffix = unit === "bytes_per_second" ? "/s" : "";
    const units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"];
    let amount = value;
    let index = 0;
    while (Math.abs(amount) >= 1024 && index < units.length - 1) {
      amount /= 1024;
      index += 1;
    }
    return `${amount.toLocaleString(undefined, { maximumFractionDigits: 1 })} ${units[index]}${suffix}`;
  }
  const suffixes: Record<string, string> = { percent: "%", milliseconds: " ms", celsius: " °C", operations_per_second: " IOPS", bits_per_second: " b/s", seconds: " s", hours: " h", rpm: " RPM" };
  return `${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}${suffixes[unit] ?? ""}`;
}

function qualityText(value: MetricSampleDocument): string {
  return value.quality === "available" ? "Available · live observation" : qualityLabel(value.quality);
}

function bars(values: Array<number | null>, domain?: { minimum: number; maximum: number }): Array<{ x: number; y: number; height: number } | null> {
  const available = values.filter((value): value is number => value !== null);
  if (!available.length) return values.map(() => null);
  const minimum = domain?.minimum ?? Math.min(...available);
  const spread = Math.max(1e-9, (domain?.maximum ?? Math.max(...available)) - minimum);
  return values.map((value, index) => value === null ? null : {
    x: index * 100 / values.length,
    y: 29 - (value - minimum) / spread * 27,
    height: Math.max(1, (value - minimum) / spread * 27),
  });
}

function MetricHelp({ metric }: { metric: MetricDefinition }) {
  return <details className="metric-help"><summary>About this metric</summary><dl><div><dt>Source</dt><dd>{metric.source}</dd></div><div><dt>Collected</dt><dd>Every {metric.minimum_interval_seconds} seconds or slower when the source is expensive</dd></div><div><dt>Value</dt><dd>{metric.kind === "derived" ? "Calculated" : "Reported"} · {metric.unit.replaceAll("_", " ")}</dd></div>{metric.formula && <div><dt>Calculation</dt><dd>{metric.formula}</dd></div>}<div><dt>Availability</dt><dd>{metric.availability}</dd></div>{metric.implementation_status && <div><dt>Support</dt><dd>{metric.implementation_status.toLowerCase().replaceAll("_", " ")}</dd></div>}</dl></details>;
}

function MetricProvenance({ sample, definition }: { sample: MetricSampleDocument; definition?: MetricDefinition }) {
  const classification = sampleClassification(sample, definition);
  const reason = sample.error_code
    ? sample.error_code.replaceAll("_", " ")
    : sample.quality === "unsupported"
      ? "The provider does not support this metric."
      : sample.quality === "not_reported"
        ? "The provider did not report a value."
        : sample.quality === "temporarily_unavailable"
          ? "The provider could not collect this observation."
          : sample.quality === "stale"
            ? "The last observation is older than the live freshness limit."
            : null;
  return <details className="metric-provenance"><summary>Source and quality</summary><dl>
    <div><dt>Provider</dt><dd>{sample.provenance?.provider ?? sample.source}</dd></div>
    <div><dt>Observed</dt><dd>{new Date(sample.provenance?.observed_at ?? sample.timestamp).toLocaleString()}</dd></div>
    <div><dt>Collection interval</dt><dd>{sample.provenance?.collection_interval_seconds ?? sample.collection_interval_seconds} seconds</dd></div>
    <div><dt>Unit</dt><dd>{sample.provenance?.unit ?? sample.unit}</dd></div>
    <div><dt>Classification</dt><dd>{classification}</dd></div>
    <div><dt>Quality</dt><dd>{qualityLabel(sample.quality)}</dd></div>
    {reason && <div><dt>Unavailable reason</dt><dd>{reason}</dd></div>}
    {(classification === "derived" || classification === "estimated") && <div><dt>Methodology</dt><dd>{definition?.formula ?? (classification === "estimated" ? "Provider estimate; exact inputs were not reported." : "Derived by the named provider from its reported inputs.")}</dd></div>}
  </dl></details>;
}

function NumericHistory({ history, label, graphType, unit }: { history: MetricHistoryDocument; label: string; graphType: "line" | "bars"; unit: string }) {
  const values = historyMeanValues(history);
  const envelope = historyEnvelopeValues(history);
  const domainValues = [...values, ...(envelope?.minimum ?? []), ...(envelope?.maximum ?? [])].filter((value): value is number => value !== null);
  const domain = domainValues.length ? { minimum: Math.min(...domainValues), maximum: Math.max(...domainValues) } : undefined;
  const gapCount = values.filter((value) => value === null).length;
  const descriptionId = `history-description-${history.metric_id.replaceAll(".", "-")}`;
  return <figure className={`analytics-chart graph-${graphType}`}>
    <figcaption>{label}<small>{history.raw === false ? "Bucket mean with peak-preserving minimum/maximum boundaries" : "Raw observations"}</small></figcaption>
    {domainValues.length ? <svg viewBox="0 0 100 30" preserveAspectRatio="none" role="img" aria-label={`${label} numeric history`} aria-describedby={descriptionId}>
      {envelope && <g className="rollup-envelope" aria-hidden="true"><path className="rollup-minimum" d={nullablePath(envelope.minimum, 30, 2, domain)} /><path className="rollup-maximum" d={nullablePath(envelope.maximum, 30, 2, domain)} /></g>}
      {graphType === "line" ? <path className="history-mean" d={nullablePath(values, 30, 2, domain)} /> : bars(values, domain).map((bar, index) => bar === null ? null : <rect key={history.points[index].timestamp} x={bar.x} y={bar.y} width={Math.max(.2, 90 / history.points.length)} height={bar.height} />)}
    </svg> : <p className="empty-state">No reported numeric values are available in these buckets.</p>}
    <p className="visually-hidden" id={descriptionId}>{history.points.length} bounded points. {gapCount} unavailable values are gaps, not zero. {history.raw === false ? "Minimum and maximum boundaries preserve bucket peaks around the mean." : "No rollup envelope is drawn for raw observations."}</p>
    <div><span>{new Date(history.start).toLocaleString()}</span><span>{new Date(history.end).toLocaleString()}</span></div>
    <details className="history-buckets"><summary>Accessible bucket values</summary><div className="table-scroll"><table className="data-table"><thead><tr><th scope="col">Time</th><th scope="col">{history.raw === false ? "Mean" : "Value"}</th>{history.raw === false && <><th scope="col">Min / max</th><th scope="col">First / last</th><th scope="col">Samples</th></>}<th scope="col">Quality</th></tr></thead><tbody>{history.points.map((point) => <tr key={point.timestamp}><td>{new Date(point.timestamp).toLocaleString()}</td><td>{displayValue(qualityHasValue(point.quality) ? point.mean ?? point.value : null, unit)}</td>{history.raw === false && <><td>{displayValue(point.minimum ?? null, unit)} / {displayValue(point.maximum ?? null, unit)}</td><td>{displayValue(point.first ?? null, unit)} / {displayValue(point.last ?? null, unit)}</td><td>{point.sample_count ?? "Not reported"}</td></>}<td>{qualityLabel(point.quality)}</td></tr>)}</tbody></table></div></details>
  </figure>;
}

function CategoricalHistory({ history, label }: { history: MetricHistoryDocument; label: string }) {
  const buckets = stateTimeline(history);
  return <section className="state-history" aria-label={`${label} state timeline`}>
    <h4>{label} state timeline</h4>
    <p>States are shown in observed order. They are never averaged or converted to invented numeric values.</p>
    <ol>{buckets.map((bucket) => <li key={bucket.timestamp} className={`quality-${bucket.quality}`}><time>{new Date(bucket.timestamp).toLocaleString()}</time><div>{bucket.states.length ? bucket.states.map((state, index) => <span key={`${state}:${index}`}>{state.replaceAll("_", " ")}{index < bucket.states.length - 1 ? <b aria-label="then">→</b> : null}</span>) : <span>{qualityLabel(bucket.quality)}</span>}</div><small>{bucket.transitionCount} transition{bucket.transitionCount === 1 ? "" : "s"} · {qualityLabel(bucket.quality)}</small></li>)}</ol>
  </section>;
}

function HistoryDiagnostics({ history }: { history: MetricHistoryDocument }) {
  const rolled = history.raw === false;
  const interval = history.points.find((point) => point.interval_seconds)?.interval_seconds;
  return <details className="graph-diagnostics"><summary>Graph details</summary><dl>
    <div><dt>Range</dt><dd>{new Date(history.start).toLocaleString()} – {new Date(history.end).toLocaleString()}</dd></div>
    <div><dt>Metric source</dt><dd>{history.metric_source ?? history.points.find((point) => point.source)?.source ?? "Not reported"}</dd></div>
    <div><dt>Resolution</dt><dd>{history.source_resolution ?? history.resolution}{interval ? ` · ${interval} second buckets` : ""}</dd></div>
    <div><dt>Representation</dt><dd>{rolled ? "Historical rollup; values are aggregates" : "Raw observations"}</dd></div>
    <div><dt>Points</dt><dd>{history.points_returned ?? history.points.length} returned · {history.displayed_points ?? history.points.length} displayed · maximum {history.maximum_points ?? MAX_DISPLAY_POINTS}</dd></div>
    {history.aggregation_method && <div><dt>Aggregation</dt><dd>{history.aggregation_method}</dd></div>}
    <div><dt>Metric classification</dt><dd>{history.metric_kind ?? "Not reported"}</dd></div>
    {history.formula && <div><dt>Formula</dt><dd>{history.formula}</dd></div>}
  </dl></details>;
}

export function AnalyticsPage() {
  const [definitions, setDefinitions] = useState<MetricDefinition[]>([]);
  const [entities, setEntities] = useState<MetricEntity[]>([]);
  const [metrics, setMetrics] = useState<MetricSampleDocument[]>([]);
  const [alerts, setAlerts] = useState<MetricAlertDocument[]>([]);
  const [entitlements, setEntitlements] = useState<EntitlementDocument | null>(null);
  const [historySettings, setHistorySettings] = useState<TelemetrySettingsDocument | null>(null);
  const [selectedEntity, setSelectedEntity] = useState("");
  const [selectedMetric, setSelectedMetric] = useState("io.read.bytes_per_second");
  const [rangeHours, setRangeHours] = useState(24);
  const [history, setHistory] = useState<MetricHistoryDocument | null>(null);
  const [forecast, setForecast] = useState<TelemetryForecastDocument | null>(null);
  const [latency, setLatency] = useState<LatencyAnalyticsDocument | null>(null);
  const [topItems, setTopItems] = useState<MetricSampleDocument[]>([]);
  const [anomalies, setAnomalies] = useState<Array<Record<string, unknown>>>([]);
  const [graphType, setGraphType] = useState<"line" | "bars">("line");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [alertBusy, setAlertBusy] = useState<string | null>(null);
  const refreshController = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    refreshController.current?.abort();
    const controller = new AbortController();
    refreshController.current = controller;
    try {
      const [catalog, entityItems, current, alertItems] = await Promise.all([
        api.metricCatalog(controller.signal), api.metricEntities(undefined, controller.signal),
        api.currentMetrics({}, controller.signal), api.metricAlerts("active", controller.signal),
      ]);
      setDefinitions(catalog.items);
      setEntitlements(catalog.entitlements);
      setEntities(entityItems);
      setMetrics(current.items);
      setAlerts(alertItems);
      setSelectedEntity((value) => value || entityItems[0]?.id || "");
      setError(null);
    } catch (reason) {
      if (reason instanceof DOMException && reason.name === "AbortError") return;
      setError(reason instanceof Error ? reason.message : "Storage analytics could not be loaded.");
    } finally {
      if (refreshController.current === controller) {
        refreshController.current = null;
        setLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    void refresh();
    const settingsController = new AbortController();
    void api.telemetrySettings(settingsController.signal).then(setHistorySettings).catch((reason) => {
      if (!(reason instanceof DOMException && reason.name === "AbortError")) setHistorySettings(null);
    });
    const timer = window.setInterval(() => { if (document.visibilityState === "visible") void refresh(); }, REFRESH_MS);
    return () => {
      window.clearInterval(timer);
      settingsController.abort();
      refreshController.current?.abort();
      refreshController.current = null;
    };
  }, [refresh]);

  async function updateAlert(action: "acknowledge" | "suppress" | "unsuppress", alertId: string): Promise<void> {
    setAlertBusy(`${action}:${alertId}`);
    setError(null);
    try {
      const updated = action === "acknowledge"
        ? await api.acknowledgeMetricAlert(alertId)
        : action === "suppress"
          ? await api.suppressMetricAlert(alertId, 60, "Temporarily suppressed from Storage Analytics")
          : await api.unsuppressMetricAlert(alertId);
      setAlerts((items) => items.map((item) => item.id === updated.id ? updated : item));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The alert could not be updated.");
    } finally {
      setAlertBusy(null);
    }
  }

  useEffect(() => {
    if (!selectedEntity || !selectedMetric) {
      setHistory(null);
      return;
    }
    const end = new Date();
    const start = new Date(end.getTime() - rangeHours * 3_600_000);
    const controller = new AbortController();
    void api.metricHistory({ entityId: selectedEntity, metricId: selectedMetric, start: start.toISOString(), end: end.toISOString(), resolution: "auto", maximumPoints: MAX_DISPLAY_POINTS, signal: controller.signal })
      .then(setHistory)
      .catch((reason) => {
        if (reason instanceof DOMException && reason.name === "AbortError") return;
        setHistory(null);
        setError(reason instanceof Error ? reason.message : "Metric history could not be loaded.");
      });
    return () => controller.abort();
  }, [selectedEntity, selectedMetric, rangeHours]);

  const capabilityKey = entitlements?.capabilities.join("|") ?? "";
  const selectedEntityType = entities.find((item) => item.id === selectedEntity)?.entity_type;

  useEffect(() => {
    setForecast(null);
    setLatency(null);
    setTopItems([]);
    setAnomalies([]);
    if (!entitlements || !selectedEntity) return;
    const controller = new AbortController();
    const capabilities = new Set(entitlements.capabilities);
    if (capabilities.has("metrics.analytics.performance")) {
      void api.topMetrics(selectedMetric, "highest", controller.signal).then(setTopItems).catch((reason) => {
        if (!(reason instanceof DOMException && reason.name === "AbortError")) setTopItems([]);
      });
      if (selectedMetric === "io.read.latency" || selectedMetric === "io.write.latency") {
        void api.latencyAnalytics(selectedEntity, selectedMetric, controller.signal).then(setLatency).catch((reason) => {
          if (!(reason instanceof DOMException && reason.name === "AbortError")) setLatency(null);
        });
      }
    }
    if (selectedEntityType === "drive" && capabilities.has("metrics.analytics.endurance")) {
      void api.enduranceForecast(selectedEntity, controller.signal).then((value) => setForecast(value.forecast)).catch((reason) => {
        if (!(reason instanceof DOMException && reason.name === "AbortError")) setForecast(null);
      });
    } else if (capabilities.has("metrics.analytics.capacity")) {
      void api.capacityForecast(selectedEntity, controller.signal).then((value) => setForecast(value.forecast)).catch((reason) => {
        if (!(reason instanceof DOMException && reason.name === "AbortError")) setForecast(null);
      });
    }
    if (capabilities.has("metrics.analytics.anomaly")) {
      void api.telemetryAnomalies(controller.signal).then(setAnomalies).catch((reason) => {
        if (!(reason instanceof DOMException && reason.name === "AbortError")) setAnomalies([]);
      });
    }
    return () => controller.abort();
  }, [capabilityKey, selectedEntity, selectedEntityType, selectedMetric]);

  const definitionMap = useMemo(() => new Map(definitions.map((item) => [item.id, item])), [definitions]);
  const selectedDefinition = definitionMap.get(selectedMetric);
  const entityMetrics = metrics.filter((item) => !selectedEntity || item.entity.id === selectedEntity);
  const performance = entityMetrics.filter((item) => item.metric_id.startsWith("io.")).slice(0, 8);
  const capacity = entityMetrics.filter((item) => item.metric_id.startsWith("capacity.") || item.metric_id.startsWith("storage.")).slice(0, 6);
  const health = entityMetrics.filter((item) => item.metric_id.startsWith("health.") || item.metric_id.startsWith("drive.")).slice(0, 8);
  const categoricalHistory = history ? selectedDefinition?.unit === "state" || historyHasCategoricalValues(history) : false;

  function metricCards(items: MetricSampleDocument[]) {
    if (!items.length) return <p className="empty-state">No readings have been reported for this entity.</p>;
    return <div className="analytics-kpi-grid">{items.map((item) => {
      const definition = definitionMap.get(item.metric_id);
      return <article className={`analytics-kpi quality-${item.quality}`} key={`${item.entity.id}:${item.metric_id}`}><div><span>{item.name}</span><small>{qualityText(item)}</small></div><strong>{displayValue(qualityHasValue(item.quality) ? item.value : null, item.unit)}</strong><small>{item.entity.display_name} · {new Date(item.timestamp).toLocaleTimeString()}</small><MetricProvenance sample={item} definition={definition} />{definition && <MetricHelp metric={definition} />}</article>;
    })}</div>;
  }

  return <section className="analytics-page" aria-label="Storage analytics">
    <div className="page-actions"><div><h2>Storage Analytics</h2><p>Current performance, capacity, health, and reported hardware data.</p></div><button className="button button-secondary" type="button" onClick={() => void refresh()} disabled={loading}>{loading ? "Refreshing…" : "Refresh"}</button></div>
    {error && <Notice tone="danger" title="Analytics unavailable">{error}</Notice>}
    {entitlements && entitlements.state !== "valid" && <Notice tone="info" title="Basic analytics active">Current health and performance remain available. Forecasts, extended history, correlation, and export require their capability.</Notice>}
    <div className="analytics-controls"><label>Storage item<select value={selectedEntity} onChange={(event) => setSelectedEntity(event.target.value)}><option value="">All storage</option>{entities.map((entity) => <option key={entity.id} value={entity.id}>{entity.display_name} ({entity.entity_type.replaceAll("_", " ")})</option>)}</select></label><label>Metric<select value={selectedMetric} onChange={(event) => setSelectedMetric(event.target.value)}>{definitions.filter((item) => item.entitled).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><label>Time range<select value={rangeHours} onChange={(event) => setRangeHours(Number(event.target.value))}><option value={0.083333}>Live</option><option value={1}>1 hour</option><option value={24}>24 hours</option><option value={168}>7 days</option><option value={720}>30 days</option>{historySettings?.extended_history.entitled && <option value={historySettings.history.long_retention_days * 24}>Longest available</option>}</select></label><label>Graph<select value={graphType} onChange={(event) => setGraphType(event.target.value as "line" | "bars")}><option value="line">Line</option><option value="bars">Bars</option></select></label></div>
    <section className="analytics-section"><h3>Performance</h3>{metricCards(performance)}</section>
    <section className="analytics-section"><h3>Capacity</h3>{metricCards(capacity)}</section>
    <section className="analytics-section"><h3>Health and endurance</h3>{metricCards(health)}</section>
    <section className="analytics-section"><div className="section-title-row"><h3>History</h3>{history && <span>{history.points.length} points</span>}</div>{history?.points.length ? <>{categoricalHistory ? <CategoricalHistory history={history} label={selectedDefinition?.name ?? selectedMetric} /> : <NumericHistory history={history} label={selectedDefinition?.name ?? selectedMetric} graphType={graphType} unit={selectedDefinition?.unit ?? history.unit} />}<HistoryDiagnostics history={history} /></> : <p className="empty-state">No stored readings are available for this selection.</p>}{selectedDefinition && <MetricHelp metric={selectedDefinition} />}</section>
    {(forecast || latency || topItems.length > 0 || anomalies.length > 0) && <section className="analytics-section"><h3>Advanced analysis</h3><div className="analytics-kpi-grid">{forecast && <article className="analytics-kpi"><span>Capacity forecast</span><strong>{forecast.status.replaceAll("_", " ")}</strong><small>{forecast.methodology}</small></article>}{latency && <article className="analytics-kpi"><span>Latency distribution</span><strong>{latency.p95 === null ? "Not reported" : `${latency.p95.toFixed(2)} ms P95`}</strong><small>{latency.samples} stored observations · P50 {latency.p50 ?? "Not reported"} · P99 {latency.p99 ?? "Not reported"}</small></article>}{topItems.slice(0, 5).map((item, index) => <article className="analytics-kpi" key={`top:${item.entity.id}`}><span>#{index + 1} {item.entity.display_name}</span><strong>{displayValue(item.value, item.unit)}</strong><small>{item.name}</small></article>)}</div>{anomalies.length > 0 && <div className="analytics-alert-list">{anomalies.slice(0, 10).map((item, index) => <article className="analytics-alert warning" key={`${String(item.metric_id)}:${index}`}><strong>{String((item.entity as { display_name?: string })?.display_name ?? "Storage")}</strong><span>{String(item.explanation ?? "Performance outside recent baseline")}</span></article>)}</div>}</section>}
    <section className="analytics-section"><h3>Active alerts</h3>{alerts.length ? <div className="analytics-alert-list">{alerts.map((alert) => <article key={alert.id} className={`analytics-alert ${alert.severity}`}><strong>{alert.entity.display_name}</strong><span>{definitionMap.get(alert.metric_id)?.name ?? alert.metric_id}</span><small>{alert.lifecycle_state.replaceAll("_", " ")} · Started {new Date(alert.started_at).toLocaleString()}</small>{alert.suppressed_until && alert.lifecycle_state === "suppressed" && <small>Suppressed until {new Date(alert.suppressed_until).toLocaleString()}</small>}{alert.runbook && <details><summary>What to do</summary><strong>{alert.runbook.title}</strong><p>{alert.runbook.summary}</p><ol>{alert.runbook.actions.map((action) => <li key={action}>{action}</li>)}</ol><small>Based on: {alert.runbook.evidence.join(", ")}</small></details>}<div className="form-actions">{alert.acknowledged_at === null && <button className="button button-secondary" type="button" disabled={alertBusy !== null} onClick={() => void updateAlert("acknowledge", alert.id)}>Acknowledge</button>}{alert.lifecycle_state === "suppressed" ? <button className="button button-secondary" type="button" disabled={alertBusy !== null} onClick={() => void updateAlert("unsuppress", alert.id)}>End suppression</button> : <button className="button button-secondary" type="button" disabled={alertBusy !== null} onClick={() => void updateAlert("suppress", alert.id)}>Suppress for 1 hour</button>}</div></article>)}</div> : <p className="empty-state">No active telemetry alerts.</p>}</section>
    <section className="analytics-section"><h3>History policy</h3>{historySettings ? <dl className="analytics-policy"><div><dt>Live collection</dt><dd>{historySettings.collection.fast_interval_seconds} seconds</dd></div><div><dt>Recent history</dt><dd>{historySettings.history.recent_resolution_seconds}-second detail for {historySettings.history.recent_retention_hours} hours</dd></div><div><dt>Medium history</dt><dd>Hourly for {historySettings.history.medium_retention_days} days</dd></div><div><dt>Long history</dt><dd>Daily for {historySettings.history.long_retention_days} days</dd></div><div><dt>Graph limit</dt><dd>{historySettings.history.maximum_graph_points.toLocaleString()} points</dd></div><div><dt>Telemetry database</dt><dd>{displayValue(historySettings.storage.database_bytes, "bytes")}</dd></div><div><dt>Estimated growth</dt><dd>{displayValue(historySettings.storage.estimated_bytes_per_day, "bytes")}/day (estimate)</dd></div><div><dt>Oldest history</dt><dd>{historySettings.storage.oldest_retained_history ? new Date(historySettings.storage.oldest_retained_history).toLocaleString() : "Not reported"}</dd></div><div><dt>Next cleanup</dt><dd>{historySettings.storage.next_cleanup ? new Date(historySettings.storage.next_cleanup).toLocaleString() : "Not reported"}</dd></div><div><dt>Extended history</dt><dd>{historySettings.extended_history.entitled ? "Available" : "Unavailable"}</dd></div></dl> : <p className="empty-state">History settings are temporarily unavailable.</p>}</section>
    <section className="analytics-section"><h3>Advanced capabilities</h3><div className="capability-grid">{definitions.filter((item) => item.capability).reduce<string[]>((items, metric) => metric.capability && !items.includes(metric.capability) ? [...items, metric.capability] : items, []).map((capability) => <div key={capability}><span className={entitlements?.capabilities.includes(capability) ? "capability-on" : "capability-off"}>{entitlements?.capabilities.includes(capability) ? "Available" : "Unavailable"}</span><strong>{capability.replaceAll("metrics.", "").replaceAll(".", " ")}</strong></div>)}</div></section>
  </section>;
}
