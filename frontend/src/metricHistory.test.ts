import { describe, expect, it } from "vitest";
import type { MetricDefinition, MetricEntity, MetricHistoryDocument } from "./types";
import {
  applicableMetricDefinitions,
  applicableMetricSelection,
  historyEnvelopeValues,
  historyMeanValues,
  nullablePath,
  qualityHasValue,
  qualityLabel,
  resolveMetricHistoryEntity,
  stateTimeline,
} from "./metricHistory";

function history(input: Partial<MetricHistoryDocument>): MetricHistoryDocument {
  return {
    entity: null,
    metric_id: "drive.temperature",
    unit: "celsius",
    resolution: "raw",
    start: "2026-08-25T00:00:00Z",
    end: "2026-08-25T01:00:00Z",
    points: [],
    ...input,
  };
}

describe("metric history presentation contract", () => {
  it("offers only entitled catalog metrics applicable to the exact entity type", () => {
    const definitions = [
      { id: "cpu.utilization", entity_types: ["host"], entitled: true },
      { id: "drive.temperature", entity_types: ["drive"], entitled: true },
      { id: "drive.endurance", entity_types: ["drive"], entitled: false },
    ] as MetricDefinition[];
    expect(applicableMetricDefinitions(definitions, "drive").map((item) => item.id)).toEqual(["drive.temperature"]);
    expect(applicableMetricSelection(definitions, "drive", "cpu.utilization")).toBe("drive.temperature");
    expect(applicableMetricSelection(definitions, "drive", "drive.temperature")).toBe("drive.temperature");
  });

  it("resolves context by stable identity and refuses ambiguous display-name substitution", () => {
    const entities = [
      { id: "one", entity_type: "drive", stable_id: "wwn:one", display_name: "SSD" },
      { id: "two", entity_type: "drive", stable_id: "wwn:two", display_name: "SSD" },
      { id: "host", entity_type: "host", stable_id: "host:a", display_name: "a" },
    ] as MetricEntity[];
    expect(resolveMetricHistoryEntity(entities, { entityType: "drive", stableId: "wwn:two", displayName: "SSD", sourceSurface: "storage" })?.id).toBe("two");
    expect(resolveMetricHistoryEntity(entities, { entityType: "drive", displayName: "SSD", sourceSurface: "storage" })).toBeNull();
    expect(resolveMetricHistoryEntity(entities, { entityType: "host", sourceSurface: "overview" })?.id).toBe("host");
  });

  it("treats an explicit stable identity as authoritative even when the name matches", () => {
    const entities = [
      { id: "reported", entity_type: "drive", stable_id: "wwn:reported", display_name: "Media SSD" },
      { id: "other", entity_type: "drive", stable_id: "wwn:other", display_name: "Other SSD" },
    ] as MetricEntity[];
    expect(resolveMetricHistoryEntity(entities, {
      entityType: "drive",
      stableId: "wwn:missing",
      displayName: "Media SSD",
      sourceSurface: "storage",
    })).toBeNull();
  });

  it("refuses the sole same-type entity when an explicit stable identity is absent", () => {
    const entities = [
      { id: "only-drive", entity_type: "drive", stable_id: "wwn:reported", display_name: "Only SSD" },
    ] as MetricEntity[];
    expect(resolveMetricHistoryEntity(entities, {
      entityType: "drive",
      stableId: "wwn:missing",
      sourceSurface: "storage",
    })).toBeNull();
  });

  it("preserves null/unavailable samples as disconnected graph segments", () => {
    const document = history({
      points: [
        { timestamp: "a", value: 0, quality: "available" },
        { timestamp: "b", value: null, quality: "not_reported" },
        { timestamp: "c", value: 10, quality: "available" },
      ],
    });
    expect(historyMeanValues(document)).toEqual([0, null, 10]);
    expect(nullablePath(historyMeanValues(document))).toMatch(/^M[^M]+ M/);
    expect(nullablePath([null, null])).toBe("");
  });

  it("uses rollup mean and exposes peak-preserving minimum/maximum inputs only for rollups", () => {
    const rolled = history({
      raw: false,
      resolution: "hour",
      points: [{ timestamp: "a", value: 4, mean: 4, minimum: 1, maximum: 20, quality: "derived", sample_count: 5 }],
    });
    expect(historyMeanValues(rolled)).toEqual([4]);
    expect(historyEnvelopeValues(rolled)).toEqual({ minimum: [1], maximum: [20] });
    expect(historyEnvelopeValues({ ...rolled, raw: true })).toBeNull();
  });

  it("retains ordered categorical transitions without numeric encoding", () => {
    const document = history({
      raw: false,
      points: [
        { timestamp: "a", value: "healthy", states: ["healthy", "degraded", "healthy"], transition_count: 2, quality: "derived" },
        { timestamp: "b", value: null, quality: "temporarily_unavailable" },
      ],
    });
    expect(stateTimeline(document)).toEqual([
      { timestamp: "a", states: ["healthy", "degraded", "healthy"], transitionCount: 2, quality: "derived" },
      { timestamp: "b", states: [], transitionCount: 0, quality: "temporarily_unavailable" },
    ]);
  });

  it("renders every normalized quality state distinctly", () => {
    const states = ["available", "not_reported", "unsupported", "temporarily_unavailable", "stale", "estimated", "derived"] as const;
    expect(states.map(qualityLabel)).toEqual([
      "Available", "Not reported", "Unsupported", "Temporarily unavailable", "Stale", "Estimated", "Derived",
    ]);
    expect(states.map(qualityHasValue)).toEqual([true, false, false, false, true, true, true]);
  });
});
