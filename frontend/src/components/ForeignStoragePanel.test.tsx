import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { ForeignStorageAssessment } from "../types";
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
    confidence: "medium",
    state: "degraded-review",
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
      confidence: "medium",
      signatures: [{ type: "xfs", usage: "filesystem", uuid: "fs-1", label: null, source: "udev" }],
    }],
    filesystems: ["XFS"],
    signature_types: ["xfs"],
    capacity_bytes: 8_000_000_000,
    warnings: ["A fresh fingerprint is required."],
    blockers: [],
    modes: [{ id: "inspect_read_only", available: false, reason: "Review first" }],
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
    expect(screen.getByText("Partial evidence")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Read-only inspection plan" })).toBeDisabled();
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
