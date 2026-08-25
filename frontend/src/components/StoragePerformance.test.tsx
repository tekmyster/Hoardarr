import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { StoragePerformanceMetrics, StorageTelemetryDocument } from "../types";
import { storageLoadState, StoragePerformance } from "./StoragePerformance";

const missing: StoragePerformanceMetrics = {
  read_bytes_per_second: null,
  write_bytes_per_second: null,
  read_iops: null,
  write_iops: null,
  read_wait_ms: null,
  write_wait_ms: null,
  utilization_percent: null,
};

function reading(metrics: StoragePerformanceMetrics, at = "2026-08-25T12:00:00Z"): StorageTelemetryDocument {
  return {
    captured_at: at,
    source: "linux_block_counters",
    summary: { ...metrics, writes_today_bytes: 0, sample_seconds: 2 },
    drives: [],
    pools: [],
  };
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("StoragePerformance", () => {
  it("never derives idle from missing inputs and discloses the exact live methodology", async () => {
    vi.spyOn(api, "storageTelemetry").mockResolvedValue(reading(missing));
    render(<StoragePerformance />);
    expect(await screen.findByText("Not reported", { selector: ".storage-simple-state" })).toBeInTheDocument();
    expect(screen.queryByText("Idle", { selector: ".storage-simple-state" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByText("How this live state is calculated"));
    expect(screen.getByText(/Missing inputs are not treated as zero/)).toBeInTheDocument();
    expect(screen.getByText("linux_block_counters")).toBeInTheDocument();
    expect(screen.getAllByText("No reported samples in this live session.")).toHaveLength(3);
  });

  it("keeps an unavailable poll as a bounded graph gap instead of a zero peak", async () => {
    vi.useFakeTimers();
    const complete = { ...missing, read_bytes_per_second: 4096, write_bytes_per_second: 2048, read_iops: 8, write_iops: 4, read_wait_ms: 2, write_wait_ms: 3, utilization_percent: 10 };
    vi.spyOn(api, "storageTelemetry")
      .mockResolvedValueOnce(reading(complete))
      .mockRejectedValueOnce(new Error("collector unavailable"))
      .mockResolvedValueOnce(reading(complete, "2026-08-25T12:00:04Z"));
    render(<StoragePerformance />);
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    await act(async () => { vi.advanceTimersByTime(2_000); await Promise.resolve(); await Promise.resolve(); });
    await act(async () => { vi.advanceTimersByTime(2_000); await Promise.resolve(); await Promise.resolve(); });
    expect(screen.getByRole("img", { name: /Bandwidth, live session history with 2 unavailable values shown as gaps/ })).toBeInTheDocument();
    expect(screen.getAllByText(/up to 60 samples/)).toHaveLength(3);
  });

  it("defines load thresholds without conflating partial zero data with idle", () => {
    expect(storageLoadState({ ...missing, read_bytes_per_second: 0 }).label).toBe("Not reported");
    expect(storageLoadState({ ...missing, read_bytes_per_second: 0, write_bytes_per_second: 0 }).label).toBe("Idle");
    expect(storageLoadState({ ...missing, write_bytes_per_second: 4096 }).label).toBe("Active");
    expect(storageLoadState({ ...missing, read_wait_ms: 35 }).label).toBe("Response delay");
    expect(storageLoadState({ ...missing, write_wait_ms: 120 }).label).toBe("Severe response delay");
  });

  it("opens persistent history for the exact reported drive identity", async () => {
    const document = reading({ ...missing, read_bytes_per_second: 4096, write_bytes_per_second: 2048 });
    document.drives = [{
      id: "wwn:drive-one", device: "/dev/sdb", device_name: "sdb", model: "Media SSD", serial: "SERIAL",
      rotational: false, system_disk: false, pool_ids: [], metrics: { ...missing, read_bytes_per_second: 4096 },
      writes_today_bytes: 1024, os_write_bytes_since_boot: 2048,
      endurance: { lifetime_writes_bytes: null, remaining_percent: null, source: null },
    }];
    vi.spyOn(api, "storageTelemetry").mockResolvedValue(document);
    const open = vi.fn();
    render(<StoragePerformance onOpenHistory={open} />);
    await userEvent.click(await screen.findByRole("button", { name: "Open persistent history" }));
    expect(open).toHaveBeenCalledWith({ entityType: "drive", stableId: "wwn:drive-one", displayName: "Media SSD", metricId: "io.read.bytes_per_second", sourceSurface: "storage" });
  });
});
