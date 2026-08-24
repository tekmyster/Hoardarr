import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { OperationDocument, StorageInventory, StorageVolumeDocument, StorageVolumePlan, StorageVolumeSnapshotPlan } from "../types";
import { StorageVolumesPanel } from "./StorageVolumesPanel";

const pools: StorageInventory["pools"]["items"] = [{
  id: "zfs:tank", name: "tank", type: "ZFS", status: "online",
  total_bytes: 100_000_000_000, used_bytes: 10_000_000_000, free_bytes: 90_000_000_000,
  members: 2, mountpoint: "/tank", pool_guid: "1234567890123456789", degraded: false,
}];

const plan: StorageVolumePlan = {
  schema_version: 1, kind: "storage.volume.create", mode: "guided", name: "media",
  purpose: "media", provider: "zfs", resource_type: "dataset",
  provider_resource_id: "tank/media", presentation: "file",
  parent: { pool_id: "zfs:tank", pool_name: "tank", pool_guid: "1234567890123456789", free_bytes_at_preview: 90_000_000_000 },
  size_bytes: null,
  properties: { compression: "zstd", recordsize: "1M", atime: "off", mountpoint: "/srv/hoardarr/volumes/media" },
  blockers: [], ready: true, explanation: "Creates a separate storage area tuned for large media files.",
  plan_sha256: "a".repeat(64),
};

const operation: OperationDocument = {
  id: "11111111-1111-4111-8111-111111111111", kind: "storage.volume.create", status: "queued",
  resource: { type: "storage_volume", id: "zfs:dataset:tank/media" },
  created_at: "2026-08-24T14:00:00Z", updated_at: "2026-08-24T14:00:00Z", result: null, error: null,
};

const snapshotOperation: OperationDocument = {
  ...operation, id: "22222222-2222-4222-8222-222222222222", kind: "storage.volume.snapshot",
};

const snapshotPlan: StorageVolumeSnapshotPlan = {
  schema_version: 1, kind: "storage.volume.snapshot", action: "create", scheduled: false,
  volume: { id: "volume-1", stable_identity: "zfs:dataset:tank/media", name: "media", provider: "zfs", resource_type: "dataset", provider_resource_id: "tank/media", provider_guid: "123456789", presentation: "file" },
  snapshot: { id: null, provider_snapshot_id: "tank/media@manual", snapshot_name: "manual", provider_guid: null },
  target_resource_id: null, confirmation: "CREATE SNAPSHOT", risk: "The live storage is not modified.", plan_sha256: "b".repeat(64),
};

const volume: StorageVolumeDocument = {
  id: "volume-1", stable_identity: "zfs:dataset:tank/media", name: "media", provider: "zfs",
  resource_type: "dataset", provider_resource_id: "tank/media", presentation: "file",
  parent_storage_entity_id: null, mountpoint: "/srv/hoardarr/volumes/media", device_path: null,
  filesystem_type: "zfs", filesystem_uuid: null, size_bytes: 90_000_000_000, allocated_bytes: 10_000,
  lifecycle_state: "active", config: {}, capabilities_detected_at: "2026-08-24T14:01:00Z",
  capabilities: {
    snapshot: { support: "supported", availability: "available", source: "provider_observation", constraints: {} },
    qos: { support: "unsupported", availability: "unsupported", source: "provider_baseline", constraints: {} },
  },
  created_at: "2026-08-24T14:00:00Z", updated_at: "2026-08-24T14:01:00Z",
};

describe("StorageVolumesPanel", () => {
  afterEach(() => cleanup());

  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "storageVolumes").mockResolvedValue([]);
    vi.spyOn(api, "previewStorageVolume").mockResolvedValue(plan);
    vi.spyOn(api, "createStorageVolume").mockResolvedValue(operation);
    vi.spyOn(api, "operation").mockResolvedValue({ ...operation, status: "succeeded", result: { volume_id: "volume-1" } });
    vi.spyOn(api, "storageVolume").mockResolvedValue({ item: volume, operations: [{ ...operation, status: "succeeded" }] });
    vi.spyOn(api, "storageVolumeSnapshots").mockResolvedValue({ items: [], schedule: { enabled: false, interval_hours: 24, retention_count: 12, prefix: "hoardarr-auto", next_run_at: null, last_run_at: null }, source: "durable_provider_operations" });
    vi.spyOn(api, "previewStorageVolumeSnapshot").mockResolvedValue(snapshotPlan);
    vi.spyOn(api, "applyStorageVolumeSnapshot").mockResolvedValue(snapshotOperation);
    vi.spyOn(api, "saveStorageVolumeSnapshotSchedule").mockResolvedValue({ enabled: true, interval_hours: 12, retention_count: 8, prefix: "media-auto", next_run_at: "2026-08-25T02:00:00Z", last_run_at: null });
  });

  it("shows an honest empty state and completes the real guided review flow", async () => {
    const user = userEvent.setup();
    render(<StorageVolumesPanel pools={pools} />);
    expect(await screen.findByText("No provider-backed storage areas registered")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Add storage area" }));
    expect(screen.getByRole("dialog", { name: "Add a storage area" })).toBeInTheDocument();
    expect(screen.getByText("Movies, TV, and media")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Review plan" }));
    expect(await screen.findByText("Recommended plan")).toBeInTheDocument();
    expect(screen.getByText("ZFS pool tank")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Create storage" }));
    await waitFor(() => expect(api.createStorageVolume).toHaveBeenCalledWith(plan));
    expect(await screen.findByText("Storage area ready")).toBeInTheDocument();
  });

  it("does not offer fake creation when no compatible provider is live", async () => {
    const user = userEvent.setup();
    render(<StorageVolumesPanel pools={[]} />);
    await screen.findByText("No provider-backed storage areas registered");
    await user.click(screen.getByRole("button", { name: "Add storage area" }));
    expect(screen.getByText("No compatible pool detected")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Review plan" })).toBeDisabled();
  });

  it("sends exact Advanced ZFS geometry to the production preview boundary", async () => {
    const user = userEvent.setup();
    render(<StorageVolumesPanel pools={pools} />);
    await screen.findByText("No provider-backed storage areas registered");
    await user.click(screen.getByRole("button", { name: "Add storage area" }));
    await user.click(screen.getByRole("checkbox", { name: /Customize ZFS settings/ }));
    await user.selectOptions(screen.getByLabelText("Resource type"), "zvol");
    await user.selectOptions(screen.getByLabelText("Compression"), "lz4");
    await user.selectOptions(screen.getByLabelText("Volume block size"), "8K");
    await user.click(screen.getByRole("checkbox", { name: /Thin\/sparse allocation/ }));
    await user.clear(screen.getByLabelText("Size (GiB)"));
    await user.type(screen.getByLabelText("Size (GiB)"), "40");
    await user.click(screen.getByRole("button", { name: "Review plan" }));
    await waitFor(() => expect(api.previewStorageVolume).toHaveBeenCalledWith(expect.objectContaining({
      advanced: true,
      resource_type: "zvol",
      compression: "lz4",
      volblocksize: "8K",
      sparse: false,
      size_bytes: 40 * 1024 ** 3,
    })));
  });

  it("shows provider capability truth and durable operation history", async () => {
    vi.mocked(api.storageVolumes).mockResolvedValue([volume]);
    const user = userEvent.setup();
    render(<StorageVolumesPanel pools={pools} />);
    await user.click(await screen.findByRole("button", { name: "Manage" }));
    const dialog = await screen.findByRole("dialog", { name: "media" });
    expect(dialog).toHaveTextContent("zfs:dataset:tank/media");
    expect(screen.getByRole("cell", { name: "snapshot" })).toBeInTheDocument();
    expect(screen.getAllByRole("cell", { name: "available" })).toHaveLength(1);
    expect(screen.getByRole("cell", { name: "qos" })).toBeInTheDocument();
    expect(screen.getAllByRole("cell", { name: "unsupported" })).toHaveLength(2);
    expect(dialog).toHaveTextContent("storage volume create");
    expect(dialog).toHaveTextContent("Snapshots and clones");
    expect(dialog).toHaveTextContent("No provider snapshots");
  });

  it("reviews a real provider snapshot and persists a bounded schedule", async () => {
    vi.mocked(api.storageVolumes).mockResolvedValue([volume]);
    const user = userEvent.setup();
    render(<StorageVolumesPanel pools={pools} />);
    await user.click(await screen.findByRole("button", { name: "Manage" }));
    await screen.findByText("No provider snapshots");
    await user.click(screen.getByRole("button", { name: "Review snapshot" }));
    expect(await screen.findByText("Review create")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "CREATE SNAPSHOT" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "CREATE SNAPSHOT" }));
    await waitFor(() => expect(api.applyStorageVolumeSnapshot).toHaveBeenCalledWith("volume-1", snapshotPlan));
    expect(await screen.findByText("Snapshot operation succeeded")).toBeInTheDocument();

    await user.click(screen.getByRole("checkbox", { name: /Keep automatic recovery points/ }));
    await user.clear(screen.getByLabelText("Every (hours)"));
    await user.type(screen.getByLabelText("Every (hours)"), "12");
    await user.clear(screen.getByLabelText("Keep latest"));
    await user.type(screen.getByLabelText("Keep latest"), "8");
    await user.clear(screen.getByLabelText("Name prefix"));
    await user.type(screen.getByLabelText("Name prefix"), "media-auto");
    await user.click(screen.getByRole("button", { name: "Save snapshot schedule" }));
    await waitFor(() => expect(api.saveStorageVolumeSnapshotSchedule).toHaveBeenCalledWith("volume-1", { enabled: true, interval_hours: 12, retention_count: 8, prefix: "media-auto" }));
  });
});
