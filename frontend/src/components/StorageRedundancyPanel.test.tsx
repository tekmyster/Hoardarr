import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { LogicalStorageDocument, StorageRedundancyPlan } from "../types";
import { StorageRedundancyPanel } from "./StorageRedundancyPanel";

const storage: LogicalStorageDocument = {
  id: "11111111-1111-4111-8111-111111111111",
  name: "MediaPool",
  stable_identity: "wwn:naa.600a098000abc",
  filesystem_uuid: "22222222-2222-4222-8222-222222222222",
  mountpoint: "/media",
  presentation_device: "/dev/sdb",
  topology_state: "single_path",
  capacity_bytes: 8_000_000_000_000,
  paths: [{
    id: "33333333-3333-4333-8333-333333333333",
    stable_path_identity: "fc:hba-a:50:00:hba-a",
    kernel_path: "/dev/sdb",
    protocol: "fc",
    state: "active",
    active: true,
    optimized: true,
    controller: {
      id: "44444444-4444-4444-8444-444444444444",
      stable_identity: "hba-a",
      model: "Controller A",
    },
  }],
};

const plan: StorageRedundancyPlan = {
  schema_version: 1,
  operation: "redundancy.add",
  storage_entity_id: storage.id,
  logical_storage_identity: storage.stable_identity,
  hardware_snapshot_sha256: "a".repeat(64),
  identity_binding_sha256: "b".repeat(64),
  before: {
    path_ids: [storage.paths[0].stable_path_identity],
    presentation_device: "/dev/sdb",
    mountpoint: "/media",
    device_mountpoint: "/mnt/hoardarr/lun7",
    filesystem_uuid: storage.filesystem_uuid,
  },
  after: {
    path_ids: [storage.paths[0].stable_path_identity, "fc:hba-b:50:00:hba-b"],
    presentation_device: "/dev/mapper/naa.600a098000abc",
    mountpoint: "/media",
    filesystem_uuid: storage.filesystem_uuid,
    topology_state: "fully_redundant",
  },
  selected_path: {
    stable_path_identity: "fc:hba-b:50:00:hba-b",
    kernel_path: "/dev/sdc",
    controller_identity: "hba-b",
    protocol: "fc",
  },
  policy: "recommended",
  settings: {
    mode: "recommended",
    path_grouping_policy: "group_by_prio",
    path_selector: "service-time 0",
    failback: "followover",
    no_path_retry: "fail",
    polling_interval_seconds: 5,
    minimum_healthy_paths: 2,
    alert_on_reduced: true,
    alert_on_failover: true,
    alert_on_path_flapping: true,
    alert_on_total_loss: true,
  },
  transition: {
    mode: "brief_maintenance_required",
    message: "Adding redundancy requires a brief storage interruption.",
  },
  destructive: false,
  format: false,
  copy_data: false,
  preserves: ["storage_entity_id", "filesystem_uuid", "mountpoint", "shares", "telemetry_history"],
  plan_sha256: "c".repeat(64),
};

describe("StorageRedundancyPanel", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("shows one logical storage object and reviews a non-destructive second path", async () => {
    vi.spyOn(api, "logicalStorage").mockResolvedValue([storage]);
    const preview = vi.spyOn(api, "previewStorageRedundancy").mockResolvedValue({
      plan,
      plan_sha256: plan.plan_sha256,
    });

    render(<StorageRedundancyPanel />);

    expect(await screen.findByText("Single path")).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText("Controller settings for MediaPool"));
    await userEvent.click(screen.getByRole("menuitem", { name: /Add redundant path/i }));
    expect(screen.getByRole("dialog", { name: "Add storage redundancy" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Review change" }));

    await waitFor(() => expect(preview).toHaveBeenCalledWith({
      storage_entity_id: storage.id,
      action: "add",
      policy: "recommended",
    }));
    expect(await screen.findByText("No storage contents will be rebuilt")).toBeInTheDocument();
    expect(screen.getAllByText("/media").length).toBeGreaterThan(0);
    expect(screen.getAllByText(storage.filesystem_uuid ?? "").length).toBeGreaterThan(0);
  });

  it("keeps removal advanced and warns that redundancy will be reduced", async () => {
    vi.spyOn(api, "logicalStorage").mockResolvedValue([{ ...storage, paths: [
      storage.paths[0],
      { ...storage.paths[0], id: "55555555-5555-4555-8555-555555555555", stable_path_identity: "fc:hba-b:50:00:hba-b", kernel_path: "/dev/sdc" },
    ] }]);

    render(<StorageRedundancyPanel />);
    await screen.findByText("Fully redundant");
    await userEvent.click(screen.getByLabelText("Controller settings for MediaPool"));
    await userEvent.click(screen.getByRole("menuitem", { name: /Remove redundant path/i }));
    expect(screen.getByText("Protection will be reduced")).toBeInTheDocument();
    expect(screen.getByText(/filesystem, data, storage name, media paths, and shares remain unchanged/i)).toBeInTheDocument();
  });

  it("reviews controller replacement as add-first without changing the mount", async () => {
    const redundant = { ...storage, topology_state: "fully_redundant", paths: [
      storage.paths[0],
      { ...storage.paths[0], id: "55555555-5555-4555-8555-555555555555", stable_path_identity: "fc:hba-b:50:00:hba-b", kernel_path: "/dev/sdc" },
    ] };
    const replacementPlan: StorageRedundancyPlan = {
      ...plan,
      operation: "redundancy.replace",
      before: { ...plan.before, path_ids: redundant.paths.map((path) => path.stable_path_identity) },
      after: { ...plan.after, path_ids: [storage.paths[0].stable_path_identity, "fc:hba-c:50:00:hba-c"] },
      removed_path: { stable_path_identity: "fc:hba-b:50:00:hba-b", kernel_path: "/dev/sdc" },
    };
    vi.spyOn(api, "logicalStorage").mockResolvedValue([redundant]);
    const preview = vi.spyOn(api, "previewStorageRedundancy").mockResolvedValue({
      plan: replacementPlan,
      plan_sha256: replacementPlan.plan_sha256,
    });

    render(<StorageRedundancyPanel />);
    await screen.findByText("Fully redundant");
    await userEvent.click(screen.getByLabelText("Controller settings for MediaPool"));
    await userEvent.click(screen.getByRole("menuitem", { name: /Replace controller path/i }));
    expect(screen.getByText("The replacement is added first")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Review change" }));
    await waitFor(() => expect(preview).toHaveBeenCalledWith({
      storage_entity_id: storage.id,
      action: "replace",
      remove_path_identity: storage.paths[0].stable_path_identity,
      policy: "recommended",
    }));
    expect(await screen.findByText("No storage contents will be rebuilt")).toBeInTheDocument();
    expect(screen.getAllByText("/media").length).toBeGreaterThan(0);
  });
});
