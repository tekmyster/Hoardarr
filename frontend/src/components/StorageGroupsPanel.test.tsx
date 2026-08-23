import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { PhysicalDiskDocument, StorageGroupDocument } from "../types";
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

describe("StorageGroupsPanel", () => {
  it("shows an honest empty state and creates a stable media namespace", async () => {
    vi.spyOn(api, "storageGroups").mockResolvedValue([]);
    vi.spyOn(api, "registeredDisks").mockResolvedValue([]);
    const create = vi.spyOn(api, "createStorageGroup").mockResolvedValue(group);
    const user = userEvent.setup();
    render(<StorageGroupsPanel />);

    expect(await screen.findByText("No Storage Groups yet")).toBeInTheDocument();
    await user.click(screen.getByText("Create Storage Group"));
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
    const transition = vi.spyOn(api, "transitionStorageBackend").mockResolvedValue({
      ...group,
      backends: [{ ...group.backends[0], lifecycle_state: "active" }],
    });
    const user = userEvent.setup();
    render(<StorageGroupsPanel />);

    expect(await screen.findByText("/srv/hoardarr/media")).toBeInTheDocument();
    expect(screen.getByText("assigned")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Activate" }));
    await waitFor(() => expect(transition).toHaveBeenCalledWith(
      group.id,
      group.backends[0].id,
      "active",
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
    const preview = vi.spyOn(api, "previewStorageGroupDrain").mockResolvedValue({
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
      verification: { mode: "accurate", full_hashes: true, additional_read_pass: false },
      capacity: { required_bytes: 8_000, destination_free_bytes: 20_000, reserve_bytes: 1_073_741_824 },
      blockers: [],
      warnings: [],
      ready: true,
      phases: ["preflight", "copy", "verify"],
      plan_sha256: "a".repeat(64),
    });
    const user = userEvent.setup();
    render(<StorageGroupsPanel />);

    const drainButtons = await screen.findAllByRole("button", { name: "Preview drain" });
    await user.click(drainButtons[0]);
    await waitFor(() => expect(preview).toHaveBeenCalledWith(group.id, {
      source_backend_id: drainGroup.backends[0].id,
      destination_backend_ids: [drainGroup.backends[1].id],
      verification_mode: "accurate",
      reserve_bytes: 1_073_741_824,
    }));
    expect(screen.getByText("Drain preflight")).toBeInTheDocument();
    expect(screen.getByText(/This preview does not move or delete files/)).toBeInTheDocument();
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
