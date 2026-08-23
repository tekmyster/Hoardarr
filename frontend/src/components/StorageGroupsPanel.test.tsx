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
