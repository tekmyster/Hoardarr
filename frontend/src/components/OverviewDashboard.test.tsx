import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { OverviewDocument, ResourceUsageDocument } from "../types";
import { networkRates, OverviewDashboard, storageActivityState } from "./OverviewDashboard";

const overview: OverviewDocument = {
  captured_at: "2026-08-20T15:00:00Z",
  source: "live",
  system: {
    hostname: "hoardarr-build",
    application: "Hoardarr",
    version: "0.1.0",
    database_ready: true,
    booted_at: "2026-08-20T12:00:00Z",
    uptime_seconds: 10_800,
    cpu: { used_percent: 5, physical_cores: 4, logical_processors: 8 },
    memory: { total_bytes: 16_000, available_bytes: 12_000, used_bytes: 4_000, used_percent: 25 },
    boot_volume: { mountpoint: "/", total_bytes: 100_000, used_bytes: 20_000, free_bytes: 80_000, used_percent: 20 },
    temperatures: [],
  },
  storage: {
    snapshot: null,
    drive_count: null,
    raw_capacity_bytes: null,
    health: null,
    pools: { status: "not_configured", items: [] },
    shares: { status: "not_configured", items: [] },
  },
  network: {
    interfaces: [{ name: "eth0", up: true, speed_mbps: 40_000, mtu: 9000, bytes_received: 10_000, bytes_sent: 5_000, errors_received: 0, errors_sent: 0, drops_received: 0, drops_sent: 0 }],
    discovery: {
      status: "available",
      source: "lldpcli",
      captured_at: "2026-08-20T15:00:00Z",
      detail: null,
      neighbors: [{
        local_interface: "eth0",
        protocol: "LLDP",
        protocol_variant: "LLDP",
        device_name: "core-9500",
        chassis_id: "00:11:22:33:44:55",
        port_id: "FortyGigabitEthernet1/0/1",
        port_description: "Hoardarr storage host",
        management_addresses: ["10.81.200.1"],
        system_description: null,
        age: "0 day, 00:00:18",
        ttl_seconds: 120,
      }],
    },
  },
  activity: { operations: [] },
  applications: { connections: [] },
  alerts: [],
};

function resourceReading(cpu: number): ResourceUsageDocument {
  return {
    captured_at: `2026-08-20T15:00:0${cpu / 10}Z`,
    source: "live",
    cpu: { used_percent: cpu, physical_cores: 4, logical_processors: 8 },
    memory: { total_bytes: 16_000, available_bytes: 8_000, used_bytes: 8_000, used_percent: 50 },
    network: { interfaces: [{ name: "eth0", up: true, bytes_received: cpu * 1_000, bytes_sent: cpu * 500 }] },
    storage: {
      system_volume: { mountpoint: "/", total_bytes: 100_000, used_bytes: 30_000, free_bytes: 70_000, used_percent: 30 },
      performance: {
        captured_at: "2026-08-20T15:00:00Z", source: "linux_block_counters",
        summary: { read_bytes_per_second: 1024, write_bytes_per_second: 2048, read_iops: 2, write_iops: 3, read_wait_ms: 1, write_wait_ms: 2, utilization_percent: 4, writes_today_bytes: 4096, sample_seconds: 2 },
        drives: [], pools: [],
      },
    },
  };
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe("OverviewDashboard", () => {
  it("derives reset-safe live network rates from monotonic counters", () => {
    const first = networkRates(null, resourceReading(10));
    const secondReading = resourceReading(20);
    secondReading.captured_at = "2026-08-20T15:00:04Z";
    const second = networkRates(first.sample, secondReading);
    expect(second.received).toBeCloseTo(10_000 / 3);
    expect(second.sent).toBeCloseTo(5_000 / 3);

    const reset = resourceReading(1);
    reset.captured_at = "2026-08-20T15:00:06Z";
    expect(networkRates(second.sample, reset)).toMatchObject({ received: null, sent: null });

    const missingCounter = resourceReading(30);
    missingCounter.captured_at = "2026-08-20T15:00:08Z";
    missingCounter.network.interfaces[0].bytes_received = null;
    const unavailable = networkRates(second.sample, missingCounter);
    expect(unavailable).toMatchObject({ received: null, sent: null });
    expect(unavailable.sample.counters).toBeNull();

    const changedInterfaceSet = resourceReading(40);
    changedInterfaceSet.captured_at = "2026-08-20T15:00:10Z";
    changedInterfaceSet.network.interfaces.push({ name: "eth1", up: true, bytes_received: 100, bytes_sent: 50 });
    expect(networkRates(second.sample, changedInterfaceSet)).toMatchObject({ received: null, sent: null });
  });

  it("does not call partial or missing storage throughput idle", () => {
    expect(storageActivityState(null, null)).toBe("Not reported");
    expect(storageActivityState(0, null)).toBe("Not reported");
    expect(storageActivityState(0, 0)).toBe("Idle");
    expect(storageActivityState(null, 1)).toBe("Active");
  });
  it("updates processor, memory, and storage from the lightweight live reading", async () => {
    vi.useFakeTimers();
    vi.spyOn(api, "overview").mockResolvedValue(overview);
    const resourceSpy = vi.spyOn(api, "resourceUsage")
      .mockResolvedValueOnce(resourceReading(10))
      .mockResolvedValueOnce(resourceReading(20));

    render(<OverviewDashboard />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(screen.getByText("10.0%")).toBeInTheDocument();
    expect(screen.getByText("50.0%")).toBeInTheDocument();
    expect(screen.getByText("30.0%")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Customize Dashboard" }));
    fireEvent.click(screen.getByRole("button", { name: "+ Network" }));
    fireEvent.click(screen.getByRole("button", { name: "+ Connected Switches & Devices" }));
    expect(screen.getByText("core-9500")).toBeInTheDocument();
    expect(screen.getByText(/FortyGigabitEthernet1\/0\/1/)).toBeInTheDocument();
    expect(screen.getByText(/source: psutil per-interface monotonic counters/)).toBeInTheDocument();
    expect(screen.getByText("No reported samples in this live session.")).toBeInTheDocument();

    await act(async () => {
      vi.advanceTimersByTime(2_000);
      await Promise.resolve();
    });

    expect(resourceSpy).toHaveBeenCalledTimes(2);
    expect(screen.getByText("20.0%")).toBeInTheDocument();
  });

  it("renders an honest empty state when storage telemetry is not reported", async () => {
    vi.spyOn(api, "overview").mockResolvedValue(overview);
    const reading = resourceReading(10);
    reading.storage.performance = null;
    vi.spyOn(api, "resourceUsage").mockResolvedValue(reading);

    render(<OverviewDashboard />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(screen.getByText("Collecting the first storage reading.")).toBeInTheDocument();
    expect(screen.queryByText("Idle")).not.toBeInTheDocument();
  });

  it("supports keyboard panel changes and persists the exact layout", async () => {
    vi.spyOn(api, "overview").mockResolvedValue(overview);
    vi.spyOn(api, "resourceUsage").mockResolvedValue(resourceReading(10));
    render(<OverviewDashboard />);
    await act(async () => { await Promise.resolve(); });

    fireEvent.click(screen.getByRole("button", { name: "Customize Dashboard" }));
    fireEvent.click(screen.getByRole("button", { name: "Move Storage earlier" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove Alerts" }));

    await waitFor(() => {
      const saved = JSON.parse(window.localStorage.getItem("hoardarr.overview.layout.v1") ?? "null") as { panels: string[] };
      expect(saved.panels).not.toContain("alerts");
      expect(saved.panels.indexOf("storage")).toBeLessThan(saved.panels.indexOf("storage-performance"));
    });
    expect(screen.getByRole("button", { name: "+ Alerts" })).toBeInTheDocument();
  });
});
