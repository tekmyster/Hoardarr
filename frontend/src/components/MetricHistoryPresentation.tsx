import {
  historyEnvelopeValues,
  historyMeanValues,
  nullablePath,
  qualityHasValue,
  qualityLabel,
  sampleClassification,
  stateTimeline,
} from "../metricHistory";
import type {
  MetricDefinition,
  MetricHistoryDocument,
  MetricSampleDocument,
  TelemetrySettingsDocument,
} from "../types";

export const MAX_HISTORY_DISPLAY_POINTS = 800;

export function formatMetricValue(value: number | string | null, unit: string): string {
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
  const suffixes: Record<string, string> = {
    percent: "%",
    milliseconds: " ms",
    celsius: " °C",
    operations_per_second: " IOPS",
    bits_per_second: " b/s",
    seconds: " s",
    hours: " h",
    rpm: " RPM",
  };
  return `${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}${suffixes[unit] ?? ""}`;
}

export function MetricDefinitionHelp({ metric }: { metric: MetricDefinition }) {
  return <details className="metric-help"><summary>About this metric</summary><dl>
    <div><dt>Source</dt><dd>{metric.source}</dd></div>
    <div><dt>Collected</dt><dd>Every {metric.minimum_interval_seconds} seconds or slower when the source is expensive</dd></div>
    <div><dt>Value</dt><dd>{metric.kind === "derived" ? "Calculated" : "Reported"} · {metric.unit.replaceAll("_", " ")}</dd></div>
    {metric.formula && <div><dt>Calculation</dt><dd>{metric.formula}</dd></div>}
    <div><dt>Availability</dt><dd>{metric.availability}</dd></div>
    {metric.implementation_status && <div><dt>Support</dt><dd>{metric.implementation_status.toLowerCase().replaceAll("_", " ")}</dd></div>}
  </dl></details>;
}

export function MetricSampleProvenance({ sample, definition }: { sample: MetricSampleDocument; definition?: MetricDefinition }) {
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

export function NumericMetricHistory({ history, label, graphType, unit }: { history: MetricHistoryDocument; label: string; graphType: "line" | "bars"; unit: string }) {
  const values = historyMeanValues(history);
  const envelope = historyEnvelopeValues(history);
  const domainValues = [...values, ...(envelope?.minimum ?? []), ...(envelope?.maximum ?? [])].filter((value): value is number => value !== null);
  const domain = domainValues.length ? { minimum: Math.min(...domainValues), maximum: Math.max(...domainValues) } : undefined;
  const gapCount = values.filter((value) => value === null).length;
  const descriptionId = `history-description-${history.entity?.id ?? "unknown"}-${history.metric_id.replaceAll(".", "-")}`;
  return <figure className={`analytics-chart graph-${graphType}`}>
    <figcaption>{label}<small>{history.raw === false ? "Bucket mean with peak-preserving minimum/maximum boundaries" : "Raw observations"}</small></figcaption>
    {domainValues.length ? <svg viewBox="0 0 100 30" preserveAspectRatio="none" role="img" aria-label={`${label} numeric history`} aria-describedby={descriptionId}>
      {envelope && <g className="rollup-envelope" aria-hidden="true"><path className="rollup-minimum" d={nullablePath(envelope.minimum, 30, 2, domain)} /><path className="rollup-maximum" d={nullablePath(envelope.maximum, 30, 2, domain)} /></g>}
      {graphType === "line" ? <path className="history-mean" d={nullablePath(values, 30, 2, domain)} /> : bars(values, domain).map((bar, index) => bar === null ? null : <rect key={history.points[index].timestamp} x={bar.x} y={bar.y} width={Math.max(.2, 90 / history.points.length)} height={bar.height} />)}
    </svg> : <p className="empty-state">No reported numeric values are available in these buckets.</p>}
    <p className="visually-hidden" id={descriptionId}>{history.points.length} bounded points. {gapCount} unavailable values are gaps, not zero. {history.raw === false ? "Minimum and maximum boundaries preserve bucket peaks around the mean." : "No rollup envelope is drawn for raw observations."}</p>
    <div><span>{new Date(history.start).toLocaleString()}</span><span>{new Date(history.end).toLocaleString()}</span></div>
    <details className="history-buckets"><summary>Accessible bucket values</summary><div className="table-scroll"><table className="data-table"><thead><tr><th scope="col">Time</th><th scope="col">{history.raw === false ? "Mean" : "Value"}</th>{history.raw === false && <><th scope="col">Min / max</th><th scope="col">First / last</th><th scope="col">Samples</th></>}<th scope="col">Quality</th></tr></thead><tbody>{history.points.map((point) => <tr key={point.timestamp}><td>{new Date(point.timestamp).toLocaleString()}</td><td>{formatMetricValue(qualityHasValue(point.quality) ? point.mean ?? point.value : null, unit)}</td>{history.raw === false && <><td>{formatMetricValue(point.minimum ?? null, unit)} / {formatMetricValue(point.maximum ?? null, unit)}</td><td>{formatMetricValue(point.first ?? null, unit)} / {formatMetricValue(point.last ?? null, unit)}</td><td>{point.sample_count ?? "Not reported"}</td></>}<td>{qualityLabel(point.quality)}</td></tr>)}</tbody></table></div></details>
  </figure>;
}

export function CategoricalMetricHistory({ history, label }: { history: MetricHistoryDocument; label: string }) {
  const buckets = stateTimeline(history);
  return <section className="state-history" aria-label={`${label} state timeline`}>
    <h4>{label} state timeline</h4>
    <p>States are shown in observed order. They are never averaged or converted to invented numeric values.</p>
    <ol>{buckets.map((bucket) => <li key={bucket.timestamp} className={`quality-${bucket.quality}`}><time>{new Date(bucket.timestamp).toLocaleString()}</time><div>{bucket.states.length ? bucket.states.map((state, index) => <span key={`${state}:${index}`}>{state.replaceAll("_", " ")}{index < bucket.states.length - 1 ? <b aria-label="then">→</b> : null}</span>) : <span>{qualityLabel(bucket.quality)}</span>}</div><small>{bucket.transitionCount} transition{bucket.transitionCount === 1 ? "" : "s"} · {qualityLabel(bucket.quality)}</small></li>)}</ol>
  </section>;
}

export function MetricHistoryDiagnostics({ history, definition, settings }: { history: MetricHistoryDocument; definition?: MetricDefinition; settings?: TelemetrySettingsDocument | null }) {
  const rolled = history.raw === false;
  const interval = history.points.find((point) => point.interval_seconds)?.interval_seconds;
  const sourceResolution = history.source_resolution ?? history.resolution;
  const retention = sourceResolution === "raw"
    ? settings ? `${settings.history.recent_retention_hours} hours raw` : "Recent raw retention"
    : sourceResolution === "hour"
      ? settings ? `${settings.history.medium_retention_days} days hourly` : "Medium hourly retention"
      : settings ? `${settings.history.long_retention_days} days daily` : "Long daily retention";
  return <details className="graph-diagnostics"><summary>Graph details</summary><dl>
    <div><dt>Entity</dt><dd>{history.entity ? `${history.entity.display_name} · ${history.entity.entity_type.replaceAll("_", " ")}` : "Not reported"}</dd></div>
    <div><dt>Stable identity</dt><dd><code>{history.entity?.stable_id ?? "Not reported"}</code></dd></div>
    <div><dt>Metric ID</dt><dd><code>{history.metric_id}</code></dd></div>
    <div><dt>Range</dt><dd>{new Date(history.start).toLocaleString()} – {new Date(history.end).toLocaleString()}</dd></div>
    <div><dt>Metric source</dt><dd>{history.metric_source ?? history.points.find((point) => point.source)?.source ?? definition?.source ?? "Not reported"}</dd></div>
    <div><dt>Requested / selected resolution</dt><dd>{history.requested_resolution ?? "auto"} / {sourceResolution}{interval ? ` · ${interval} second buckets` : ""}</dd></div>
    <div><dt>Representation</dt><dd>{rolled ? "Historical rollup; values are aggregates" : "Raw observations"}</dd></div>
    <div><dt>Points</dt><dd>{history.points_returned ?? history.points.length} returned · {history.displayed_points ?? history.points.length} displayed · maximum {history.maximum_points ?? MAX_HISTORY_DISPLAY_POINTS}</dd></div>
    {settings && <div><dt>Request limits</dt><dd>{settings.history.maximum_series} series · {settings.history.maximum_observations.toLocaleString()} observations</dd></div>}
    <div><dt>Retention boundary</dt><dd>{retention} · {definition?.retention_class === "extended" ? settings?.extended_history.entitled ? "extended history entitled" : "extended history entitlement required" : "basic recent history"}</dd></div>
    {history.aggregation_method && <div><dt>Aggregation</dt><dd>{history.aggregation_method}</dd></div>}
    <div><dt>Metric classification</dt><dd>{history.metric_kind ?? definition?.kind ?? "Not reported"}</dd></div>
    {(history.formula ?? definition?.formula) && <div><dt>Formula</dt><dd>{history.formula ?? definition?.formula}</dd></div>}
  </dl></details>;
}
