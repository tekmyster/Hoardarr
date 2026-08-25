import type { MetricClassification, MetricDefinition, MetricHistoryDocument, MetricQuality, MetricSampleDocument } from "./types";

const VALUE_QUALITIES = new Set<MetricQuality>(["available", "stale", "derived", "estimated"]);

export function qualityHasValue(quality: MetricQuality): boolean {
  return VALUE_QUALITIES.has(quality);
}

export function qualityLabel(quality: MetricQuality): string {
  return ({
    available: "Available",
    not_reported: "Not reported",
    unsupported: "Unsupported",
    temporarily_unavailable: "Temporarily unavailable",
    stale: "Stale",
    derived: "Derived",
    estimated: "Estimated",
  } satisfies Record<MetricQuality, string>)[quality];
}

export function numericValue(value: number | string | null, quality: MetricQuality): number | null {
  return qualityHasValue(quality) && typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function sampleClassification(sample: MetricSampleDocument, definition?: MetricDefinition): MetricClassification {
  if (sample.classification) return sample.classification;
  if (sample.quality === "estimated") return "estimated";
  if (sample.quality === "derived" || definition?.kind === "derived" || sample.raw === false) return "derived";
  return "raw";
}

/** Build an SVG line with explicit gaps for null/unavailable observations. */
export function nullablePath(
  values: readonly (number | null)[],
  height = 30,
  padding = 2,
  domain?: { minimum: number; maximum: number },
): string {
  const available = values.filter((value): value is number => value !== null && Number.isFinite(value));
  if (!available.length) return "";
  const minimum = domain?.minimum ?? Math.min(...available);
  const maximum = domain?.maximum ?? Math.max(...available);
  const spread = Math.max(Number.EPSILON, maximum - minimum);
  let connected = false;
  return values.map((value, index) => {
    if (value === null || !Number.isFinite(value)) {
      connected = false;
      return "";
    }
    const x = values.length === 1 ? 0 : index * 100 / (values.length - 1);
    const y = height - padding - (value - minimum) / spread * (height - padding * 2);
    const command = connected ? "L" : "M";
    connected = true;
    return `${command}${x.toFixed(2)},${y.toFixed(2)}`;
  }).filter(Boolean).join(" ");
}

export function historyMeanValues(history: MetricHistoryDocument): Array<number | null> {
  return history.points.map((point) => numericValue(
    point.mean ?? point.value,
    point.quality,
  ));
}

export function historyEnvelopeValues(history: MetricHistoryDocument): {
  minimum: Array<number | null>;
  maximum: Array<number | null>;
} | null {
  if (history.raw !== false) return null;
  const minimum = history.points.map((point) => numericValue(point.minimum ?? null, point.quality));
  const maximum = history.points.map((point) => numericValue(point.maximum ?? null, point.quality));
  if (![...minimum, ...maximum].some((value) => value !== null)) return null;
  return { minimum, maximum };
}

export interface StateTimelineBucket {
  timestamp: string;
  states: string[];
  transitionCount: number;
  quality: MetricQuality;
}

export function stateTimeline(history: MetricHistoryDocument): StateTimelineBucket[] {
  return history.points.map((point) => {
    const states = qualityHasValue(point.quality)
      ? point.states?.filter((value): value is string => typeof value === "string" && value.length > 0)
        ?? (typeof point.value === "string" ? [point.value] : [])
      : [];
    return {
      timestamp: point.timestamp,
      states,
      transitionCount: point.transition_count ?? Math.max(0, states.length - 1),
      quality: point.quality,
    };
  });
}

export function historyHasCategoricalValues(history: MetricHistoryDocument): boolean {
  return history.points.some((point) => typeof point.value === "string" || (point.states?.length ?? 0) > 0);
}
