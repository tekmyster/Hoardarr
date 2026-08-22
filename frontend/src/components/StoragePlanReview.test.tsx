import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { BackendStoragePlan, SelectedDriveSummary } from "../App";
import type { Drive } from "../types";

afterEach(cleanup);

const drive: Drive = {
  id: "wwn:5000c50012345678",
  path: "/dev/sdb",
  model: "ST8000NM",
  vendor: "SEAGATE",
  serial: "ZA123456",
  wwn: "5000c50012345678",
  capacityBytes: 8_000_000_000_000,
  stableIdentity: true,
  readOnly: false,
  selectable: true,
  selectionBlockers: [],
  connection: { bus: "SAS", transport: "sas/mpt3sas" },
  sector: { logical: 512, physical: 4096 },
  signatures: ["gpt"],
  partitions: [{ kernelName: "sdb1", path: "/dev/sdb1", startBytes: 1_048_576, sizeBytes: 7_999_000_000_000, filesystem: "xfs" }],
  signatureScan: { status: "complete", reason: null, source: "wipefs" },
  location: "NETAPP DS4246 bay 12",
  removable: false,
  healthStatus: "healthy",
  metrics: [],
  observations: [],
  tests: [],
};

describe("storage final review", () => {
  it("shows stable identity, geometry, existing data, layout, tests, account and exact actions", () => {
    render(<>
      <SelectedDriveSummary drives={[drive]} detailed />
      <BackendStoragePlan storage={{
        topology: "zfs",
        snapshots: true,
        encryption: "none",
        intake_tests: { identity: true, full_surface_read: true, smart_short: false },
        service_account: { username: "media" },
        file_access: { acl_model: "posix_acl" },
        format: { filesystem: "xfs", partition_table: "gpt", alignment_bytes: 1_048_576, allocation_unit_bytes: 4096 },
        risk: { destructive: true, approval_required: true, message: "Existing data will be lost." },
        actions: [
          { action_id: "test:identity", type: "drive.identity.verify", device_id: drive.id, destructive: false },
          { action_id: "partition", type: "disk.partition_table.create", device_id: drive.id, destructive: true },
          { action_id: "layout", type: "storage.layout.ensure", device_id: null, destructive: true },
        ],
        folders: ["/data/media/Movies"],
        warnings: [],
      }} />
    </>);

    for (const text of [
      "ZA123456",
      "NETAPP DS4246 bay 12",
      "zfs",
      "identity, full surface read",
      "media",
      "posix_acl",
      "disk.partition_table.create",
      "/data/media/Movies",
    ]) expect(screen.getByText(text)).toBeInTheDocument();
    expect(screen.getAllByText("/dev/sdb", { exact: false }).length).toBeGreaterThan(0);
    expect(screen.getAllByText("5000c50012345678", { exact: false }).length).toBeGreaterThan(0);
    expect(screen.getByText("512 B logical")).toBeInTheDocument();
    expect(screen.getByText("4096 B physical")).toBeInTheDocument();
    expect(screen.getAllByText("Yes").length).toBeGreaterThan(0);
  });
});
