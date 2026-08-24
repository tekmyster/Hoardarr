import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { RemoteBackupTargetDocument } from "../types";
import { RemoteBackupsPanel } from "./RemoteBackupsPanel";

function target(status = "available"): RemoteBackupTargetDocument {
  return {
    id: "target-1",
    name: "Home MinIO",
    provider: "minio",
    endpoint_url: "https://minio.example:9000",
    region: "us-east-1",
    bucket: "hoardarr-backups",
    prefix: "hoardarr",
    force_path_style: true,
    verify_tls: true,
    allow_private_network: false,
    allow_insecure_http: false,
    bandwidth_limit_mib: null,
    schedule: { enabled: false },
    credential_fingerprint: "credential-fp",
    status,
    last_tested_at: "2026-08-23T12:00:00Z",
    last_success_at: null,
    error: null,
    enabled: true,
    created_at: "2026-08-23T11:00:00Z",
    updated_at: "2026-08-23T12:00:00Z",
  };
}

describe("RemoteBackupsPanel", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("shows honest empty states", async () => {
    vi.spyOn(api, "backupTargets").mockResolvedValue([]);
    vi.spyOn(api, "backupRuns").mockResolvedValue([]);
    render(<RemoteBackupsPanel />);
    expect(await screen.findByText("No backup target")).toBeInTheDocument();
    expect(screen.getByText("No backups yet")).toBeInTheDocument();
    expect(screen.getByText(/not the media files/i)).toBeInTheDocument();
  });

  it("creates a target without ever rendering its secret", async () => {
    const create = vi.spyOn(api, "createBackupTarget").mockResolvedValue(target("untested"));
    vi.spyOn(api, "backupTargets").mockResolvedValueOnce([]).mockResolvedValue([target("untested")]);
    vi.spyOn(api, "backupRuns").mockResolvedValue([]);
    render(<RemoteBackupsPanel />);
    await userEvent.click(await screen.findByRole("button", { name: "Add backup target" }));
    await userEvent.type(screen.getByLabelText("Name"), "Home MinIO");
    await userEvent.type(screen.getByLabelText("Endpoint"), "https://minio.example:9000");
    await userEvent.type(screen.getByLabelText("Bucket"), "hoardarr-backups");
    await userEvent.type(screen.getByLabelText("Access key"), "access-key");
    await userEvent.type(screen.getByLabelText("Secret key"), "secret-value");
    await userEvent.click(screen.getByRole("button", { name: "Save target" }));
    await waitFor(() => expect(create).toHaveBeenCalledWith(expect.objectContaining({ secret_access_key: "secret-value" })));
    expect(await screen.findByText("Home MinIO")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("secret-value")).not.toBeInTheDocument();
  });

  it("requires a successful connection test before enabling backup", async () => {
    vi.spyOn(api, "backupTargets").mockResolvedValue([target("untested")]);
    vi.spyOn(api, "backupRuns").mockResolvedValue([]);
    vi.spyOn(api, "testBackupTarget").mockResolvedValue({ id: "test-1", kind: "backup.target.test", status: "succeeded" });
    render(<RemoteBackupsPanel />);
    const backup = await screen.findByRole("button", { name: "Back up now" });
    expect(backup).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Test connection" }));
    await waitFor(() => expect(api.testBackupTarget).toHaveBeenCalledWith("target-1"));
  });

  it("starts restore validation only for a successful persisted run", async () => {
    vi.spyOn(api, "backupTargets").mockResolvedValue([target()]);
    vi.spyOn(api, "backupRuns").mockResolvedValue([{
      id: "run-1",
      target_id: "target-1",
      backup_kind: "control_plane",
      object_key: "hoardarr/control-plane.tar.gz",
      artifact_sha256: "a".repeat(64),
      artifact_size_bytes: 4096,
      status: "succeeded",
      phase: "completed",
      report: {},
      error: null,
      started_at: "2026-08-23T12:00:00Z",
      completed_at: "2026-08-23T12:01:00Z",
      created_at: "2026-08-23T12:00:00Z",
      updated_at: "2026-08-23T12:01:00Z",
    }]);
    const validate = vi.spyOn(api, "validateBackupRestore").mockResolvedValue({ id: "validate-1", kind: "backup.restore.validate", status: "succeeded" });
    render(<RemoteBackupsPanel />);
    expect(await screen.findByText("Fresh appliance recovery")).toBeInTheDocument();
    expect(screen.getByText(/Scheduled remote backups are credential-redacted by default/)).toBeInTheDocument();
    expect(screen.getByText(/encrypted full-credential export is available only from the appliance console/)).toBeInTheDocument();
    expect(screen.getByText("a".repeat(64))).toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: "Validate restore" }));
    await waitFor(() => expect(validate).toHaveBeenCalledWith("run-1"));
  });

  it("persists an automatic backup schedule through the backend", async () => {
    vi.spyOn(api, "backupTargets").mockResolvedValue([target()]);
    vi.spyOn(api, "backupRuns").mockResolvedValue([]);
    const update = vi.spyOn(api, "updateBackupSchedule").mockResolvedValue({
      ...target(),
      schedule: { enabled: true, interval_hours: 24 },
    });
    render(<RemoteBackupsPanel />);
    await userEvent.click(await screen.findByRole("button", { name: "Back up every 24 hours" }));
    await waitFor(() => expect(update).toHaveBeenCalledWith("target-1", { enabled: true, interval_hours: 24 }));
    expect(await screen.findByText("Automatic: Every 24 hours")).toBeInTheDocument();
  });

  it("replaces credentials without retaining them and requires a new connection test", async () => {
    vi.spyOn(api, "backupTargets").mockResolvedValue([target()]);
    vi.spyOn(api, "backupRuns").mockResolvedValue([]);
    const rotate = vi.spyOn(api, "rotateBackupTargetCredentials").mockResolvedValue({
      ...target("untested"),
      last_tested_at: null,
      schedule: { enabled: false },
      credential_fingerprint: "replacement-fingerprint",
    });
    render(<RemoteBackupsPanel />);
    await userEvent.click(await screen.findByRole("button", { name: "Replace credentials" }));
    await userEvent.type(screen.getByLabelText("Replacement access key"), "replacement-access");
    await userEvent.type(screen.getByLabelText("Replacement secret key"), "replacement-secret");
    await userEvent.click(screen.getByRole("button", { name: "Replace and require retest" }));
    await waitFor(() => expect(rotate).toHaveBeenCalledWith("target-1", {
      access_key_id: "replacement-access",
      secret_access_key: "replacement-secret",
    }));
    expect(screen.queryByDisplayValue("replacement-secret")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Back up now" })).toBeDisabled();
  });
});
