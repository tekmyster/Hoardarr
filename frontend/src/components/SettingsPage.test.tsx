import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { SettingsPage } from "./SettingsPage";

describe("SettingsPage updates and add-ons", () => {
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
});
