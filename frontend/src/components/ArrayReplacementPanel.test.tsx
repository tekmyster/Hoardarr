import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { ArrayReplacementPlan, Drive, StorageInventory } from "../types";
import { ArrayReplacementPanel } from "./ArrayReplacementPanel";

const drive = {
  id: "wwn:replacement", path: "/dev/sdz", model: "Replacement", vendor: "TEST", serial: "NEW",
  wwn: "replacement", capacityBytes: 2_000_000_000, stableIdentity: true, readOnly: false,
  selectable: true, selectionBlockers: [], connection: { bus: "SAS", transport: "sas" },
  sector: { logical: 512, physical: 4096 }, signatures: [], partitions: [],
  signatureScan: { status: "complete", reason: null, source: "wipefs" }, location: "Not reported",
  removable: false, healthStatus: "healthy", metrics: [], observations: [], tests: [],
} as Drive;

const inventory = {
  captured_from: "live_host", topology: { status: "not_available", nodes: [], links: [], enclosures: [], direct_attached_drive_ids: [] }, active_operations: [],
  pools: { status: "configured", items: [{
    id: "zfs:media", name: "media", type: "ZFS", status: "degraded", total_bytes: 2_000_000_000,
    used_bytes: 1_000_000_000, free_bytes: 1_000_000_000, members: null, mountpoint: "/data/media",
    pool_guid: "1234567890123456789", degraded: true,
    configuration: { quality: "available", vdev_type: "mirror", member_paths: ["/dev/disk/by-id/scsi-old", "/dev/disk/by-id/scsi-live"], member_capacities: { "/dev/disk/by-id/scsi-old": 1_000_000_000 }, config_sha256: "a".repeat(64) },
  }] }, shares: { status: "not_configured", items: [] },
  controllers: { status: "Not reported", items: [], unavailable: [] },
  enclosures: { status: "Not reported", items: [], unavailable: [] },
} as StorageInventory;

const plan = {
  schema_version: 1, kind: "array_replacement", provider: "zfs", target_id: "zfs:media",
  target_name: "media", target_identity: "1234567890123456789", configuration_sha256: "a".repeat(64),
  level: "mirror", member_count: 2, degraded: true, old_member_path: "/dev/disk/by-id/scsi-old",
  minimum_capacity_bytes: 1_000_000_000,
  device: { id: drive.id, stable_identity: true, vendor: drive.vendor, model: drive.model, serial: drive.serial, wwn: drive.wwn, eui64: null, nguid: null, capacity_bytes: drive.capacityBytes, logical_sector_bytes: 512, physical_sector_bytes: 4096 },
  device_binding_sha256: "b".repeat(64), hardware_snapshot_sha256: "c".repeat(64),
  existing_data: { detected: true, partition_count: 1, signature_types: ["ext4"], scan_status: "complete" }, destructive: true,
} as ArrayReplacementPlan;

describe("ArrayReplacementPanel", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => cleanup());

  it("reviews the real provider identity and requires exact destructive consent", async () => {
    const preview = vi.spyOn(api, "previewArrayReplacement").mockResolvedValue({ plan, plan_sha256: "d".repeat(64) });
    const apply = vi.spyOn(api, "applyArrayReplacement").mockResolvedValue({ id: "operation", kind: "storage.array.replace", status: "succeeded" });
    const user = userEvent.setup();
    render(<ArrayReplacementPanel inventory={inventory} availableDrives={[drive]} />);
    await user.selectOptions(screen.getByLabelText("Array replacement drive"), drive.id);
    await user.click(screen.getByRole("button", { name: "Review array replacement" }));
    expect(preview).toHaveBeenCalledWith({ target_id: "zfs:media", old_member_path: "/dev/disk/by-id/scsi-old", replacement_device_id: drive.id });
    expect(await screen.findByText("Existing data detected on the replacement")).toBeInTheDocument();
    expect(screen.getByText(plan.target_identity)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start durable array replacement" })).toBeDisabled();
    await user.type(screen.getByLabelText("Array replacement confirmation"), "I AGREE");
    await user.click(screen.getByRole("button", { name: "Start durable array replacement" }));
    expect(apply).toHaveBeenCalledWith(plan, "d".repeat(64));
    expect(await screen.findByText("Array replacement completed")).toBeInTheDocument();
  });

  it("offers the empty slot for a degraded Linux MD array", () => {
    render(<ArrayReplacementPanel inventory={{ ...inventory, pools: { status: "configured", items: [{ ...inventory.pools.items[0], id: "md:md0", name: "md0", type: "Linux MD raid1", pool_guid: undefined, configuration: { quality: "available", array_uuid: "abcd:1234", level: "raid1", raid_disks: 2, member_paths: ["/dev/sdb"], config_sha256: "e".repeat(64) } }] } }} availableDrives={[drive]} />);
    expect(screen.getByRole("option", { name: "Missing member / empty array slot" })).toBeInTheDocument();
  });
});
