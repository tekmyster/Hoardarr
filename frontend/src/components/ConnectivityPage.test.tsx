import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { ConnectivityPage } from "./ConnectivityPage";

describe("ConnectivityPage", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("shows a panel for every protocol and opens the selected editor", async () => {
    vi.spyOn(api, "connectivityServices").mockResolvedValue([]);
    vi.spyOn(api, "connectivityCapabilities").mockResolvedValue({
      service_available: true,
      protocols: {
        smb: { available: true },
        nfs: { available: true },
        iscsi: { available: true },
        fcoe: { available: false },
      },
      tools: {},
    });

    render(<ConnectivityPage />);

    await waitFor(() => expect(screen.getByRole("button", { name: "Add NFS storage access" })).toBeEnabled());
    expect(screen.getByRole("heading", { name: "SMB" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "iSCSI" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add FCoE storage access" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Add NFS storage access" }));
    expect(screen.getByRole("dialog", { name: "Add storage access" })).toBeInTheDocument();
    expect(screen.getByLabelText("Allowed networks")).toHaveValue("192.168.0.0/16");
  });

  it("uses detected ports for a Nexus FCoE target", async () => {
    vi.spyOn(api, "connectivityServices").mockResolvedValue([]);
    vi.spyOn(api, "connectivityCapabilities").mockResolvedValue({
      service_available: true,
      protocols: {
        smb: { available: true },
        nfs: { available: true },
        iscsi: { available: true },
        fcoe: { available: true, installed: true, online: false },
      },
      tools: {},
      fcoe_interfaces_detected: true,
      fcoe_interfaces: [{
        name: "enp5s0f0",
        driver: "i40e",
        mac: "00:11:22:33:44:55",
        state: "up",
        speed_mbps: 40000,
        target_wwpn: "20:00:00:11:22:33:44:55",
        dcb_owner: "host",
        online: false,
      }],
    });

    render(<ConnectivityPage />);

    await userEvent.click(await screen.findByRole("button", { name: "Add FCoE storage access" }));
    expect(screen.getByLabelText("Connection mode")).toHaveValue("fabric");
    expect(screen.getByLabelText("Discover FCoE VLAN automatically")).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /enp5s0f0/i })).not.toBeChecked();
  });

  it("shows configured entries with an edit action", async () => {
    vi.spyOn(api, "connectivityServices").mockResolvedValue([{
      id: "smb-media",
      protocol: "smb",
      name: "media",
      config: { path: "/mnt/media", valid_users: ["media", "viewer"], write_users: ["media"], read_users: ["viewer"] },
      status: "active",
      state: {},
      error: null,
      created_at: "2026-08-21T12:00:00Z",
      updated_at: "2026-08-21T12:00:00Z",
    }]);
    vi.spyOn(api, "connectivityCapabilities").mockResolvedValue({
      service_available: true,
      protocols: {
        smb: { available: true },
        nfs: { available: true },
        iscsi: { available: true },
        fcoe: { available: false },
      },
      tools: {},
    });

    render(<ConnectivityPage />);

    expect(await screen.findByText("/mnt/media")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Edit SMB media" }));
    expect(screen.getByRole("dialog", { name: "Edit storage access" })).toBeInTheDocument();
    expect(screen.getByLabelText("Name")).toHaveValue("media");
    expect(screen.getByLabelText("Media applications")).toHaveValue("media");
    expect(screen.getByLabelText("Media users")).toHaveValue("viewer");
  });
});
