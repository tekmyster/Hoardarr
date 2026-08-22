import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Drive, HardwareSnapshot, StorageInventory } from "../types";
import { StoragePage } from "./StoragePage";

afterEach(cleanup);

describe("StoragePage", () => {
  it("shows storage details and launches changes only from explicit actions", async () => {
    const user = userEvent.setup();
    const onAction = vi.fn();
    const onDriveAction = vi.fn();
    const onScan = vi.fn();
    render(<StoragePage snapshot={null} drives={[]} busy={false} status={null} error={null} onScan={onScan} onAction={onAction} onDriveAction={onDriveAction} />);

    expect(screen.getByRole("heading", { name: "Drives" })).toBeInTheDocument();
    expect(screen.getByText("No storage inventory yet")).toBeInTheDocument();
    expect(screen.queryByText(/Step 1 of/i)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Scan storage" }));
    await user.click(screen.getByRole("button", { name: "Add storage" }));
    await user.click(screen.getByRole("button", { name: "Move data" }));
    await user.click(screen.getByRole("button", { name: "Change storage" }));

    expect(onScan).toHaveBeenCalledTimes(1);
    expect(onAction.mock.calls.map(([action]) => action)).toEqual(["add", "move", "change"]);
  });

  it("offers lifecycle actions for an unassigned drive and hides them for a pool member", async () => {
    const user = userEvent.setup();
    const onDriveAction = vi.fn();
    const drive: Drive = {
      id: "wwn:test-drive",
      path: "/dev/sdb",
      model: "SSD-240G V01",
      vendor: "CISCO",
      serial: "STP26501RAW",
      wwn: "t10.test",
      capacityBytes: 240_057_409_536,
      stableIdentity: true,
      readOnly: false,
      selectable: true,
      selectionBlockers: [],
      connection: { bus: "USB", transport: "scsi/usb" },
      sector: { logical: 512, physical: 4096 },
      signatures: [],
      partitions: [],
      signatureScan: { status: "partial", reason: null, source: "udev" },
      location: "USB bridge",
      removable: true,
      healthStatus: "unknown",
      metrics: [],
      observations: [],
      tests: [],
    };
    const snapshot: HardwareSnapshot = {
      id: "snapshot-1",
      captured_at: "2026-08-20T19:14:52Z",
      sha256: "abc",
      hardware: {},
    };
    const common = { snapshot, drives: [drive], busy: false, status: null, error: null, onScan: vi.fn(), onAction: vi.fn(), onDriveAction };
    const { rerender } = render(<StoragePage {...common} />);

    await user.click(screen.getByLabelText("Actions for /dev/sdb"));
    expect(screen.getByRole("menuitem", { name: /Set up as storage/i })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /Run drive checks/i })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /Import existing data/i })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /Expand combined storage/i })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /Use for downloads\/cache/i })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /Advanced options/i })).toBeInTheDocument();

    await user.click(screen.getByRole("menuitem", { name: /Use for downloads\/cache/i }));
    expect(onDriveAction).toHaveBeenCalledWith("cache", drive.id);

    await user.click(screen.getByLabelText("Actions for /dev/sdb"));
    await user.click(screen.getByRole("menuitem", { name: /Expand combined storage/i }));
    expect(onDriveAction).toHaveBeenCalledWith("expand", drive.id);

    rerender(<StoragePage {...common} assignedDriveIds={new Set([drive.id])} />);
    expect(screen.queryByLabelText("Actions for /dev/sdb")).not.toBeInTheDocument();
    expect(screen.getByText("Managed")).toBeInTheDocument();

    rerender(<StoragePage {...common} reservedDriveIds={new Set([drive.id])} />);
    expect(screen.queryByLabelText("Actions for /dev/sdb")).not.toBeInTheDocument();
    expect(screen.getByText("Active build")).toBeInTheDocument();
  });

  it("shows dated saved drafts and blocks one whose drive is unavailable", async () => {
    const user = userEvent.setup();
    const onResumeDraft = vi.fn();
    const onDiscardDraft = vi.fn();
    render(<StoragePage
      snapshot={null}
      drives={[]}
      busy={false}
      status={null}
      error={null}
      onScan={vi.fn()}
      onAction={vi.fn()}
      onDriveAction={vi.fn()}
      savedDrafts={[{
        id: "draft-1",
        savedAt: "2026-08-20T21:30:00Z",
        mode: "guided",
        action: "add",
        selectedDriveIds: ["serial:missing"],
        selectedDriveLabels: [],
        available: false,
        unavailableReason: "1 selected drive is no longer detected.",
      }]}
      onResumeDraft={onResumeDraft}
      onDiscardDraft={onDiscardDraft}
    />);

    expect(screen.getByText("1 selected drive is no longer detected.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Discard" }));
    expect(onDiscardDraft).toHaveBeenCalledWith("draft-1");
    expect(onResumeDraft).not.toHaveBeenCalled();
  });

  it("shows request failures and only live discovered pools and shares", () => {
    const inventory: StorageInventory = {
      captured_from: "live_host",
      topology: { status: "available", nodes: [], links: [], enclosures: [], direct_attached_drive_ids: [] },
      active_operations: [],
      pools: { status: "configured", items: [{ id: "pool-1", name: "Media", type: "mergerfs", status: "healthy", total_bytes: 1000, used_bytes: 250, free_bytes: 750, members: 4, mountpoint: "/srv/hoardarr/media" }] },
      shares: { status: "configured", items: [{ id: "share-1", name: "Media", protocol: "smb", path: "/srv/hoardarr/media" }] },
      controllers: { status: "not_reported", items: [], unavailable: [] },
    };
    render(<StoragePage snapshot={null} drives={[]} busy={false} status={null} error="Hardware scan timed out" onScan={vi.fn()} onAction={vi.fn()} onDriveAction={vi.fn()} storageInventory={inventory} />);

    expect(screen.getByRole("alert")).toHaveTextContent("Hardware scan timed out");
    expect(screen.getAllByText("Media").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("mergerfs")).toBeInTheDocument();
    expect(screen.getAllByText("/srv/hoardarr/media")).toHaveLength(2);
    expect(screen.getByText("smb")).toBeInTheDocument();
  });
});
