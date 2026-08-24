import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { SettingsPage } from "./SettingsPage";

describe("SettingsPage updates and add-ons", () => {
  beforeEach(() => {
    vi.spyOn(api, "backupTargets").mockResolvedValue([]);
    vi.spyOn(api, "backupRuns").mockResolvedValue([]);
    vi.spyOn(api, "webhookEndpoints").mockResolvedValue([]);
    vi.spyOn(api, "webhookEventTypes").mockResolvedValue([]);
    vi.spyOn(api, "fleetTelemetrySettings").mockResolvedValue({
      anonymous_heartbeat: { required: true, enabled: true },
      hardware_enabled: true,
      enhanced_enabled: false,
      content_enabled: false,
      installation_id: "11111111-1111-4111-8111-111111111111",
      endpoint: "https://hoardarr.com/api/telemetry/v1",
      connection_status: "unregistered",
      credential_fingerprint: null,
      last_successful_upload: null,
      last_attempted_upload: null,
      last_error: null,
      schema_version: 1,
      country_code: "US",
      timezone: "America/New_York",
      location_detection_method: "manual",
      location_confirmed: true,
      queued_records: 0,
      queued_bytes: 0,
      dead_letter_records: 0,
      by_status: {},
      limitations: "Local administrators can alter collected data.",
    });
    vi.spyOn(api, "fleetPendingPayloads").mockResolvedValue({
      schema_version: 1,
      field_groups: {},
      items: [],
    });
    vi.spyOn(api, "haStatus").mockResolvedValue({
      configured: false,
      maturity_level: "HA-2",
      mode: null,
      peer: null,
      events: [],
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("shows compatibility blockers and never offers an unsafe update", async () => {
    vi.spyOn(api, "apiKeys").mockResolvedValue([]);
    vi.spyOn(api, "addons").mockResolvedValue([]);
    vi.spyOn(api, "updateStatus").mockResolvedValue({
      current_version: "0.3.11",
      latest_version: null,
      channel: "stable",
      metadata_sha256: null,
      last_checked_at: null,
      last_error: null,
      operation: null,
    });
    vi.spyOn(api, "checkUpdates").mockResolvedValue({
      current_version: "0.3.11",
      latest_version: "0.4.0",
      channel: "stable",
      compatible: false,
      blockers: [{ code: "storage_active", message: "Storage work is currently active" }],
      required_free_bytes: 1024,
      metadata_sha256: "a".repeat(64),
    });

    render(<SettingsPage />);
    expect(await screen.findByText(/Home Assistant summary/)).toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: "Check for updates" }));
    expect(await screen.findByText("Storage work is currently active")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Install update" })).not.toBeInTheDocument();
  });

  it("polls real operation progress after starting an update", async () => {
    vi.spyOn(api, "apiKeys").mockResolvedValue([]);
    vi.spyOn(api, "addons").mockResolvedValue([]);
    vi.spyOn(api, "updateStatus").mockResolvedValue({
      current_version: "0.3.11",
      latest_version: null,
      channel: "stable",
      metadata_sha256: null,
      last_checked_at: null,
      last_error: null,
      operation: null,
    });
    vi.spyOn(api, "checkUpdates").mockResolvedValue({
      current_version: "0.3.11",
      latest_version: "0.4.0",
      channel: "stable",
      compatible: true,
      blockers: [],
      required_free_bytes: 1024,
      metadata_sha256: "b".repeat(64),
    });
    vi.spyOn(api, "applyUpdate").mockResolvedValue({ id: "update-1", kind: "update.apply", status: "queued" });
    vi.spyOn(api, "operation").mockResolvedValue({ id: "update-1", kind: "update.apply", status: "running" });
    vi.spyOn(api, "storageOperationProgress").mockResolvedValue({
      operation_id: "update-1",
      state: "running",
      phase: "Running database migrations",
      completed_steps: 0,
      total_steps: 0,
      percent: 50,
      completed_actions: [],
      notices: [],
      current_action: null,
      estimate: null,
      updated_at: null,
    });

    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Check for updates" }));
    await userEvent.click(await screen.findByRole("button", { name: "Install update" }));
    await waitFor(() => expect(screen.getByText("Running database migrations")).toBeInTheDocument());
    expect(screen.getByRole("progressbar", { name: "Update progress" })).toHaveValue(50);
  });

  it("shows a generated media password once and requires explicit dismissal", async () => {
    vi.spyOn(api, "apiKeys").mockResolvedValue([]);
    vi.spyOn(api, "addons").mockResolvedValue([]);
    vi.spyOn(api, "updateStatus").mockResolvedValue({
      current_version: "0.3.11",
      latest_version: null,
      channel: "stable",
      metadata_sha256: null,
      last_checked_at: null,
      last_error: null,
      operation: null,
    });
    vi.spyOn(api, "provisionMediaAccount").mockResolvedValue({
      account: { username: "media", created: true, password_updated: true, smb_enabled: true, shell_login: false },
      credential: { generated: true, password: "one-time-password", display_once: true },
    });

    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Create or reset account" }));
    const password = await screen.findByLabelText("Generated media account password");
    expect(password).toHaveAttribute("type", "password");
    await userEvent.click(screen.getByRole("button", { name: "Show generated password" }));
    expect(password).toHaveAttribute("type", "text");
    await userEvent.click(screen.getByRole("button", { name: "I saved this password" }));
    expect(screen.queryByDisplayValue("one-time-password")).not.toBeInTheDocument();
  });

  it("configures persistent two-node awareness without claiming automatic failover", async () => {
    vi.spyOn(api, "apiKeys").mockResolvedValue([]);
    vi.spyOn(api, "addons").mockResolvedValue([]);
    vi.spyOn(api, "updateStatus").mockResolvedValue({ current_version: "0.3.11", latest_version: null, channel: "stable", metadata_sha256: null, last_checked_at: null, last_error: null, operation: null });
    vi.spyOn(api, "saveHAConfiguration").mockResolvedValue({
      configured: true, maturity_level: "HA-3", mode: "controlled_single_writer",
      local: { node_id: "hoardarr-a", name: "Hoardarr-A", fqdn: "hoardarr-a.local", ip: "10.81.200.251", role: "active" },
      peer: { node_id: "hoardarr-b", name: "Hoardarr-B", fqdn: "hoardarr-b.local", ip: "10.81.200.252", role: "passive", reachable: false, state: "unavailable", last_seen_at: null },
      service_ip: "10.81.200.253", current_owner_node_id: "hoardarr-a", synchronization_state: "unavailable", failover_readiness: "unknown", storage_ownership: "not_reported", automatic_failover: false, fencing_configured: false, updated_at: "2026-08-24T15:00:00Z", events: [],
    });
    render(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Configure two nodes" }));
    await userEvent.type(screen.getByLabelText("IP address", { exact: true }), "10.81.200.251");
    await userEvent.type(screen.getByLabelText("Peer IP address"), "10.81.200.252");
    await userEvent.type(screen.getByLabelText("Floating/service IP (optional)"), "10.81.200.253");
    await userEvent.click(screen.getByRole("button", { name: "Save node settings" }));
    await waitFor(() => expect(api.saveHAConfiguration).toHaveBeenCalledWith(expect.objectContaining({ local_node_id: "hoardarr-a", peer_node_id: "hoardarr-b", local_ip: "10.81.200.251", peer_ip: "10.81.200.252" })));
    expect(await screen.findByText("HA-3 · Persistent peer awareness")).toBeInTheDocument();
    expect(screen.getByText("Automatic failover is not configured")).toBeInTheDocument();
  });
});
