import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { ForeignInspectionPlan, ForeignStorageAssessment } from "../types";
import { ForeignStoragePanel } from "./ForeignStoragePanel";

const assessment: ForeignStorageAssessment = {
  snapshot: { id: "snapshot-1", captured_at: "2026-08-23T20:00:00Z", sha256: "a".repeat(64) },
  policy: {
    default_access: "read_only",
    automatic_mount: false,
    automatic_assembly: false,
    mutation_performed: false,
  },
  candidates: [{
    id: "foreign:one",
    profile: "standalone_filesystem",
    profile_name: "Standalone filesystem",
    origin: {
      name: "Not reported",
      confidence: "unknown",
      reason: "Filesystem metadata cannot identify the previous NAS.",
    },
    confidence: "high",
    state: "ready",
    members: [{
      device_id: "wwn:archive",
      kernel_path: "/dev/sdb",
      model: "Archive disk",
      capacity_bytes: 8_000_000_000,
      stable_identity: true,
      system_device: false,
      read_only: false,
      removable: true,
      mounted: false,
      mountpoints: [],
      signature_scan: { status: "partial", source: "udev", reason: "Cached evidence" },
      confidence: "high",
      signatures: [{ type: "xfs", usage: "filesystem", uuid: "fs-1", label: null, source: "udev" }],
    }],
    filesystems: ["XFS"],
    signature_types: ["xfs"],
    capacity_bytes: 8_000_000_000,
    warnings: ["A fresh fingerprint is required."],
    blockers: [],
    modes: [{ id: "inspect_read_only", available: true, reason: "A bounded read-only inventory can be reviewed and queued." }],
    mutation_performed: false,
  }],
  unrecognized_device_count: 0,
};

afterEach(() => vi.restoreAllMocks());

describe("ForeignStoragePanel", () => {
  it("shows persisted read-only evidence without claiming a source product", async () => {
    vi.spyOn(api, "foreignStorage").mockResolvedValue(assessment);
    render(<ForeignStoragePanel />);

    expect(await screen.findByText("Standalone filesystem")).toBeInTheDocument();
    expect(screen.getByText("Read-only is the default")).toBeInTheDocument();
    expect(screen.getByText("Not reported")).toBeInTheDocument();
    expect(screen.getByText("Confirmed evidence")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Review read-only inspection" })).toBeEnabled();
  });

  it("reviews, starts, and reports a real durable read-only inventory", async () => {
    const user = userEvent.setup();
    const plan: ForeignInspectionPlan = {
      schema_version: 1,
      operation: "foreign.inspect_read_only",
      candidate_id: "foreign:one",
      hardware_snapshot_id: "snapshot-1",
      hardware_snapshot_sha256: "a".repeat(64),
      device: { id: "wwn:archive", model: "Archive disk", capacity_bytes: 8_000_000_000 },
      source: {
        kind: "whole_device",
        kernel_path_at_preview: "/dev/sdb",
        partition_number: null,
        filesystem_type: "xfs",
        filesystem_uuid: "fs-1",
        filesystem_label: "Archive",
        signature_source: "wipefs",
        read_only_options: ["ro", "norecovery", "nodev", "nosuid", "noexec"],
      },
      limits: { maximum_entries: 100_000, maximum_extension_groups: 256, maximum_errors: 100 },
      access: "read_only",
      persistent_mount: false,
      automatic_activation: false,
      mutation_performed: false,
      plan_sha256: "b".repeat(64),
    };
    vi.spyOn(api, "foreignStorage").mockResolvedValue(assessment);
    vi.spyOn(api, "previewForeignInspection").mockResolvedValue(plan);
    vi.spyOn(api, "startForeignInspection").mockResolvedValue({
      id: "operation-1",
      kind: "storage.foreign.inspect",
      status: "succeeded",
      result: {
        access: "read_only",
        persistent_mount: false,
        mutation_performed: false,
        inventory: { file_count: 12, total_bytes: 4096, read_errors: [] },
      },
    });
    render(<ForeignStoragePanel />);

    await user.click(await screen.findByRole("button", { name: "Review read-only inspection" }));
    expect(await screen.findByText("No storage configuration will change")).toBeInTheDocument();
    expect(screen.getByText("100,000 entries")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "INSPECT READ ONLY" }));
    expect(await screen.findByText("Read-only inventory completed")).toBeInTheDocument();
    expect(screen.getByText(/12 files/)).toBeInTheDocument();
  });

  it("keeps unknown media honest rather than calling it empty", async () => {
    vi.spyOn(api, "foreignStorage").mockResolvedValue({
      ...assessment,
      candidates: [],
      unrecognized_device_count: 2,
    });
    render(<ForeignStoragePanel />);

    expect(await screen.findByText("No recognized foreign storage")).toBeInTheDocument();
    expect(screen.getByText(/2 non-system devices have insufficient signature evidence/i)).toBeInTheDocument();
  });

  it("shows a retryable error state", async () => {
    vi.spyOn(api, "foreignStorage").mockRejectedValue(new Error("Discovery required"));
    render(<ForeignStoragePanel />);

    expect(await screen.findByText("Foreign storage assessment unavailable")).toBeInTheDocument();
    expect(screen.getByText("Discovery required")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "Try again" })).toBeEnabled());
  });
});
