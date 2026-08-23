import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { StorageTopology } from "../types";
import { StorageTopologyPanels } from "./StorageTopologyPanels";

afterEach(cleanup);

describe("StorageTopologyPanels", () => {
  it("maps enclosure bays and connects supported drive actions", async () => {
    const user = userEvent.setup();
    const onDriveAction = vi.fn();
    const topology: StorageTopology = {
      status: "available",
      nodes: [
        { id: "controller:1", kind: "controller", label: "Broadcom SAS HBA", address: "0000:01:00.0", driver: "mpt3sas", protocol: "SAS", status: "detected" },
        { id: "sas_host:1", kind: "sas_host", label: "host6", address: "host6", protocol: "SAS", status: "detected" },
        { id: "phy:1", kind: "phy", label: "phy-6:2", address: "phy-6:2", protocol: "SAS", status: "detected", sas_address: "0x5000c500abcd", phy_identifier: "2", minimum_speed_gbps: 1.5, capable_speed_gbps: 12, negotiated_speed_gbps: 6, invalid_dwords: 11, disparity_errors: 7, loss_of_sync: 3, reset_problems: 1 },
        { id: "enclosure:1", kind: "enclosure", label: "NETAPP DS4246", address: "6:0:0:0", protocol: "SAS", status: "OK" },
        { id: "drive:1", kind: "drive", stable_identity: "wwn:5000c50012345678", label: "SEAGATE ST8000NM", serial: "ZA123456", path: "/dev/sdb", slot: "12", mapping_source: "sysfs enclosure_device", mapping_confidence: "high", mapping_last_confirmed_at: "2026-08-23T12:00:00Z", controller_id: "controller:1", enclosure_id: "enclosure:1", capacity_bytes: 8_000_000_000_000, used_bytes: 4_000_000_000_000, usable_bytes: 8_000_000_000_000, health_status: "healthy", smart_available: true, temperature_c: 39, protocol: "SAS", capable_speed_gbps: 12, negotiated_speed_gbps: 6 },
        { id: "pool:zfs:tank", kind: "pool", label: "tank", protocol: "Logical", status: "online", pool_type: "ZFS" },
        { id: "filesystem:zfs:tank", kind: "filesystem", label: "/mnt/hoardarr/tank", path: "/mnt/hoardarr/tank", protocol: "Logical", status: "mounted" },
        { id: "share:smb:media", kind: "share", label: "Media", path: "/mnt/hoardarr/tank/Media", status: "configured" },
      ],
      links: [
        { id: "controller:1->sas_host:1", source: "controller:1", target: "sas_host:1", protocol: "SAS", capable_speed_gbps: null, negotiated_speed_gbps: null },
        { id: "sas_host:1->phy:1", source: "sas_host:1", target: "phy:1", protocol: "SAS", capable_speed_gbps: 12, negotiated_speed_gbps: 6 },
        { id: "phy:1->enclosure:1", source: "phy:1", target: "enclosure:1", protocol: "SAS", capable_speed_gbps: 12, negotiated_speed_gbps: 12 },
        { id: "enclosure:1->drive:1", source: "enclosure:1", target: "drive:1", protocol: "SAS", capable_speed_gbps: 12, negotiated_speed_gbps: 6 },
        { id: "drive:1->pool:zfs:tank", source: "drive:1", target: "pool:zfs:tank", protocol: "Logical", capable_speed_gbps: null, negotiated_speed_gbps: null },
        { id: "pool:zfs:tank->filesystem:zfs:tank", source: "pool:zfs:tank", target: "filesystem:zfs:tank", protocol: "Logical", capable_speed_gbps: null, negotiated_speed_gbps: null },
        { id: "filesystem:zfs:tank->share:smb:media", source: "filesystem:zfs:tank", target: "share:smb:media", protocol: "Logical", capable_speed_gbps: null, negotiated_speed_gbps: null },
      ],
      enclosures: [{ id: "enclosure:1", label: "NETAPP DS4246", vendor: "NETAPP", model: "DS4246", address: "6:0:0:0", status: "OK", protocols: ["SAS"], controller_ids: ["controller:1"], bays: [{ slot: "12", drive_id: "drive:1", status: "OK", locate: true, fault: false, mapping_source: "sysfs enclosure_device", mapping_confidence: "high", mapping_last_confirmed_at: "2026-08-23T12:00:00Z" }, { slot: "13", drive_id: null, status: "Not installed", mapping_confidence: "unknown" }] }],
      direct_attached_drive_ids: [],
    };

    render(<StorageTopologyPanels topology={topology} actionableDriveIds={new Set(["wwn:5000c50012345678"])} onDriveAction={onDriveAction} />);

    expect(screen.getByRole("heading", { name: "Attached storage" })).toBeInTheDocument();
    expect(screen.getAllByText("NETAPP DS4246")).toHaveLength(2);
    expect(screen.getAllByText("ZA123456")).toHaveLength(2);
    expect(screen.getByText("50% used")).toBeInTheDocument();
    expect(screen.getByText("Empty")).toBeInTheDocument();
    expect(screen.getByText("Broadcom SAS HBA")).toBeInTheDocument();
    expect(screen.getAllByText("6 Gb/s negotiated · 12 Gb/s capable")).toHaveLength(2);
    expect(screen.getByText("Locate On")).toBeInTheDocument();
    expect(screen.getByText("Fault Off")).toBeInTheDocument();
    expect(screen.getByText("SAS PHY")).toBeInTheDocument();
    expect(screen.getByText("SAS host")).toBeInTheDocument();
    expect(screen.getAllByText("host6")).toHaveLength(2);
    expect(screen.getByText("0x5000c500abcd")).toBeInTheDocument();
    expect(screen.getByText("Invalid DWORDs").nextElementSibling).toHaveTextContent("11");
    expect(screen.getByText("Bay 12 · Confirmed")).toBeInTheDocument();
    expect(screen.getByText("Bay 13 · Not reported")).toBeInTheDocument();
    expect(screen.getByText("sysfs enclosure_device")).toBeInTheDocument();
    expect(screen.getByText("tank")).toBeInTheDocument();
    expect(screen.getByText("/mnt/hoardarr/tank/Media")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Run health tests" }));
    expect(onDriveAction).toHaveBeenCalledWith("test", "wwn:5000c50012345678");
    await user.click(screen.getByRole("button", { name: "Review existing data" }));
    expect(onDriveAction).toHaveBeenCalledWith("import", "wwn:5000c50012345678");
    const sasRails = [...document.querySelectorAll<HTMLElement>(".topology-link.protocol-sas")];
    expect(sasRails.map((item) => item.style.getPropertyValue("--link-width"))).toEqual(["2px", "4px", "6px", "4px"]);
  });

  it("renders untrusted hardware metadata as text", () => {
    const hostile = '<img src=x onerror="window.__hoardarr_xss=1">';
    const topology: StorageTopology = {
      status: "available",
      nodes: [{
        id: "controller:hostile",
        kind: "controller",
        label: hostile,
        protocol: "SAS",
        status: "detected",
      }],
      links: [],
      enclosures: [],
      direct_attached_drive_ids: [],
    };
    const { container } = render(<StorageTopologyPanels topology={topology} />);
    expect(screen.getByText(hostile)).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
  });

  it("routes a managed topology drive to the Storage Group lifecycle", async () => {
    const user = userEvent.setup();
    const onManageLifecycle = vi.fn();
    const topology: StorageTopology = {
      status: "available",
      nodes: [
        { id: "controller:1", kind: "controller", label: "HBA", protocol: "SAS" },
        { id: "drive:managed", kind: "drive", stable_identity: "wwn:managed", label: "Managed disk", protocol: "SAS", health_status: "healthy" },
      ],
      links: [{ id: "managed", source: "controller:1", target: "drive:managed", protocol: "SAS", capable_speed_gbps: null, negotiated_speed_gbps: null }],
      enclosures: [],
      direct_attached_drive_ids: [],
    };
    render(<StorageTopologyPanels topology={topology} managedDriveIds={new Set(["wwn:managed"])} onManageLifecycle={onManageLifecycle} />);
    await user.click(screen.getByRole("button", { name: "Manage lifecycle" }));
    expect(onManageLifecycle).toHaveBeenCalledOnce();
    expect(screen.queryByRole("button", { name: "Set up this drive" })).not.toBeInTheDocument();
  });
});
