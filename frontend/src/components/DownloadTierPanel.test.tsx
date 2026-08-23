import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { StorageGroupDocument, TierTransferPlan } from "../types";
import { DownloadTierPanel } from "./DownloadTierPanel";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const groups: StorageGroupDocument[] = [{
  id: "group-media",
  name: "Media",
  namespace_path: "/data/media",
  purpose: "media",
  state: "active",
  policy: {},
  backends: [
    { id: "landing", stable_identity: "wwn:fast", physical_disk_id: "disk-fast", storage_entity_id: null, namespace_path: "/data/downloads", role: "landing", lifecycle_state: "preferred_write" },
    { id: "media", stable_identity: "wwn:media", physical_disk_id: "disk-media", storage_entity_id: null, namespace_path: "/data/media", role: "data", lifecycle_state: "active" },
  ],
  events: [],
}];

const plan: TierTransferPlan = {
  workload: "torrent",
  source: "/data/downloads/completed/example.mkv",
  destination: "/data/media/Movies/example.mkv",
  source_identity: "dev:11",
  destination_identity: "dev:22",
  same_filesystem: false,
  method: "copy",
  retain_until: "seeding_complete",
  cleanup: true,
  required_bytes: 1_073_741_824,
  completed_steps: ["download_complete"],
  sha256: "a".repeat(64),
};

describe("DownloadTierPanel", () => {
  it("keeps an honest empty state until a real landing backend exists", async () => {
    vi.spyOn(api, "storageGroups").mockResolvedValue([]);
    render(<DownloadTierPanel />);
    expect(await screen.findByText("No download SSD or NVMe is configured")).toBeInTheDocument();
    expect(screen.getByText(/until a real landing backend exists/)).toBeInTheDocument();
  });

  it("reviews cross-filesystem torrent semantics and starts real retained cleanup", async () => {
    vi.spyOn(api, "storageGroups").mockResolvedValue(groups);
    const preview = vi.spyOn(api, "previewTierTransfer").mockResolvedValue({ plan, plan_sha256: "b".repeat(64) });
    vi.spyOn(api, "applyTierTransfer").mockResolvedValue({ id: "transfer-1", kind: "storage.transfer", status: "succeeded", result: { state: "retained" } });
    const cleanupTransfer = vi.spyOn(api, "cleanupTierTransfer").mockResolvedValue({ id: "cleanup-1", kind: "storage.transfer.cleanup", status: "queued" });
    const user = userEvent.setup();
    render(<DownloadTierPanel />);

    await screen.findByDisplayValue("/data/downloads/completed/example.mkv");
    await user.click(screen.getByRole("button", { name: "Review transfer" }));
    await waitFor(() => expect(preview).toHaveBeenCalledWith(expect.objectContaining({
      workload: "torrent",
      method: "auto",
      retain_until: "seeding_complete",
      completed_steps: ["download_complete"],
    })));
    expect(await screen.findByText(/different filesystems; a hardlink is not possible/)).toBeInTheDocument();
    expect(screen.getByText("1.07 GB")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Start durable transfer" }));
    expect(await screen.findByText("Imported and retained for seeding")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Seeding complete — clean up source" }));
    await waitFor(() => expect(cleanupTransfer).toHaveBeenCalledWith("transfer-1"));
  });
});
