import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { ForeignInspectionPlan, ForeignMigrationPlan, ForeignStorageAssessment } from "../types";
import { ForeignStoragePanel } from "./ForeignStoragePanel";

const assessment: ForeignStorageAssessment = {
  snapshot: { id: "snapshot-1", captured_at: "2026-08-23T20:00:00Z", sha256: "a".repeat(64) },
  policy: {
    default_access: "read_only",
    automatic_mount: false,
    automatic_assembly: false,
    mutation_performed: false,
  },
  unraid_evidence: null,
  migration_destinations: [],
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
      unraid: null,
    }],
    filesystems: ["XFS"],
    signature_types: ["xfs"],
    capacity_bytes: 8_000_000_000,
    health: { quality: "not_reported", state: null, reason: "Filesystem metadata does not prove drive health." },
    warnings: ["A fresh fingerprint is required."],
    blockers: [],
    modes: [{ id: "inspect_read_only", available: true, reason: "A bounded read-only inventory can be reviewed and queued." }],
    latest_inventory: null,
    mutation_performed: false,
  }],
  unrecognized_device_count: 0,
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ForeignStoragePanel", () => {
  it("shows persisted read-only evidence without claiming a source product", async () => {
    vi.spyOn(api, "foreignStorage").mockResolvedValue(assessment);
    render(<ForeignStoragePanel />);

    expect(await screen.findByText("Standalone filesystem")).toBeInTheDocument();
    expect(screen.getByText("Read-only is the default")).toBeInTheDocument();
    expect(screen.getAllByText("Not reported").length).toBeGreaterThan(0);
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

  it("shows the durable inventory on reload and marks an older snapshot stale", async () => {
    vi.spyOn(api, "foreignStorage").mockResolvedValue({
      ...assessment,
      candidates: [{
        ...assessment.candidates[0],
        latest_inventory: {
          operation_id: "operation-1",
          completed_at: "2026-08-23T20:15:00Z",
          hardware_snapshot_sha256: "b".repeat(64),
          current_snapshot_match: false,
          filesystem: { type: "xfs", uuid: "fs-1", label: "Archive" },
          inventory: {
            file_count: 4_020,
            total_bytes: 4_100_000_000,
            largest_file: { path: "Movies/Feature.mkv", bytes: 2_000_000_000 },
            oldest_mtime_unix: 1_700_000_000,
            newest_mtime_unix: 1_710_000_000,
            extension_distribution: [{ extension: ".mkv", files: 2_018 }],
            case_collision_count: 1,
            unicode_collision_count: 2,
            read_errors: [{ path: "Unreadable.mkv" }],
            truncated: false,
          },
          access: "read_only",
          persistent_mount: false,
          mutation_performed: false,
        },
      }],
    });
    render(<ForeignStoragePanel />);

    expect(await screen.findByText("Earlier inspection report")).toBeInTheDocument();
    expect(screen.getByText("Discovery changed after this inventory")).toBeInTheDocument();
    expect(screen.getByText("4,020")).toBeInTheDocument();
    expect(screen.getByText(/Movies\/Feature.mkv/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refresh read-only inventory" })).toBeEnabled();
  });

  it("plans and completes a verified copy while preserving the read-only source", async () => {
    const user = userEvent.setup();
    const migratable: ForeignStorageAssessment = {
      ...assessment,
      migration_destinations: [{
        id: "backend-1",
        storage_group_id: "group-1",
        name: "Media",
        path: "/srv/hoardarr/media/disk2",
        stable_identity: "managed:disk2",
        lifecycle_state: "preferred_write",
        device_number: 42,
        free_bytes: 100_000_000_000,
      }],
      candidates: [{
        ...assessment.candidates[0],
        latest_inventory: {
          operation_id: "inventory-1",
          completed_at: "2026-08-23T20:15:00Z",
          hardware_snapshot_sha256: "a".repeat(64),
          current_snapshot_match: true,
          filesystem: { type: "xfs", uuid: "fs-1", label: "Archive" },
          inventory: {
            file_count: 2,
            total_bytes: 4096,
            largest_file: { path: "Movies/Feature.mkv", bytes: 4000 },
            oldest_mtime_unix: 1_700_000_000,
            newest_mtime_unix: 1_710_000_000,
            extension_distribution: [{ extension: ".mkv", files: 1 }],
            case_collision_count: 0,
            unicode_collision_count: 0,
            read_errors: [],
            truncated: false,
          },
          access: "read_only",
          persistent_mount: false,
          mutation_performed: false,
        },
      }],
    };
    const migrationPlan = {
      schema_version: 1,
      operation: "foreign.migrate_files",
      candidate_id: "foreign:one",
      hardware_snapshot_id: "snapshot-1",
      hardware_snapshot_sha256: "a".repeat(64),
      source_inventory_operation_id: "inventory-1",
      source_inventory_sha256: "b".repeat(64),
      device: { id: "wwn:archive", model: "Archive disk", capacity_bytes: 8_000_000_000 },
      device_binding_sha256: "c".repeat(64),
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
      destination: {
        ...migratable.migration_destinations[0],
        backend_id: "backend-1",
        free_bytes_at_preview: 100_000_000_000,
        reserve_bytes: 1_073_741_824,
      },
      inventory: { file_count: 2, total_bytes: 4096 },
      verification: { mode: "accurate", algorithm: "blake3" },
      collision_policy: "stop",
      source_access: "read_only",
      source_retained: true,
      parity_reuse_supported: false,
      plan_sha256: "d".repeat(64),
    } satisfies ForeignMigrationPlan;
    vi.spyOn(api, "foreignStorage").mockResolvedValue(migratable);
    const previewSpy = vi.spyOn(api, "previewForeignMigration").mockResolvedValue(migrationPlan);
    vi.spyOn(api, "startForeignMigration").mockResolvedValue({
      id: "migration-1",
      kind: "storage.foreign.migrate",
      status: "succeeded",
      result: {
        files_verified: 2,
        destination_path: "/srv/hoardarr/media/disk2",
        source_retained: true,
        parity_reused: false,
      },
    });
    render(<ForeignStoragePanel />);

    await user.click(await screen.findByRole("button", { name: "Plan verified copy" }));
    expect(await screen.findByText("This copies files; it does not adopt or erase the source")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Review copy plan" }));
    expect(previewSpy).toHaveBeenCalledWith(expect.objectContaining({
      destination_backend_id: "backend-1",
      verification_mode: "accurate",
      collision_policy: "stop",
    }));
    expect(await screen.findByText("Source data stays untouched")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "COPY AND VERIFY" }));
    expect(await screen.findByText("Copy and verification completed")).toBeInTheDocument();
    expect(screen.getByText(/source stayed read-only and remains unchanged/i)).toBeInTheDocument();
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

  it("loads assignment evidence and shows identified parity without claiming reuse", async () => {
    const user = userEvent.setup();
    const identified: ForeignStorageAssessment = {
      ...assessment,
      unraid_evidence: {
        id: "evidence-1",
        source: "unraid_runtime_state",
        document_sha256: "d".repeat(64),
        captured_at: "2026-08-23T20:00:00Z",
        unraid_version: "7.2.0",
        assignment_count: 1,
        matched_assignment_count: 1,
        unmatched_slots: [],
        ambiguous_slots: [],
      },
      candidates: [{
        ...assessment.candidates[0],
        profile: "unraid_unknown",
        profile_name: "Identified Unraid parity disk",
        filesystems: [],
        signature_types: [],
        unraid: {
          role: "parity",
          classification: "identified",
          slot: "parity",
          reason: "Stable identity matches the persisted Unraid parity assignment.",
          evidence_sha256: "d".repeat(64),
          parity_reuse_supported: false,
        },
      }],
    };
    vi.spyOn(api, "foreignStorage").mockResolvedValueOnce(assessment).mockResolvedValue(identified);
    const save = vi.spyOn(api, "saveUnraidEvidence").mockResolvedValue(identified.unraid_evidence!);
    render(<ForeignStoragePanel />);

    const input = await screen.findByLabelText("Load Unraid assignment export");
    await user.upload(input, new File([JSON.stringify({ schema_version: 1 })], "unraid.json", { type: "application/json" }));
    await waitFor(() => expect(save).toHaveBeenCalled());
    expect(await screen.findByText("Identified Unraid parity disk")).toBeInTheDocument();
    expect(screen.getByText(/Identified: parity/)).toBeInTheDocument();
    expect(screen.getByText("assignment evidence loaded")).toBeInTheDocument();
  });

  it("previews inactive Linux MD metadata without claiming assembly or health", async () => {
    const user = userEvent.setup();
    const stackAssessment: ForeignStorageAssessment = {
      ...assessment,
      candidates: [{
        ...assessment.candidates[0],
        id: "foreign:1234567890abcdef12345678",
        profile: "linux_md",
        profile_name: "Linux MD array",
        filesystems: [],
        signature_types: ["linux_raid_member"],
        members: [
          {
            ...assessment.candidates[0].members[0],
            device_id: "wwn:md-one",
            kernel_path: "/dev/sdb",
            signatures: [{ type: "linux_raid_member", usage: "raid", uuid: "md-uuid", label: null, source: "wipefs" }],
          },
          {
            ...assessment.candidates[0].members[0],
            device_id: "wwn:md-two",
            kernel_path: "/dev/sdc",
            signatures: [{ type: "linux_raid_member", usage: "raid", uuid: "md-uuid", label: null, source: "wipefs" }],
          },
        ],
        warnings: [],
        modes: [
          { id: "inspect_read_only", available: false, reason: "The stack is not a standalone filesystem." },
          { id: "preview_stack", available: true, reason: "Provider labels can be reviewed without assembly." },
        ],
      }],
    };
    vi.spyOn(api, "foreignStorage").mockResolvedValue(stackAssessment);
    const stackSpy = vi.spyOn(api, "previewForeignStack").mockResolvedValue({
      candidate_id: "foreign:1234567890abcdef12345678",
      plan_sha256: "c".repeat(64),
      provider: "linux_md",
      identity: "md-uuid",
      name: "media:0",
      layout: "raid6",
      members: [{ source: "/dev/sdb", role: 0 }, { source: "/dev/sdc", role: 1 }],
      completeness: { quality: "available", state: "incomplete", expected_members: 4, observed_members: 2, missing_members: 2 },
      health: { quality: "not_reported", state: null, reason: "Inactive metadata cannot prove health." },
      mountability: { quality: "temporarily_unavailable", state: "not_ready", reason: "Two members are missing." },
      activation_performed: false,
      mutation_performed: false,
    });
    render(<ForeignStoragePanel />);

    await user.click(await screen.findByRole("button", { name: "Review stack metadata" }));
    expect(stackSpy).toHaveBeenCalledWith("foreign:1234567890abcdef12345678");
    expect(await screen.findByText("Storage stack was not activated")).toBeInTheDocument();
    expect(screen.getByText("md-uuid")).toBeInTheDocument();
    expect(screen.getByText("2 of 4")).toBeInTheDocument();
    expect(screen.getAllByText("Not reported").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: /assemble|activate|import/i })).not.toBeInTheDocument();
  });

  it("shows a retryable error state", async () => {
    vi.spyOn(api, "foreignStorage").mockRejectedValue(new Error("Discovery required"));
    render(<ForeignStoragePanel />);

    expect(await screen.findByText("Foreign storage assessment unavailable")).toBeInTheDocument();
    expect(screen.getByText("Discovery required")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "Try again" })).toBeEnabled());
  });
});
