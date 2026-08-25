import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import {
  applicableMetricDefinitions,
  applicableMetricSelection,
  historyHasCategoricalValues,
  qualityHasValue,
  qualityLabel,
  resolveMetricHistoryEntity,
} from "../metricHistory";
import type { MetricHistoryContext } from "../metricHistory";
import type { EntitlementDocument, LatencyAnalyticsDocument, MetricAlertDocument, MetricDefinition, MetricEntity, MetricHistoryDocument, MetricSampleDocument, TelemetryForecastDocument, TelemetrySettingsDocument } from "../types";
import {
  CategoricalMetricHistory,
  formatMetricValue,
  MAX_HISTORY_DISPLAY_POINTS,
  MetricDefinitionHelp,
  MetricHistoryDiagnostics,
  MetricSampleProvenance,
  NumericMetricHistory,
} from "./MetricHistoryPresentation";
import { Notice } from "./ui";

const REFRESH_MS = 5_000;

function qualityText(value: MetricSampleDocument): string {
  return value.quality === "available" ? "Available · live observation" : qualityLabel(value.quality);
}
export function AnalyticsPage({ context }: { context?: MetricHistoryContext | null }) {
  const [definitions, setDefinitions] = useState<MetricDefinition[]>([]);
  const [entities, setEntities] = useState<MetricEntity[]>([]);
  const [metrics, setMetrics] = useState<MetricSampleDocument[]>([]);
  const [alerts, setAlerts] = useState<MetricAlertDocument[]>([]);
  const [entitlements, setEntitlements] = useState<EntitlementDocument | null>(null);
  const [historySettings, setHistorySettings] = useState<TelemetrySettingsDocument | null>(null);
  const [selectedEntity, setSelectedEntity] = useState("");
  const [selectedMetric, setSelectedMetric] = useState("");
  const [rangeHours, setRangeHours] = useState(24);
  const [history, setHistory] = useState<MetricHistoryDocument | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [forecast, setForecast] = useState<TelemetryForecastDocument | null>(null);
  const [latency, setLatency] = useState<LatencyAnalyticsDocument | null>(null);
  const [topItems, setTopItems] = useState<MetricSampleDocument[]>([]);
  const [anomalies, setAnomalies] = useState<Array<Record<string, unknown>>>([]);
  const [graphType, setGraphType] = useState<"line" | "bars">("line");
  const [manualContextOverrideKey, setManualContextOverrideKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [alertBusy, setAlertBusy] = useState<string | null>(null);
  const refreshController = useRef<AbortController | null>(null);
  const historyRequestSequence = useRef(0);

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
      setSelectedEntity((value) => {
        if (value && entityItems.some((entity) => entity.id === value)) return value;
        const contextualEntity = resolveMetricHistoryEntity(entityItems, context);
        if (context) return contextualEntity?.id ?? "";
        return entityItems[0]?.id ?? "";
      });
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
  }, [context]);

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

  const definitionMap = useMemo(() => new Map(definitions.map((item) => [item.id, item])), [definitions]);
  const selectedEntityRecord = entities.find((item) => item.id === selectedEntity);
  const selectedEntityType = selectedEntityRecord?.entity_type;
  const applicableDefinitions = useMemo(
    () => applicableMetricDefinitions(definitions, selectedEntityType),
    [definitions, selectedEntityType],
  );
  const selectedDefinition = definitionMap.get(selectedMetric);
  const contextKey = context
    ? `${context.sourceSurface}:${context.entityType}:${context.stableId ?? ""}:${context.displayName ?? ""}:${context.metricId ?? ""}`
    : "";
  const appliedContextKey = useRef("");
  const contextualEntity = resolveMetricHistoryEntity(entities, context);
  const contextualSelectionUnavailable = Boolean(context && !loading && !contextualEntity);
  const manualContextRecoveryActive = Boolean(
    contextualSelectionUnavailable
    && manualContextOverrideKey === contextKey
    && selectedEntityRecord,
  );
  const contextualSelectionBlocked = contextualSelectionUnavailable && !manualContextRecoveryActive;

  useEffect(() => {
    if (!entities.length || !definitions.length) return;
    if (context && appliedContextKey.current !== contextKey) {
      const contextualEntity = resolveMetricHistoryEntity(entities, context);
      appliedContextKey.current = contextKey;
      setManualContextOverrideKey("");
      if (contextualEntity) {
        setSelectedEntity(contextualEntity.id);
        setSelectedMetric(applicableMetricSelection(definitions, contextualEntity.entity_type, context.metricId));
      } else {
        setSelectedEntity("");
        setSelectedMetric("");
        setHistory(null);
        setHistoryLoading(false);
        setHistoryError(null);
        historyRequestSequence.current += 1;
      }
      return;
    }
    if (contextualSelectionUnavailable && manualContextOverrideKey !== contextKey) return;
    if (!selectedEntityRecord) {
      const firstEntity = entities[0];
      setSelectedEntity(firstEntity.id);
      setSelectedMetric(applicableMetricSelection(definitions, firstEntity.entity_type));
      return;
    }
    if (!applicableDefinitions.some((definition) => definition.id === selectedMetric)) {
      setSelectedMetric(applicableMetricSelection(definitions, selectedEntityRecord.entity_type));
    }
  }, [applicableDefinitions, context, contextKey, contextualSelectionUnavailable, definitions, entities, manualContextOverrideKey, selectedEntityRecord, selectedMetric]);

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
    const definitionApplies = selectedDefinition?.entitled
      && selectedDefinition.entity_types.includes(selectedEntityType ?? "");
    if (contextualSelectionBlocked || !selectedEntity || !selectedMetric || !definitionApplies) {
      historyRequestSequence.current += 1;
      setHistory(null);
      setHistoryLoading(false);
      setHistoryError(null);
      return;
    }
    const end = new Date();
    const start = new Date(end.getTime() - rangeHours * 3_600_000);
    const controller = new AbortController();
    const requestSequence = ++historyRequestSequence.current;
    setHistory(null);
    setHistoryError(null);
    setHistoryLoading(true);
    const maximumPoints = Math.min(historySettings?.history.maximum_graph_points ?? MAX_HISTORY_DISPLAY_POINTS, MAX_HISTORY_DISPLAY_POINTS);
    void api.metricHistory({ entityId: selectedEntity, metricId: selectedMetric, start: start.toISOString(), end: end.toISOString(), resolution: "auto", maximumPoints, signal: controller.signal })
      .then((document) => {
        if (historyRequestSequence.current === requestSequence) setHistory(document);
      })
      .catch((reason) => {
        if (reason instanceof DOMException && reason.name === "AbortError") return;
        if (historyRequestSequence.current === requestSequence) {
          setHistory(null);
          setHistoryError(reason instanceof Error ? reason.message : "Metric history could not be loaded.");
        }
      })
      .finally(() => {
        if (historyRequestSequence.current === requestSequence) setHistoryLoading(false);
      });
    return () => controller.abort();
  }, [contextualSelectionBlocked, historySettings?.history.maximum_graph_points, rangeHours, selectedDefinition, selectedEntity, selectedEntityType, selectedMetric]);

  const capabilityKey = entitlements?.capabilities.join("|") ?? "";

  useEffect(() => {
    setForecast(null);
    setLatency(null);
    setTopItems([]);
    setAnomalies([]);
    if (contextualSelectionBlocked || !entitlements || !selectedEntity || !selectedMetric || !selectedDefinition?.entity_types.includes(selectedEntityType ?? "")) return;
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
  }, [capabilityKey, contextualSelectionBlocked, selectedDefinition, selectedEntity, selectedEntityType, selectedMetric]);

  const entityMetrics = contextualSelectionBlocked ? [] : metrics.filter((item) => !selectedEntity || item.entity.id === selectedEntity);
  const performance = entityMetrics.filter((item) => item.metric_id.startsWith("io.")).slice(0, 8);
  const capacity = entityMetrics.filter((item) => item.metric_id.startsWith("capacity.") || item.metric_id.startsWith("storage.")).slice(0, 6);
  const health = entityMetrics.filter((item) => item.metric_id.startsWith("health.") || item.metric_id.startsWith("drive.")).slice(0, 8);
  const displayedHistory = contextualSelectionBlocked ? null : history;
  const categoricalHistory = displayedHistory ? selectedDefinition?.unit === "state" || historyHasCategoricalValues(displayedHistory) : false;

  function metricCards(items: MetricSampleDocument[]) {
    if (!items.length) return <p className="empty-state">No readings have been reported for this entity.</p>;
    return <div className="analytics-kpi-grid">{items.map((item) => {
      const definition = definitionMap.get(item.metric_id);
      return <article className={`analytics-kpi quality-${item.quality}`} key={`${item.entity.id}:${item.metric_id}`}><div><span>{item.name}</span><small>{qualityText(item)}</small></div><strong>{formatMetricValue(qualityHasValue(item.quality) ? item.value : null, item.unit)}</strong><small>{item.entity.display_name} · {new Date(item.timestamp).toLocaleTimeString()}</small><MetricSampleProvenance sample={item} definition={definition} />{definition && <MetricDefinitionHelp metric={definition} />}</article>;
    })}</div>;
  }

  const contextualSelectionActive = contextualEntity?.id === selectedEntity;
  const changeEntity = (entityId: string): void => {
    const nextEntity = entities.find((entity) => entity.id === entityId);
    if (contextualSelectionUnavailable) setManualContextOverrideKey(contextKey);
    setSelectedEntity(entityId);
    setSelectedMetric(applicableMetricSelection(definitions, nextEntity?.entity_type));
  };

  return <section className="analytics-page" aria-label="Storage analytics">
    <div className="page-actions"><div><h2>Storage Analytics</h2><p>Current performance, capacity, health, and reported hardware data.</p></div><button className="button button-secondary" type="button" onClick={() => void refresh()} disabled={loading}>{loading ? "Refreshing…" : "Refresh"}</button></div>
    {error && <Notice tone="danger" title="Analytics unavailable">{error}</Notice>}
    {contextualEntity && context && contextualSelectionActive && <Notice tone="info" title={`History for ${contextualEntity.display_name}`}>Opened from {context.sourceSurface.replaceAll("_", " ")}. This graph uses persisted backend telemetry for the exact {contextualEntity.entity_type.replaceAll("_", " ")} identity.</Notice>}
    {contextualSelectionUnavailable && <Notice tone="warning" title="Storage item is not currently reported">{manualContextRecoveryActive ? "The requested identity is still unavailable. Showing only the different item you selected explicitly; it is not a substitute for the requested storage." : <>The requested {context?.entityType.replaceAll("_", " ")} identity could not be matched safely. No history was requested for another item. Choose a reported item below to inspect it explicitly.</>}</Notice>}
    {entitlements && entitlements.state !== "valid" && <Notice tone="info" title="Basic analytics active">Current health and performance remain available. Forecasts, extended history, correlation, and export require their capability.</Notice>}
    <div className="analytics-controls"><label>Storage item<select value={contextualSelectionBlocked ? "" : selectedEntity} onChange={(event) => changeEntity(event.target.value)}>{contextualSelectionBlocked && <option value="">Choose a reported item</option>}{entities.map((entity) => <option key={entity.id} value={entity.id}>{entity.display_name} ({entity.entity_type.replaceAll("_", " ")})</option>)}</select></label><label>Metric<select value={contextualSelectionBlocked ? "" : selectedMetric} onChange={(event) => setSelectedMetric(event.target.value)} disabled={contextualSelectionBlocked || !applicableDefinitions.length}>{contextualSelectionBlocked && <option value="">Unavailable until an item is selected</option>}{applicableDefinitions.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><label>Time range<select value={rangeHours} onChange={(event) => setRangeHours(Number(event.target.value))} disabled={contextualSelectionBlocked}><option value={0.083333}>Live</option><option value={1}>1 hour</option><option value={24}>24 hours</option><option value={168}>7 days</option><option value={720}>30 days</option>{historySettings?.extended_history.entitled && <option value={historySettings.history.long_retention_days * 24}>Longest available</option>}</select></label><label>Graph<select value={graphType} onChange={(event) => setGraphType(event.target.value as "line" | "bars")} disabled={contextualSelectionBlocked}><option value="line">Line</option><option value="bars">Bars</option></select></label></div>
    <section className="analytics-section"><h3>Performance</h3>{metricCards(performance)}</section>
    <section className="analytics-section"><h3>Capacity</h3>{metricCards(capacity)}</section>
    <section className="analytics-section"><h3>Health and endurance</h3>{metricCards(health)}</section>
    <section className="analytics-section" aria-busy={historyLoading}><div className="section-title-row"><h3>Persistent history</h3>{displayedHistory && <span>{displayedHistory.points.length} points</span>}</div>{contextualSelectionBlocked ? <div className="empty-state"><p>No history was requested because the specified storage identity is unavailable.</p><small>Select a reported item explicitly to inspect different storage.</small></div> : historyLoading ? <p className="empty-state">Loading stored telemetry…</p> : historyError ? <Notice tone="danger" title="History unavailable">{historyError}</Notice> : displayedHistory?.points.length ? <>{categoricalHistory ? <CategoricalMetricHistory history={displayedHistory} label={selectedDefinition?.name ?? selectedMetric} /> : <NumericMetricHistory history={displayedHistory} label={selectedDefinition?.name ?? selectedMetric} graphType={graphType} unit={selectedDefinition?.unit ?? displayedHistory.unit} />}<MetricHistoryDiagnostics history={displayedHistory} definition={selectedDefinition} settings={historySettings} /></> : <div className="empty-state"><p>No stored readings are available for this selection.</p><small>Missing history is not shown as zero or idle.</small></div>}{!contextualSelectionBlocked && selectedDefinition && <MetricDefinitionHelp metric={selectedDefinition} />}</section>
    {!contextualSelectionBlocked && (forecast || latency || topItems.length > 0 || anomalies.length > 0) && <section className="analytics-section"><h3>Advanced analysis</h3><div className="analytics-kpi-grid">{forecast && <article className="analytics-kpi"><span>Capacity forecast</span><strong>{forecast.status.replaceAll("_", " ")}</strong><small>{forecast.methodology}</small></article>}{latency && <article className="analytics-kpi"><span>Latency distribution</span><strong>{latency.p95 === null ? "Not reported" : `${latency.p95.toFixed(2)} ms P95`}</strong><small>{latency.samples} stored observations · P50 {latency.p50 ?? "Not reported"} · P99 {latency.p99 ?? "Not reported"}</small></article>}{topItems.slice(0, 5).map((item, index) => <article className="analytics-kpi" key={`top:${item.entity.id}`}><span>#{index + 1} {item.entity.display_name}</span><strong>{formatMetricValue(item.value, item.unit)}</strong><small>{item.name}</small></article>)}</div>{anomalies.length > 0 && <div className="analytics-alert-list">{anomalies.slice(0, 10).map((item, index) => <article className="analytics-alert warning" key={`${String(item.metric_id)}:${index}`}><strong>{String((item.entity as { display_name?: string })?.display_name ?? "Storage")}</strong><span>{String(item.explanation ?? "Performance outside recent baseline")}</span></article>)}</div>}</section>}
    <section className="analytics-section"><h3>Active alerts</h3>{alerts.length ? <div className="analytics-alert-list">{alerts.map((alert) => <article key={alert.id} className={`analytics-alert ${alert.severity}`}><strong>{alert.entity.display_name}</strong><span>{definitionMap.get(alert.metric_id)?.name ?? alert.metric_id}</span><small>{alert.lifecycle_state.replaceAll("_", " ")} · Started {new Date(alert.started_at).toLocaleString()}</small>{alert.suppressed_until && alert.lifecycle_state === "suppressed" && <small>Suppressed until {new Date(alert.suppressed_until).toLocaleString()}</small>}{alert.runbook && <details><summary>What to do</summary><strong>{alert.runbook.title}</strong><p>{alert.runbook.summary}</p><ol>{alert.runbook.actions.map((action) => <li key={action}>{action}</li>)}</ol><small>Based on: {alert.runbook.evidence.join(", ")}</small></details>}<div className="form-actions">{alert.acknowledged_at === null && <button className="button button-secondary" type="button" disabled={alertBusy !== null} onClick={() => void updateAlert("acknowledge", alert.id)}>Acknowledge</button>}{alert.lifecycle_state === "suppressed" ? <button className="button button-secondary" type="button" disabled={alertBusy !== null} onClick={() => void updateAlert("unsuppress", alert.id)}>End suppression</button> : <button className="button button-secondary" type="button" disabled={alertBusy !== null} onClick={() => void updateAlert("suppress", alert.id)}>Suppress for 1 hour</button>}</div></article>)}</div> : <p className="empty-state">No active telemetry alerts.</p>}</section>
    <section className="analytics-section"><h3>History policy</h3>{historySettings ? <dl className="analytics-policy"><div><dt>Live collection</dt><dd>{historySettings.collection.fast_interval_seconds} seconds</dd></div><div><dt>Recent history</dt><dd>{historySettings.history.recent_resolution_seconds}-second detail for {historySettings.history.recent_retention_hours} hours</dd></div><div><dt>Medium history</dt><dd>Hourly for {historySettings.history.medium_retention_days} days</dd></div><div><dt>Long history</dt><dd>Daily for {historySettings.history.long_retention_days} days</dd></div><div><dt>Graph limit</dt><dd>{historySettings.history.maximum_graph_points.toLocaleString()} points</dd></div><div><dt>Telemetry database</dt><dd>{formatMetricValue(historySettings.storage.database_bytes, "bytes")}</dd></div><div><dt>Estimated growth</dt><dd>{formatMetricValue(historySettings.storage.estimated_bytes_per_day, "bytes")}/day (estimate)</dd></div><div><dt>Oldest history</dt><dd>{historySettings.storage.oldest_retained_history ? new Date(historySettings.storage.oldest_retained_history).toLocaleString() : "Not reported"}</dd></div><div><dt>Next cleanup</dt><dd>{historySettings.storage.next_cleanup ? new Date(historySettings.storage.next_cleanup).toLocaleString() : "Not reported"}</dd></div><div><dt>Extended history</dt><dd>{historySettings.extended_history.entitled ? "Available" : "Unavailable"}</dd></div></dl> : <p className="empty-state">History settings are temporarily unavailable.</p>}</section>
    <section className="analytics-section"><h3>Advanced capabilities</h3><div className="capability-grid">{definitions.filter((item) => item.capability).reduce<string[]>((items, metric) => metric.capability && !items.includes(metric.capability) ? [...items, metric.capability] : items, []).map((capability) => <div key={capability}><span className={entitlements?.capabilities.includes(capability) ? "capability-on" : "capability-off"}>{entitlements?.capabilities.includes(capability) ? "Available" : "Unavailable"}</span><strong>{capability.replaceAll("metrics.", "").replaceAll(".", " ")}</strong></div>)}</div></section>
  </section>;
}
