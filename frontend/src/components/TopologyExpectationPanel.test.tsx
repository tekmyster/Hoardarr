import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { TopologyExpectationStatus } from "../types";
import { TopologyExpectationPanel } from "./TopologyExpectationPanel";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const expectation = {
  id: "expectation-1",
  name: "Media shelf",
  source_snapshot_id: "snapshot-1",
  expected: {
    schema_version: 1,
    source_snapshot_id: "snapshot-1",
    source_snapshot_sha256: "a".repeat(64),
    nodes: [{ id: "controller:0000:01:00.0", kind: "controller" }],
  },
  active: true,
  created_at: "2026-08-23T18:00:00Z",
  updated_at: "2026-08-23T18:00:00Z",
};

describe("TopologyExpectationPanel", () => {
  it("saves the current real scan only after the user chooses the baseline", async () => {
    const user = userEvent.setup();
    const read = vi.spyOn(api, "topologyExpectation")
      .mockResolvedValueOnce({ expectation: null, active_drifts: [], recent_events: [] })
      .mockResolvedValueOnce({ expectation, active_drifts: [], recent_events: [] });
    const save = vi.spyOn(api, "saveTopologyExpectation").mockResolvedValue(expectation);

    render(<TopologyExpectationPanel snapshotId="snapshot-1" />);
    expect(await screen.findByText("No expected topology has been saved")).toBeInTheDocument();
    await user.clear(screen.getByLabelText("Baseline name"));
    await user.type(screen.getByLabelText("Baseline name"), "Media shelf");
    await user.click(screen.getByRole("button", { name: "Use current scan as expected" }));

    await waitFor(() => expect(save).toHaveBeenCalledWith("snapshot-1", "Media shelf"));
    expect(await screen.findByText("Latest scan matches the baseline")).toBeInTheDocument();
    expect(read).toHaveBeenCalledTimes(2);
  });

  it("shows active and resolved drift episodes without inventing missing values", async () => {
    const active = {
      id: "drift-1",
      expectation_id: expectation.id,
      snapshot_id: "snapshot-2",
      kind: "link_rate_degraded",
      severity: "warning" as const,
      entity_type: "path",
      entity_id: "path:end_device-6:0:3",
      message: "Path A negotiated 6 Gb/s; expected 12 Gb/s.",
      expected: { negotiated_speed_gbps: 12 },
      observed: { negotiated_speed_gbps: 6 },
      state: "active" as const,
      first_seen_at: "2026-08-23T18:10:00Z",
      last_seen_at: "2026-08-23T18:11:00Z",
      resolved_at: null,
    };
    const resolved = { ...active, id: "drift-0", state: "resolved" as const, resolved_at: "2026-08-23T18:05:00Z" };
    const status: TopologyExpectationStatus = {
      expectation,
      active_drifts: [active],
      recent_events: [active, resolved],
    };
    vi.spyOn(api, "topologyExpectation").mockResolvedValue(status);

    render(<TopologyExpectationPanel snapshotId="snapshot-2" />);
    expect(await screen.findAllByText("Path A negotiated 6 Gb/s; expected 12 Gb/s.")).toHaveLength(2);
    expect(screen.getByText("1 difference needs review")).toBeInTheDocument();
    expect(screen.getByText("Resolved topology changes")).toBeInTheDocument();
    expect(screen.queryByText(/0 Gb\/s/)).not.toBeInTheDocument();
  });
});
