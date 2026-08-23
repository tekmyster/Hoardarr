import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { Drive, TopologyPlanDocument, TopologyPlanTemplate } from "../types";
import { TopologyPlanningPanel } from "./TopologyPlanningPanel";

const template: TopologyPlanTemplate = {
  id: "generic-8-bay",
  name: "Generic 8-bay server",
  description: "One server chassis and eight bays.",
  controller_count: 1,
  enclosures: [{ id: "chassis-bays", label: "Server bays", bay_count: 8 }],
};

const plan: TopologyPlanDocument = {
  id: "plan-1",
  name: "Future media storage",
  template_id: "generic-8-bay",
  revision: 0,
  plan: {
    schema_version: 1,
    chassis: { id: "host", label: "Hoardarr host" },
    controllers: [{ id: "controller-1", label: "Controller A", state: "planned" }],
    enclosures: [{ id: "chassis-bays", label: "Server bays", bay_count: 8, controller_ids: ["controller-1"] }],
    changes: [],
    notes: "",
  },
  created_at: "2026-08-23T20:00:00Z",
  updated_at: "2026-08-23T20:00:00Z",
};

const drive = {
  id: "wwn:existing-drive",
  model: "Existing media drive",
  serial: "SAFE-SERIAL",
  stableIdentity: true,
} as Drive;

afterEach(() => vi.restoreAllMocks());

describe("TopologyPlanningPanel", () => {
  it("creates a declared layout and persists planned expansion without changing live inventory", async () => {
    vi.spyOn(api, "topologyPlanTemplates").mockResolvedValue([template]);
    vi.spyOn(api, "topologyPlans").mockResolvedValue([]);
    vi.spyOn(api, "createTopologyPlan").mockResolvedValue(plan);
    const update = vi.spyOn(api, "updateTopologyPlan").mockImplementation(async (document) => ({ ...document, revision: document.revision + 1 }));
    render(<TopologyPlanningPanel drives={[drive]} />);

    expect(await screen.findByText("No future layout has been planned")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Starting layout"), "generic-8-bay");
    await userEvent.click(screen.getByRole("button", { name: "Create planning layout" }));
    expect(await screen.findByLabelText("Future media storage planned topology")).toBeInTheDocument();
    expect(screen.getAllByText("Open")).toHaveLength(8);
    expect(screen.getByText("Planning mode is separate from live discovery")).toBeInTheDocument();

    await userEvent.clear(screen.getByLabelText("Bay"));
    await userEvent.type(screen.getByLabelText("Bay"), "3");
    await userEvent.type(screen.getByLabelText("Capacity (TB, optional)"), "18");
    await userEvent.click(screen.getByRole("button", { name: "Add to plan" }));
    await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
    const saved = update.mock.calls[0][0];
    expect(saved.plan.changes[0]).toMatchObject({
      kind: "disk_addition",
      enclosure_id: "chassis-bays",
      slot: 3,
      capacity_bytes: 18_000_000_000_000,
    });
    expect(await screen.findByText("18 TB planned")).toBeInTheDocument();
  });

  it("shows load failures as product state rather than an empty fake plan", async () => {
    vi.spyOn(api, "topologyPlanTemplates").mockRejectedValue(new Error("planning API unavailable"));
    vi.spyOn(api, "topologyPlans").mockResolvedValue([]);
    render(<TopologyPlanningPanel drives={[]} />);
    expect(await screen.findByText("planning API unavailable")).toBeInTheDocument();
  });
});
