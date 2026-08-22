import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { MetricCatalogDocument, MetricEntity, MetricSampleDocument } from "../types";
import { AnalyticsPage } from "./AnalyticsPage";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const entity: MetricEntity = {
  id: "entity-1",
  entity_type: "drive",
  stable_id: "wwn:one",
  display_name: "Enterprise SSD",
  labels: {},
  topology: { controller: "controller-1" },
  first_seen_at: "2026-08-22T10:00:00Z",
  last_seen_at: "2026-08-22T11:00:00Z",
};

const catalog: MetricCatalogDocument = {
  items: [
    {
      id: "io.read.bytes_per_second",
      name: "Read throughput",
      entity_types: ["drive"],
      unit: "bytes_per_second",
      kind: "raw",
      source: "Linux block counters",
      minimum_interval_seconds: 5,
      capability: null,
      retention_class: "recent",
      aggregation: "mean",
      availability: "When Linux reports block counters",
      formula: null,
      test_evidence: "test",
      entitled: true,
    },
    {
      id: "analytics.latency.p95",
      name: "Latency P95",
      entity_types: ["drive"],
      unit: "milliseconds",
      kind: "derived",
      source: "stored observations",
      minimum_interval_seconds: 60,
      capability: "metrics.analytics.performance",
      retention_class: "extended",
      aggregation: "mean",
      availability: "With sufficient history",
      formula: "nearest-rank 95th percentile",
      test_evidence: "test",
      entitled: false,
    },
  ],
  quality_states: ["available", "not_reported", "unsupported", "temporarily_unavailable", "stale", "estimated", "derived"],
  entitlements: {
    state: "unlicensed",
    capabilities: [],
    expires_at: null,
    license_id: null,
    detail: "Basic telemetry is active.",
    validated_at: "2026-08-22T11:00:00Z",
    cached: false,
    basic_metrics_available: true,
  },
};

const sample: MetricSampleDocument = {
  metric_id: "io.read.bytes_per_second",
  name: "Read throughput",
  entity,
  timestamp: "2026-08-22T11:00:00Z",
  value: 1048576,
  unit: "bytes_per_second",
  source: "Linux block counters",
  collection_interval_seconds: 5,
  quality: "available",
  raw: true,
  labels: {},
  capability: null,
  error_code: null,
};

const historySettings = {
  collection: { fast_interval_seconds: 5, device_interval_seconds: 300, hardware_interval_seconds: 900 },
  history: { recent_resolution_seconds: 5, recent_retention_hours: 48, medium_resolution_seconds: 3600, medium_retention_days: 90, long_resolution_seconds: 86400, long_retention_days: 730, maximum_graph_points: 1200, maximum_series: 16, maximum_observations: 20000 },
  storage: { database_bytes: 4096, oldest_raw_history: null, oldest_retained_history: null, entity_count: 1, estimated_bytes_per_day: 1024, estimate_method: "estimate", last_cleanup: null, next_cleanup: null, cleanup_batch_size: 10000 },
  extended_history: { entitled: false, capability: "metrics.history.extended" },
};

describe("AnalyticsPage", () => {
  it("renders only real API readings and explains licensed state", async () => {
    vi.spyOn(api, "metricCatalog").mockResolvedValue(catalog);
    vi.spyOn(api, "metricEntities").mockResolvedValue([entity]);
    vi.spyOn(api, "currentMetrics").mockResolvedValue({ captured_at: "2026-08-22T11:00:00Z", items: [sample], restricted_capabilities: ["metrics.analytics.performance"] });
    vi.spyOn(api, "metricAlerts").mockResolvedValue([]);
    vi.spyOn(api, "telemetrySettings").mockResolvedValue(historySettings);
    vi.spyOn(api, "metricHistory").mockResolvedValue({ entity, metric_id: sample.metric_id, unit: sample.unit, resolution: "raw", start: "2026-08-22T10:00:00Z", end: "2026-08-22T11:00:00Z", points: [{ timestamp: "2026-08-22T11:00:00Z", value: 1048576, quality: "available" }] });

    render(<AnalyticsPage />);
    expect(await screen.findByText("1 MiB/s")).toBeInTheDocument();
    expect(screen.getByText("Basic analytics active")).toBeInTheDocument();
    expect(screen.queryByText("Latency P95", { selector: "option" })).not.toBeInTheDocument();
    expect(screen.getByText("No active telemetry alerts.")).toBeInTheDocument();
    expect(screen.getByText("5-second detail for 48 hours")).toBeInTheDocument();
    await userEvent.click(screen.getAllByText("About this metric")[0]);
    expect(screen.getAllByText("Linux block counters", { selector: "dd" }).length).toBeGreaterThan(0);
    await waitFor(() => expect(api.metricHistory).toHaveBeenCalled());
  });

  it("shows missing readings honestly", async () => {
    vi.spyOn(api, "metricCatalog").mockResolvedValue(catalog);
    vi.spyOn(api, "metricEntities").mockResolvedValue([entity]);
    vi.spyOn(api, "currentMetrics").mockResolvedValue({ captured_at: "2026-08-22T11:00:00Z", items: [{ ...sample, value: null, quality: "not_reported" }], restricted_capabilities: [] });
    vi.spyOn(api, "metricAlerts").mockResolvedValue([]);
    vi.spyOn(api, "metricHistory").mockResolvedValue({ entity, metric_id: sample.metric_id, unit: sample.unit, resolution: "raw", start: "2026-08-22T10:00:00Z", end: "2026-08-22T11:00:00Z", points: [] });
    render(<AnalyticsPage />);
    expect(await screen.findByText("Not reported")).toBeInTheDocument();
    expect(screen.getByText("No stored readings are available for this selection.")).toBeInTheDocument();
  });

  it("passes automated accessibility checks with live and unavailable data", async () => {
    vi.spyOn(api, "metricCatalog").mockResolvedValue(catalog);
    vi.spyOn(api, "metricEntities").mockResolvedValue([entity]);
    vi.spyOn(api, "currentMetrics").mockResolvedValue({ captured_at: "2026-08-22T11:00:00Z", items: [sample], restricted_capabilities: [] });
    vi.spyOn(api, "metricAlerts").mockResolvedValue([]);
    vi.spyOn(api, "metricHistory").mockResolvedValue({ entity, metric_id: sample.metric_id, unit: sample.unit, resolution: "raw", start: "2026-08-22T10:00:00Z", end: "2026-08-22T11:00:00Z", points: [{ timestamp: "2026-08-22T11:00:00Z", value: 1048576, quality: "available" }] });
    const { container } = render(<AnalyticsPage />);
    await screen.findByText("1 MiB/s");
    const result = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(result.violations).toEqual([]);
  });

  it("loads entitled analysis from backend endpoints and changes graph type", async () => {
    const licensed = {
      ...catalog,
      entitlements: {
        ...catalog.entitlements,
        state: "valid",
        capabilities: [
          "metrics.analytics.performance",
          "metrics.analytics.endurance",
          "metrics.analytics.anomaly",
        ],
      },
    };
    vi.spyOn(api, "metricCatalog").mockResolvedValue(licensed);
    vi.spyOn(api, "metricEntities").mockResolvedValue([entity]);
    vi.spyOn(api, "currentMetrics").mockResolvedValue({ captured_at: "2026-08-22T11:00:00Z", items: [sample], restricted_capabilities: [] });
    vi.spyOn(api, "metricAlerts").mockResolvedValue([]);
    vi.spyOn(api, "metricHistory").mockResolvedValue({ entity, metric_id: sample.metric_id, unit: sample.unit, resolution: "raw", start: "2026-08-22T10:00:00Z", end: "2026-08-22T11:00:00Z", points: [{ timestamp: "2026-08-22T11:00:00Z", value: 1048576, quality: "available" }] });
    vi.spyOn(api, "topMetrics").mockResolvedValue([sample]);
    vi.spyOn(api, "enduranceForecast").mockResolvedValue({ forecast: { status: "insufficient_history", methodology: "Stored percentage-used observations." } });
    vi.spyOn(api, "telemetryAnomalies").mockResolvedValue([{ metric_id: "io.read.bytes_per_second", entity, explanation: "Performance outside recent baseline" }]);

    const { container } = render(<AnalyticsPage />);
    expect(await screen.findByText("Advanced analysis")).toBeInTheDocument();
    expect(screen.getByText("Stored percentage-used observations.")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Graph"), "bars");
    expect(container.querySelector(".graph-bars rect")).not.toBeNull();
    expect(api.topMetrics).toHaveBeenCalledTimes(1);
  });

  it("cancels history work and polling when the page unmounts", async () => {
    vi.spyOn(api, "metricCatalog").mockResolvedValue(catalog);
    vi.spyOn(api, "metricEntities").mockResolvedValue([entity]);
    vi.spyOn(api, "currentMetrics").mockResolvedValue({ captured_at: "2026-08-22T11:00:00Z", items: [sample], restricted_capabilities: [] });
    vi.spyOn(api, "metricAlerts").mockResolvedValue([]);
    const settingsSpy = vi.spyOn(api, "telemetrySettings").mockResolvedValue(historySettings);
    const historySpy = vi.spyOn(api, "metricHistory").mockReturnValue(new Promise(() => undefined));
    const clearIntervalSpy = vi.spyOn(window, "clearInterval");
    const { unmount } = render(<AnalyticsPage />);
    await waitFor(() => expect(historySpy).toHaveBeenCalled());
    const signal = historySpy.mock.calls[0][0].signal;
    const settingsSignal = settingsSpy.mock.calls[0][0];
    expect(signal?.aborted).toBe(false);
    expect(settingsSignal?.aborted).toBe(false);
    unmount();
    expect(signal?.aborted).toBe(true);
    expect(settingsSignal?.aborted).toBe(true);
    expect(clearIntervalSpy).toHaveBeenCalled();
  });

  it("aborts an unfinished aggregate refresh when the page unmounts", async () => {
    const catalogSpy = vi.spyOn(api, "metricCatalog").mockReturnValue(new Promise(() => undefined));
    vi.spyOn(api, "metricEntities").mockResolvedValue([]);
    vi.spyOn(api, "currentMetrics").mockResolvedValue({ captured_at: "2026-08-22T11:00:00Z", items: [], restricted_capabilities: [] });
    vi.spyOn(api, "metricAlerts").mockResolvedValue([]);
    vi.spyOn(api, "telemetrySettings").mockResolvedValue(historySettings);
    const { unmount } = render(<AnalyticsPage />);
    await waitFor(() => expect(catalogSpy).toHaveBeenCalledTimes(1));
    const signal = catalogSpy.mock.calls[0][0];
    expect(signal?.aborted).toBe(false);
    unmount();
    expect(signal?.aborted).toBe(true);
  });
});
