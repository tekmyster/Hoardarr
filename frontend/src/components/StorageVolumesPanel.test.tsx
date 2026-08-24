import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { OperationDocument, StorageInventory, StorageVolumePlan } from "../types";
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

describe("StorageVolumesPanel", () => {
  afterEach(() => cleanup());

  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "storageVolumes").mockResolvedValue([]);
    vi.spyOn(api, "previewStorageVolume").mockResolvedValue(plan);
    vi.spyOn(api, "createStorageVolume").mockResolvedValue(operation);
    vi.spyOn(api, "operation").mockResolvedValue({ ...operation, status: "succeeded", result: { volume_id: "volume-1" } });
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
});
