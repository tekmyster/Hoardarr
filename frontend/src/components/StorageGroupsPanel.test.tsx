import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { LogicalStorageDocument, PhysicalDiskDocument, StorageBackendActivationPlan, StorageDrainPlan, StorageGroupDocument } from "../types";
import { StorageGroupsPanel } from "./StorageGroupsPanel";

const disk: PhysicalDiskDocument = {
  id: "11111111-1111-4111-8111-111111111111",
  stable_identity: "wwn:5000c500feed0001",
  kernel_path: "/dev/sdb",
  serial: "SANITIZED-0001",
  wwn: "5000c500feed0001",
  vendor: "Example",
  model: "Media Disk",
  capacity_bytes: 8_000_000_000_000,
  media_type: "hdd",
  health_state: "healthy",
  lifecycle_state: "discovered",
  last_seen_at: "2026-08-23T12:00:00Z",
};

const group: StorageGroupDocument = {
  id: "22222222-2222-4222-8222-222222222222",
  name: "Media",
  namespace_path: "/srv/hoardarr/media",
  purpose: "media",
  state: "active",
  policy: { placement: "preferred_then_available" },
  backends: [{
    id: "33333333-3333-4333-8333-333333333333",
    stable_identity: disk.stable_identity,
    physical_disk_id: disk.id,
    storage_entity_id: null,
    namespace_path: "/srv/hoardarr/backends/a",
    role: "data",
    lifecycle_state: "assigned",
  }],
  events: [{
    id: "44444444-4444-4444-8444-444444444444",
    event_type: "backend_assigned",
    backend_id: "33333333-3333-4333-8333-333333333333",
    previous_state: null,
    resulting_state: "assigned",
    reason: null,
    occurred_at: "2026-08-23T12:00:00Z",
  }],
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

beforeEach(() => {
  vi.spyOn(api, "logicalStorage").mockResolvedValue([]);
});

describe("StorageGroupsPanel", () => {
  it("attaches a completed logical pool and hides its managed member disks", async () => {
    const managedMember = { ...disk, lifecycle_state: "managed_member" };
    const storage: LogicalStorageDocument = {
      id: "88888888-8888-4888-8888-888888888888",
      name: "media-library",
      stable_identity: "mergerfs:0123456789abcdef",
      storage_kind: "mergerfs",
      provider: "mergerfs",
      redundancy_capable: false,
      filesystem_uuid: null,
      mountpoint: "/data",
      presentation_device: "/mnt/hoardarr/media",
      topology_state: "not_applicable",
      capacity_bytes: 24_000_000_000,
      paths: [],
    };
    vi.mocked(api.logicalStorage).mockResolvedValue([storage]);
    vi.spyOn(api, "storageGroups").mockResolvedValue([group]);
    vi.spyOn(api, "registeredDisks").mockResolvedValue([managedMember]);
    const attach = vi.spyOn(api, "assignStorageGroupEntity").mockResolvedValue(group);
    const user = userEvent.setup();
    render(<StorageGroupsPanel />);

    const chooser = await screen.findByLabelText("Managed storage to add to Media");
    expect(screen.queryByRole("option", { name: /Media Disk/ })).not.toBeInTheDocument();
    await user.selectOptions(chooser, storage.id);
    await user.click(screen.getByRole("button", { name: "Attach managed storage" }));
    await waitFor(() => expect(attach).toHaveBeenCalledWith(group.id, storage.id, "/data"));
  });

  it("shows an honest empty state and creates a stable media namespace", async () => {
    vi.spyOn(api, "storageGroups").mockResolvedValue([]);
    vi.spyOn(api, "registeredDisks").mockResolvedValue([]);
    const create = vi.spyOn(api, "createStorageGroup").mockResolvedValue(group);
    const user = userEvent.setup();
    render(<StorageGroupsPanel />);

    expect(await screen.findByText("No Storage Groups yet")).toBeInTheDocument();
    const createToggle = screen.getByRole("button", { name: "Close Storage Group form" });
    expect(createToggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByLabelText("Name")).toBeInTheDocument();
    await user.click(createToggle);
    expect(createToggle).toHaveAttribute("aria-expanded", "false");
    expect(createToggle).toHaveAccessibleName("Create Storage Group");
    await user.click(createToggle);
    expect(createToggle).toHaveAttribute("aria-expanded", "true");
    await user.type(screen.getByLabelText("Name"), "Media");
    await user.click(screen.getByRole("button", { name: "Create group" }));

    await waitFor(() => expect(create).toHaveBeenCalledWith({
      name: "Media",
      namespace_path: "/srv/hoardarr/media",
      purpose: "media",
    }));
  });

  it("renders lifecycle state and activates an assigned backend", async () => {
    vi.spyOn(api, "storageGroups").mockResolvedValue([group]);
    vi.spyOn(api, "registeredDisks").mockResolvedValue([disk]);
    const plan: StorageBackendActivationPlan = {
      schema_version: 1,
      kind: "storage.backend.activate",
      storage_group_id: group.id,
      storage_group_namespace: group.namespace_path,
      backend_id: group.backends[0].id,
      stable_identity: group.backends[0].stable_identity,
      lifecycle_state: "assigned",
      health: "healthy",
      evidence: {
        path: "/srv/hoardarr/backends/a",
        filesystem_device: 101,
        mount_source: "/dev/sdb1",
        exact_mount: true,
        identity_match: true,
        identity_basis: "mounted source belongs to the registered disk",
        total_bytes: 8_000_000_000_000,
        free_bytes: 7_000_000_000_000,
      },
      blockers: [],
      ready: true,
      plan_sha256: "a".repeat(64),
    };
    vi.spyOn(api, "previewStorageBackendActivation").mockResolvedValue(plan);
    const activate = vi.spyOn(api, "activateStorageBackend").mockResolvedValue({
      ...group,
      backends: [{ ...group.backends[0], lifecycle_state: "active" }],
    });
    const user = userEvent.setup();
    render(<StorageGroupsPanel />);

    expect(await screen.findByText("/srv/hoardarr/media")).toBeInTheDocument();
    expect(screen.getByText("assigned")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Review activation" }));
    expect(await screen.findByText("Matches assigned storage")).toBeInTheDocument();
    expect(screen.getByText(/does not format, mount, or write/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Activate verified storage" }));
    await waitFor(() => expect(activate).toHaveBeenCalledWith(plan));
  });

  it("releases only a verified retired assignment after explicit confirmation", async () => {
    const retiredGroup: StorageGroupDocument = {
      ...group,
      backends: [{ ...group.backends[0], lifecycle_state: "retired" }],
    };
    vi.spyOn(api, "storageGroups")
      .mockResolvedValueOnce([retiredGroup])
      .mockResolvedValueOnce([{ ...retiredGroup, backends: [] }]);
    vi.spyOn(api, "registeredDisks")
      .mockResolvedValueOnce([{ ...disk, lifecycle_state: "retired" }])
      .mockResolvedValueOnce([{ ...disk, lifecycle_state: "reuse_ready" }]);
    const release = vi.spyOn(api, "releaseRetiredStorageBackend").mockResolvedValue({
      item: { ...retiredGroup, backends: [] },
      disk: { ...disk, lifecycle_state: "reuse_ready" },
    });
    const user = userEvent.setup();
    render(<StorageGroupsPanel />);

    await user.click(await screen.findByRole("button", { name: "Release retired disk" }));
    expect(screen.getByText(/does not erase, format, mount, or wipe/)).toBeInTheDocument();
    const confirm = screen.getByLabelText("Release retired disk confirmation");
    expect(screen.getByRole("button", { name: "Release for reuse" })).toBeDisabled();
    await user.type(confirm, "RELEASE");
    await user.click(screen.getByRole("button", { name: "Release for reuse" }));
    await waitFor(() => expect(release).toHaveBeenCalledWith(
      retiredGroup.id,
      retiredGroup.backends[0].id,
      "Verified drain complete; operator released the retired disk for reuse.",
    ));
  });

  it("shows a real immutable drain preflight without implying that files moved", async () => {
    const drainGroup: StorageGroupDocument = {
      ...group,
      backends: [
        { ...group.backends[0], lifecycle_state: "preferred_write" },
        {
          ...group.backends[0],
          id: "55555555-5555-4555-8555-555555555555",
          stable_identity: "disk:wwn:destination",
          physical_disk_id: "66666666-6666-4666-8666-666666666666",
          namespace_path: "/srv/hoardarr/backends/b",
          lifecycle_state: "active",
        },
      ],
    };
    vi.spyOn(api, "storageGroups").mockResolvedValue([drainGroup]);
    vi.spyOn(api, "registeredDisks").mockResolvedValue([]);
    const drainPlan: StorageDrainPlan = {
      schema_version: 1,
      kind: "storage.drain",
      storage_group_id: group.id,
      storage_group_namespace: group.namespace_path,
      source: {
        backend_id: drainGroup.backends[0].id,
        stable_identity: drainGroup.backends[0].stable_identity,
        path: drainGroup.backends[0].namespace_path!,
        filesystem_device: 101,
        required_bytes: 8_000,
        health: "healthy",
        lifecycle_state: "preferred_write",
      },
      destinations: [{
        backend_id: drainGroup.backends[1].id,
        stable_identity: drainGroup.backends[1].stable_identity,
        path: drainGroup.backends[1].namespace_path!,
        filesystem_device: 202,
        free_bytes: 20_000,
        total_bytes: 30_000,
        health: "healthy",
      }],
      verification: { mode: "accurate", full_hashes: true, additional_read_pass: false, algorithm: "blake3" },
      capacity: { required_bytes: 8_000, destination_free_bytes: 20_000, reserve_bytes: 1_073_741_824 },
      controls: {
        enforce_source_read_only: true,
        source_read_only_capability: { supported: true, currently_read_only: false, reason: "Exact test mount." },
        bandwidth_limit_mib_per_second: 64,
        io_priority: "normal",
        start_at: null,
        maintenance_window_minutes: null,
        maintenance_window_end: null,
      },
      blockers: [],
      warnings: [],
      ready: true,
      phases: ["preflight", "copy", "verify"],
      plan_sha256: "a".repeat(64),
    };
    const preview = vi.spyOn(api, "previewStorageGroupDrain").mockResolvedValue(drainPlan);
    const startedOperation = {
      id: "77777777-7777-4777-8777-777777777777",
      kind: "storage.drain",
      status: "queued" as const,
    };
    const start = vi.spyOn(api, "startStorageGroupDrain").mockResolvedValue(startedOperation);
    vi.spyOn(api, "operation").mockResolvedValue({ ...startedOperation, status: "paused" });
    vi.spyOn(api, "storageOperationProgress").mockResolvedValue({
      operation_id: startedOperation.id,
      state: "paused",
      phase: "copying",
      completed_steps: 0,
      total_steps: 2,
      percent: 35,
      completed_actions: [],
      notices: [],
      current_action: null,
      estimate: null,
      updated_at: 1,
      files: { total: 2, copied: 1, verified: 0 },
    });
    const user = userEvent.setup();
    render(<StorageGroupsPanel />);

    await user.click(await screen.findByText("Drain scheduling and limits"));
    await user.clear(screen.getByLabelText("Copy speed limit (MiB/s)"));
    await user.type(screen.getByLabelText("Copy speed limit (MiB/s)"), "64");
    await user.click(screen.getByLabelText("Temporarily enforce a read-only source mount"));
    const drainButtons = await screen.findAllByRole("button", { name: "Preview drain" });
    await user.click(drainButtons[0]);
    await waitFor(() => expect(preview).toHaveBeenCalledWith(group.id, {
      source_backend_id: drainGroup.backends[0].id,
      destination_backend_ids: [drainGroup.backends[1].id],
      verification_mode: "accurate",
      reserve_bytes: 1_073_741_824,
      enforce_source_read_only: true,
      bandwidth_limit_mib_per_second: 64,
      io_priority: "normal",
      start_at: null,
      maintenance_window_minutes: null,
    }));
    expect(screen.getByText("Drain preflight")).toBeInTheDocument();
    expect(screen.getByText(/This preview does not move or delete files/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start durable drain" })).toBeDisabled();
    await user.type(screen.getByLabelText("Drain destructive confirmation"), "I AGREE");
    await user.click(screen.getByRole("button", { name: "Start durable drain" }));
    await waitFor(() => expect(start).toHaveBeenCalledWith(drainPlan));
    expect(await screen.findByText("Drain and retire source")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "Resume drain" })).toBeInTheDocument());
  });

  it("aborts initial history requests when the panel unmounts", () => {
    const observed: AbortSignal[] = [];
    vi.spyOn(api, "storageGroups").mockImplementation((signal) => {
      if (signal) observed.push(signal);
      return new Promise(() => undefined);
    });
    vi.spyOn(api, "registeredDisks").mockImplementation((signal) => {
      if (signal) observed.push(signal);
      return new Promise(() => undefined);
    });
    const view = render(<StorageGroupsPanel />);
    view.unmount();
    expect(observed).toHaveLength(2);
    expect(observed.every((signal) => signal.aborted)).toBe(true);
  });
});
