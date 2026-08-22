import { describe, expect, it } from "vitest";
import { DEFAULT_DASHBOARD_PANELS, loadDashboardPanels, moveDashboardPanel, shiftDashboardPanel } from "./dashboard";

describe("Overview dashboard layout", () => {
  it("uses defaults when saved state is absent or invalid", () => {
    expect(loadDashboardPanels(null)).toEqual(DEFAULT_DASHBOARD_PANELS);
    expect(loadDashboardPanels("not-json")).toEqual(DEFAULT_DASHBOARD_PANELS);
    expect(loadDashboardPanels('{"version":5,"panels":[]}')).toEqual(DEFAULT_DASHBOARD_PANELS);
  });

  it("accepts an intentionally empty dashboard and removes unknown or duplicate panels", () => {
    expect(loadDashboardPanels('{"version":1,"panels":[]}')).toEqual([]);
    expect(loadDashboardPanels('{"version":1,"panels":["network","unknown","network","system"]}')).toEqual(["network", "neighbors", "system", "storage-performance"]);
    expect(loadDashboardPanels('{"version":2,"panels":["network","system"]}')).toEqual(["network", "system", "storage-performance"]);
    expect(loadDashboardPanels('{"version":3,"panels":["network","system"]}')).toEqual(["network", "system"]);
    expect(loadDashboardPanels('{"version":4,"panels":["storage","drive-health"]}')).toEqual(["storage", "drive-health"]);
  });

  it("supports drag and keyboard-style reordering without mutating input", () => {
    const original = ["system", "network", "alerts"] as const;
    expect(moveDashboardPanel([...original], "alerts", "system")).toEqual(["alerts", "system", "network"]);
    expect(shiftDashboardPanel([...original], "network", -1)).toEqual(["network", "system", "alerts"]);
    expect(original).toEqual(["system", "network", "alerts"]);
  });
});
