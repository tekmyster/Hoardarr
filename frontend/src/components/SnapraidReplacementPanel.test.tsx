import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { Drive, SnapraidReplacementPlan, StorageInventory } from "../types";
import { SnapraidReplacementPanel } from "./SnapraidReplacementPanel";

const drive: Drive = {
  id: "wwn:replacement",
  path: "/dev/sdz",
  model: "Replacement",
  vendor: "TEST",
  serial: "REPLACE-ONE",
  wwn: "replacement",
  capacityBytes: 4_000_000_000,
  stableIdentity: true,
  readOnly: false,
  selectable: true,
  selectionBlockers: [],
  connection: { bus: "SAS", transport: "sas" },
  sector: { logical: 512, physical: 4096 },
  signatures: ["ext4"],
  partitions: [],
  signatureScan: { status: "complete", reason: null, source: "wipefs" },
  location: "Not reported",
  removable: false,
  healthStatus: "healthy",
  metrics: [],
  observations: [],
  tests: [],
};

const inventory = {
  captured_from: "live_host",
  topology: { status: "not_available", nodes: [], links: [], enclosures: [], direct_attached_drive_ids: [] },
  active_operations: [],
  pools: {
    status: "configured",
    items: [{
      id: "snapraid:media",
      name: "media",
      type: "SnapRAID",
      status: "degraded",
      total_bytes: null,
      used_bytes: null,
      free_bytes: null,
      members: null,
      mountpoint: null,
      configuration: {
        quality: "available",
        data_disks: [{ name: "d1", path: "/mnt/old" }],
        parity_disks: [{ level: 1, path: "/mnt/parity/snapraid.parity" }],
        content_files: ["/mnt/content/snapraid.content"],
        config_sha256: "a".repeat(64),
        errors: [],
      },
    }],
  },
  shares: { status: "not_configured", items: [] },
  controllers: { status: "Not reported", items: [], unavailable: [] },
  enclosures: { status: "Not reported", items: [], unavailable: [] },
} as StorageInventory;

const plan: SnapraidReplacementPlan = {
  schema_version: 1,
  kind: "snapraid_replacement",
  pool_name: "media",
  data_name: "d1",
  old_path: "/mnt/old",
  replacement_mount: "/mnt/hoardarr/disks/snapraid-media-d1-1234567890abcdef",
  filesystem: "ext4",
  config_sha256: "a".repeat(64),
  device: {
    id: drive.id,
    stable_identity: true,
    vendor: drive.vendor,
    model: drive.model,
    serial: drive.serial,
    wwn: drive.wwn,
    eui64: null,
    nguid: null,
    capacity_bytes: drive.capacityBytes,
    logical_sector_bytes: 512,
    physical_sector_bytes: 4096,
  },
  device_binding_sha256: "b".repeat(64),
  hardware_snapshot_sha256: "c".repeat(64),
  existing_data: {
    detected: true,
    partition_count: 1,
    signature_types: ["ext4"],
    scan_status: "complete",
  },
  destructive: true,
};

describe("SnapraidReplacementPanel", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => cleanup());

  it("reviews exact existing data and starts the durable replacement only after consent", async () => {
    const preview = vi.spyOn(api, "previewSnapraidReplacement").mockResolvedValue({
      plan,
      plan_sha256: "d".repeat(64),
    });
    const apply = vi.spyOn(api, "applySnapraidReplacement").mockResolvedValue({
      id: "operation-one",
      kind: "storage.snapraid.replace",
      status: "succeeded",
    });
    const user = userEvent.setup();
    render(<SnapraidReplacementPanel inventory={inventory} availableDrives={[drive]} />);

    await user.selectOptions(screen.getByLabelText("SnapRAID replacement drive"), drive.id);
    await user.click(screen.getByRole("button", { name: "Review replacement" }));
    expect(preview).toHaveBeenCalledWith({
      pool_name: "media",
      data_name: "d1",
      replacement_device_id: drive.id,
      filesystem: "ext4",
    });
    expect(await screen.findByText("Existing data detected on the replacement")).toBeInTheDocument();
    expect(screen.getByText("/mnt/old")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start durable replacement" })).toBeDisabled();

    await user.type(screen.getByLabelText("SnapRAID replacement confirmation"), "I AGREE");
    await user.click(screen.getByRole("button", { name: "Start durable replacement" }));
    expect(apply).toHaveBeenCalledWith(plan, "d".repeat(64));
    expect(await screen.findByText("Replacement completed")).toBeInTheDocument();
  });

  it("does not expose a destructive workflow without a safely parsed configuration", () => {
    render(<SnapraidReplacementPanel inventory={{
      ...inventory,
      pools: {
        ...inventory.pools,
        items: [{
          ...inventory.pools.items[0],
          configuration: { ...inventory.pools.items[0].configuration!, quality: "temporarily_unavailable" },
        }],
      },
    }} availableDrives={[drive]} />);

    expect(screen.getByText("SnapRAID configuration not available")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Review replacement" })).not.toBeInTheDocument();
  });
});
