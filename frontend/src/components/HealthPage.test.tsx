import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { HardwareSnapshot, StorageInventory, StorageTelemetryDocument } from "../types";
import { HealthPage } from "./HealthPage";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("HealthPage", () => {
  it("shows live reported values and never treats missing health as healthy", async () => {
    const snapshot: HardwareSnapshot = {
      id: "snapshot-1",
      captured_at: "2026-08-21T20:00:00Z",
      sha256: "abc",
      hardware: { disks: [{
        id: "wwn:drive-1",
        kernel_path: "/dev/sdb",
        vendor: "CISCO",
        model: "SSD-240G",
        serial: "SERIAL-1",
        capacity_bytes: 240_000_000_000,
        stable_identity: true,
        system_disk: false,
        connection: { transport: "usb" },
        sector_sizes: { logical: 512, physical: 4096 },
        signature_scan: { status: "complete" },
        health: { status: "warning", power_on_hours: { status: "unavailable" } },
      }] },
    };
    const inventory: StorageInventory = {
      captured_from: "live_host",
      topology: { status: "available", nodes: [], links: [], enclosures: [], direct_attached_drive_ids: [] },
      active_operations: [],
      pools: { status: "not_configured", items: [] },
      shares: { status: "not_configured", items: [] },
      controllers: { status: "Not reported", items: [], unavailable: [] },
    };
    const telemetry: StorageTelemetryDocument = {
      captured_at: "2026-08-21T20:00:00Z",
      source: "linux_block_counters",
      summary: { read_bytes_per_second: 0, write_bytes_per_second: 0, read_iops: 0, write_iops: 0, read_wait_ms: 0, write_wait_ms: 0, utilization_percent: 0, writes_today_bytes: 0, sample_seconds: 1 },
      drives: [{ id: "wwn:drive-1", device: "/dev/sdb", device_name: "sdb", model: "SSD-240G", serial: "SERIAL-1", rotational: false, system_disk: false, pool_ids: [], metrics: { read_bytes_per_second: 0, write_bytes_per_second: 0, read_iops: 0, write_iops: 0, read_wait_ms: 0, write_wait_ms: 0, utilization_percent: 0 }, writes_today_bytes: 0, os_write_bytes_since_boot: 0, endurance: { lifetime_writes_bytes: null, remaining_percent: null, source: null } }],
      pools: [],
    };
    vi.spyOn(api, "latestHardwareSnapshot").mockResolvedValue(snapshot);
    vi.spyOn(api, "storageInventory").mockResolvedValue(inventory);
    vi.spyOn(api, "storageTelemetry").mockResolvedValue(telemetry);
    vi.spyOn(api, "connectivityServices").mockResolvedValue([]);

    render(<HealthPage />);
    expect(await screen.findByText(/reports warning/i)).toBeInTheDocument();
    expect(screen.getByText("Power-on hours", { selector: "th" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByText("Not reported").length).toBeGreaterThan(1));
    expect(screen.queryByText("Healthy")).not.toBeInTheDocument();
    expect(screen.getByText("No pools configured")).toBeInTheDocument();
  });

  it("keeps partial health visible when one provider fails", async () => {
    vi.spyOn(api, "latestHardwareSnapshot").mockResolvedValue(null);
    vi.spyOn(api, "storageInventory").mockRejectedValue(new Error("Pool status unavailable"));
    vi.spyOn(api, "storageTelemetry").mockRejectedValue(new Error("Telemetry unavailable"));
    vi.spyOn(api, "connectivityServices").mockResolvedValue([]);
    render(<HealthPage />);
    expect(await screen.findByText("Pool status unavailable")).toBeInTheDocument();
    expect(screen.getByText("No hardware scan")).toBeInTheDocument();
  });
});
