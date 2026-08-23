import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { StorageExpansionAssessment } from "../types";
import { StorageExpansionPanel } from "./StorageExpansionPanel";

const assessment: StorageExpansionAssessment = {
  schema_version: 1,
  hardware_snapshot_id: "11111111-1111-4111-8111-111111111111",
  hardware_snapshot_sha256: "a".repeat(64),
  captured_at: "2026-08-23T12:00:00Z",
  storage_groups: [{
    id: "22222222-2222-4222-8222-222222222222",
    name: "Media",
    namespace_path: "/srv/hoardarr/media",
    purpose: "media",
    backend_count: 2,
    raw_capacity_bytes: 16_000_000_000_000,
    capacity: { total_bytes: 16_000_000_000_000, used_bytes: 8_000_000_000_000, free_bytes: 8_000_000_000_000, quality: "available", source: "statvfs Storage Group namespace" },
    distribution: { reported_members: 2, minimum_utilization_percent: 40, maximum_utilization_percent: 60, spread_percentage_points: 20, methodology: "Maximum minus minimum utilization." },
    protection: { data_backends: 2, parity_backends: 1, summary: "1 parity backend configured" },
    preferred_backend_id: "33333333-3333-4333-8333-333333333333",
  }],
  available_disks: [{
    id: "44444444-4444-4444-8444-444444444444",
    stable_identity: "wwn:new-drive",
    kernel_path: "/dev/sdz",
    vendor: "Example",
    model: "Expansion Disk",
    capacity_bytes: 8_000_000_000_000,
    media_type: "hdd",
    health: "healthy",
    existing_data: { state: "none_detected", detail: "Complete scan found no signatures." },
    eligible: true,
    blockers: [],
    warnings: [],
  }],
  reserved_disks: [],
  detected_capabilities: { mergerfs: true, snapraid: true, zfs: false },
  candidates: [{
    id: "candidate-one",
    kind: "add_mergerfs_member",
    disk_ids: ["44444444-4444-4444-8444-444444444444"],
    storage_group_id: "22222222-2222-4222-8222-222222222222",
    storage_group_name: "Media",
    title: "Add capacity to Media",
    summary: "Add another independently readable member.",
    recommended: true,
    setup_mode: "expand",
    capacity: {
      raw_delta_bytes: 8_000_000_000_000,
      estimated_usable_delta_bytes: 8_000_000_000_000,
      methodology: "Independent member capacity before filesystem overhead.",
    },
    protection_impact: "SnapRAID protection must be resynchronized after the member is added.",
    future_expansion: "Different-size members can be added later.",
    migration_work: "Review, format, mount, and verify placement.",
    restrictions: ["Parity must be at least as large as the largest data disk."],
    target: { provider: "mergerfs", instance_id: "mergerfs:0123456789abcdef", mountpoint: "/srv/hoardarr/media" },
  }],
  methodology: "Read-only analysis; no changes were made.",
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("StorageExpansionPanel", () => {
  it("shows real capacity, protection, restrictions, and opens the existing wizard", async () => {
    vi.spyOn(api, "storageExpansion").mockResolvedValue(assessment);
    const onPlan = vi.fn();
    const user = userEvent.setup();
    render(<StorageExpansionPanel onPlan={onPlan} snapshotId="snapshot-one" />);

    const candidate = await screen.findByLabelText("Add capacity to Media");
    expect(screen.getByText("8 TB free of 16 TB")).toBeInTheDocument();
    expect(screen.getByText("20.0 point member-usage spread")).toBeInTheDocument();
    expect(screen.getByText("1 parity backend configured")).toBeInTheDocument();
    expect(within(candidate).getAllByText("8 TB", { exact: true })).toHaveLength(2);
    expect(screen.getByText(/SnapRAID protection must be resynchronized/)).toBeInTheDocument();
    await user.click(screen.getByText("Restrictions and calculation details"));
    expect(screen.getByText(/Parity must be at least as large/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Customize this plan" }));
    expect(onPlan).toHaveBeenCalledWith("expand", assessment.candidates[0].disk_ids, {
      candidate_id: assessment.candidates[0].id,
      kind: assessment.candidates[0].kind,
      storage_group_id: assessment.candidates[0].storage_group_id,
      hardware_snapshot_sha256: assessment.hardware_snapshot_sha256,
      disk_ids: assessment.candidates[0].disk_ids,
      target: assessment.candidates[0].target,
    });
  });

  it("renders an honest no-disk state", async () => {
    vi.spyOn(api, "storageExpansion").mockResolvedValue({
      ...assessment,
      available_disks: [],
      candidates: [],
    });
    render(<StorageExpansionPanel onPlan={vi.fn()} snapshotId="snapshot-one" />);
    expect(await screen.findByText("No unassigned disks detected")).toBeInTheDocument();
  });

  it("reconciles choices automatically after a new hardware snapshot", async () => {
    const load = vi.spyOn(api, "storageExpansion")
      .mockResolvedValueOnce({ ...assessment, available_disks: [], candidates: [] })
      .mockResolvedValueOnce(assessment);
    const view = render(<StorageExpansionPanel onPlan={vi.fn()} snapshotId="snapshot-one" />);
    expect(await screen.findByText("No unassigned disks detected")).toBeInTheDocument();
    view.rerender(<StorageExpansionPanel onPlan={vi.fn()} snapshotId="snapshot-two" />);
    expect(await screen.findByLabelText("Add capacity to Media")).toBeInTheDocument();
    await waitFor(() => expect(load).toHaveBeenCalledTimes(2));
  });

  it("reserves a disk for later and shows the persistent reserved state", async () => {
    const reserved = { ...assessment.available_disks[0] };
    vi.spyOn(api, "storageExpansion")
      .mockResolvedValueOnce(assessment)
      .mockResolvedValueOnce({ ...assessment, available_disks: [], reserved_disks: [reserved], candidates: [] });
    const reservation = vi.spyOn(api, "setDiskReservation").mockResolvedValue({
      id: reserved.id,
      stable_identity: reserved.stable_identity,
      kernel_path: reserved.kernel_path,
      serial: null,
      wwn: null,
      vendor: reserved.vendor,
      model: reserved.model,
      capacity_bytes: reserved.capacity_bytes,
      media_type: reserved.media_type,
      health_state: reserved.health,
      lifecycle_state: "reserved",
      last_seen_at: "2026-08-23T12:00:00Z",
    });
    const user = userEvent.setup();
    render(<StorageExpansionPanel onPlan={vi.fn()} snapshotId="snapshot-one" />);
    await user.click(await screen.findByRole("button", { name: "Reserve for later" }));
    await screen.findByRole("heading", { name: "Reserved for later" });
    expect(screen.getByRole("button", { name: "Release disk" })).toBeInTheDocument();
    expect(reservation).toHaveBeenCalledWith(reserved.id, "reserve");
  });
});
