import { describe, expect, it } from "vitest";
import type { MetricHistoryDocument } from "./types";
import {
  historyEnvelopeValues,
  historyMeanValues,
  nullablePath,
  qualityHasValue,
  qualityLabel,
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
