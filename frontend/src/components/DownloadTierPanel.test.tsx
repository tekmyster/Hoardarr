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

const summary = {
  queue: {
    queued_count: 2, running_count: 1, failed_count: 1,
    queued_bytes: 2_147_483_648, running_planned_bytes: 536_870_912,
    retained_for_seeding_count: 1, retained_for_seeding_bytes: 1_073_741_824,
    observed_bytes_per_second: 104_857_600, rate_sample_count: 4,
    estimated_queued_seconds: 21, estimate_quality: "estimated" as const,
    estimate_methodology: "Queued bytes divided by measured completed transfers.",
  },
  tiers: [{ storage_group_id: "group-media", storage_group_name: "Media", backend_id: "landing", role: "landing" as const, path: "/data/downloads", quality: "available" as const, total_bytes: 10_737_418_240, used_bytes: 4_294_967_296, free_bytes: 6_442_450_944 }],
};

describe("DownloadTierPanel", () => {
  it("keeps an honest empty state until a real landing backend exists", async () => {
    vi.spyOn(api, "storageGroups").mockResolvedValue([]);
    vi.spyOn(api, "tierTransferSummary").mockResolvedValue({ queue: { ...summary.queue, queued_count: 0, queued_bytes: 0, estimated_queued_seconds: 0, estimate_quality: "available", estimate_methodology: "No queued transfer bytes remain." }, tiers: [] });
    render(<DownloadTierPanel />);
    expect(await screen.findByText("No download SSD or NVMe is configured")).toBeInTheDocument();
    expect(screen.getByText(/until a real landing backend exists/)).toBeInTheDocument();
  });

  it("reviews cross-filesystem torrent semantics and starts real retained cleanup", async () => {
    vi.spyOn(api, "storageGroups").mockResolvedValue(groups);
    vi.spyOn(api, "tierTransferSummary").mockResolvedValue(summary);
    const preview = vi.spyOn(api, "previewTierTransfer").mockResolvedValue({ plan, plan_sha256: "b".repeat(64) });
    vi.spyOn(api, "applyTierTransfer").mockResolvedValue({ id: "transfer-1", kind: "storage.transfer", status: "succeeded", result: { state: "retained" } });
    const cleanupTransfer = vi.spyOn(api, "cleanupTierTransfer").mockResolvedValue({ id: "cleanup-1", kind: "storage.transfer.cleanup", status: "queued" });
    const user = userEvent.setup();
    render(<DownloadTierPanel />);

    await screen.findByDisplayValue("/data/downloads/completed/example.mkv");
    expect(screen.getByText("2 · 2.15 GB")).toBeInTheDocument();
    expect(screen.getByText("About 1 min")).toBeInTheDocument();
    expect(screen.getByText(/4.29 GB used · 6.44 GB free/)).toBeInTheDocument();
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
