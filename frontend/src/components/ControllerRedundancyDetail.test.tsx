import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type {
  CurrentMetricsDocument,
  LogicalStorageDocument,
  MetricEntity,
  MetricHistoryDocument,
  StorageRedundancyEventDocument,
  StorageRedundancySettings,
} from "../types";
import { ControllerRedundancyDetail } from "./ControllerRedundancyDetail";

const settings: StorageRedundancySettings = {
  mode: "recommended",
  path_grouping_policy: "group_by_prio",
  path_selector: "service-time 0",
  failback: "followover",
  no_path_retry: "fail",
  polling_interval_seconds: 5,
  minimum_healthy_paths: 2,
  alert_on_reduced: true,
  alert_on_failover: true,
  alert_on_path_flapping: true,
  alert_on_total_loss: true,
};

const storage: LogicalStorageDocument = {
  id: "11111111-1111-4111-8111-111111111111",
  name: "MediaPool",
  stable_identity: "wwn:naa.600a098000abc",
  filesystem_uuid: "22222222-2222-4222-8222-222222222222",
  mountpoint: "/media",
  presentation_device: "/dev/mapper/naa.600a098000abc",
  topology_state: "fully_redundant",
  capacity_bytes: 8_000_000_000_000,
  node_name: "Node A",
  storage_scope: "external_shared",
  ownership_mode: "controlled_single_writer",
  ownership_state: "serving",
  peer_node: "Node B",
  redundancy_settings: settings,
  redundancy_summary: {
    healthy_paths: 2,
    active_paths: 1,
    failed_paths: 0,
    failovers_today: 1,
    last_failover: "2026-08-22T14:32:08Z",
    time_degraded_seconds: 0,
  },
  paths: ["a", "b"].map((name, index) => ({
    id: `${index + 3}3333333-3333-4333-8333-333333333333`,
    stable_path_identity: `fc:hba-${name}:target-${name}`,
    kernel_path: `/dev/sd${index ? "c" : "b"}`,
    protocol: "fc",
    state: "active",
    active: true,
    optimized: index === 0,
    controller: {
      id: `${index + 5}5555555-5555-4555-8555-555555555555`,
      stable_identity: `hba-${name}`,
      model: `Controller ${name.toUpperCase()}`,
      provider: "dm-multipath",
      state: { firmware: "1.2.3" },
    },
    metadata: { negotiated_speed: "12 Gb/s", capable_speed: "12 Gb/s", hctl: `2:0:0:${index}` },
  })),
};

const entities: MetricEntity[] = storage.paths.map((path, index) => ({
  id: `${index + 7}7777777-7777-4777-8777-777777777777`,
  entity_type: "storage_path",
  stable_id: `storage-path:${path.stable_path_identity}`,
  display_name: path.kernel_path,
  labels: { device: path.kernel_path.slice(5), state: path.state },
  topology: { storage_entity_id: storage.id },
  first_seen_at: "2026-08-22T13:00:00Z",
  last_seen_at: "2026-08-22T15:00:00Z",
}));
const logicalEntity: MetricEntity = {
  id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  entity_type: "logical_storage",
  stable_id: `logical-storage:${storage.stable_identity}`,
  display_name: storage.name,
  labels: { topology_state: storage.topology_state },
  topology: { storage_entity_id: storage.id },
  first_seen_at: "2026-08-22T13:00:00Z",
  last_seen_at: "2026-08-22T15:00:00Z",
};

const events: StorageRedundancyEventDocument[] = [{
  id: "99999999-9999-4999-8999-999999999999",
  event_type: "controller_failover",
  path_id: storage.paths[0].id,
  controller_id: storage.paths[0].controller?.id ?? null,
  operation_id: null,
  previous_state: "fully_redundant",
  resulting_state: "failed_over",
  details: { active_path: storage.paths[1].stable_path_identity },
  occurred_at: "2026-08-22T14:32:08Z",
}];

function currentMetrics(): CurrentMetricsDocument {
  const items = entities.flatMap((entity) => [
    ["io.read.bytes_per_second", 120_000_000, "bytes_per_second"],
    ["io.write.bytes_per_second", 80_000_000, "bytes_per_second"],
    ["io.read.iops", 240, "operations_per_second"],
    ["io.write.iops", 160, "operations_per_second"],
    ["io.read.latency", 2.4, "milliseconds"],
    ["io.write.latency", 3.1, "milliseconds"],
  ].map(([metricId, value, unit]) => ({
    metric_id: String(metricId),
    name: String(metricId),
    entity,
    timestamp: "2026-08-22T15:00:00Z",
    value: Number(value),
    unit: String(unit),
    source: "Linux block counters",
    collection_interval_seconds: 5,
    quality: "available" as const,
    raw: true,
    labels: {},
    capability: null,
    error_code: null,
  })));
  return {
    items,
    captured_at: "2026-08-22T15:00:00Z",
    restricted_capabilities: [],
  };
}

function history(entity: MetricEntity, metricId: string): MetricHistoryDocument {
  return {
    entity,
    metric_id: metricId,
    unit: metricId.includes("latency") ? "milliseconds" : metricId.includes("iops") ? "operations_per_second" : "bytes_per_second",
    resolution: "raw",
    start: "2026-08-22T13:00:00Z",
    end: "2026-08-22T15:00:00Z",
    points: [
      { timestamp: "2026-08-22T13:00:00Z", value: 10, quality: "available" },
      { timestamp: "2026-08-22T14:32:08Z", value: 90, quality: "available" },
      { timestamp: "2026-08-22T15:00:00Z", value: 25, quality: "available" },
    ],
  };
}

describe("ControllerRedundancyDetail", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("shows topology, real path details, KPIs, graphs, annotations, and events", async () => {
    vi.spyOn(api, "storageRedundancyEvents").mockResolvedValue(events);
    vi.spyOn(api, "metricEntities").mockImplementation(async (entityType) => entityType === "logical_storage" ? [logicalEntity] : entities);
    vi.spyOn(api, "currentMetrics").mockResolvedValue(currentMetrics());
    vi.spyOn(api, "metricHistory").mockImplementation(async ({ entityId, metricId }) => history([...entities, logicalEntity].find((entity) => entity.id === entityId)!, metricId));

    render(<ControllerRedundancyDetail storage={storage} onAction={vi.fn()} />);
    expect(await screen.findByText("2 / 2 paths healthy")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /MediaPool controller and path topology/i })).toBeInTheDocument();
    expect(screen.getByText("Failovers today")).toBeInTheDocument();
    expect(screen.getByText("Node A")).toBeInTheDocument();
    expect(screen.getByText("Storage role")).toBeInTheDocument();
    expect(screen.getByText("serving")).toBeInTheDocument();
    expect(screen.getByText("Node B")).toBeInTheDocument();
    expect(screen.getByText(/does not infer peer IO or ownership/i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Controllers & paths" }));
    expect(screen.getByText("Controller A")).toBeInTheDocument();
    expect(screen.getAllByText("12 Gb/s").length).toBeGreaterThan(0);

    await userEvent.click(screen.getByRole("button", { name: "Performance" }));
    expect(await screen.findByRole("img", { name: "Read throughput by controller path" })).toBeInTheDocument();
    await waitFor(() => expect(api.metricHistory).toHaveBeenCalledTimes(14));
    expect(screen.getAllByText(/Vertical markers show failover/).length).toBeGreaterThan(0);

    await userEvent.click(screen.getByRole("button", { name: "Events" }));
    expect(screen.getByText("controller failover")).toBeInTheDocument();
    expect(screen.getByText(/fully_redundant → failed_over/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Performance" }));
    expect(screen.getByRole("img", { name: "Read throughput by controller path" })).toBeInTheDocument();
    expect(api.metricHistory).toHaveBeenCalledTimes(14);
  });

  it("reviews supported settings through the real configure workflow", async () => {
    vi.spyOn(api, "storageRedundancyEvents").mockResolvedValue([]);
    vi.spyOn(api, "metricEntities").mockResolvedValue([]);
    vi.spyOn(api, "currentMetrics").mockResolvedValue({
      items: [],
      captured_at: "2026-08-22T15:00:00Z",
      restricted_capabilities: [],
    });
    const action = vi.fn();
    render(<ControllerRedundancyDetail storage={storage} onAction={action} />);
    await userEvent.click(screen.getByRole("button", { name: "Advanced settings" }));
    await userEvent.click(screen.getByLabelText("Customize"));
    await userEvent.selectOptions(screen.getByRole("combobox", { name: /Path grouping/i }), "multibus");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: /Failback/i }), "manual");
    await userEvent.click(screen.getByRole("button", { name: "Review and apply settings" }));
    expect(action).toHaveBeenCalledWith("configure", expect.objectContaining({
      mode: "custom",
      path_grouping_policy: "multibus",
      failback: "manual",
    }));
  });
});
