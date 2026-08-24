import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { FleetPendingDocument, FleetTelemetrySettingsDocument } from "../types";
import { FleetTelemetryPanel } from "./FleetTelemetryPanel";

const settings: FleetTelemetrySettingsDocument = {
  anonymous_heartbeat: { required: true, enabled: true },
  hardware_enabled: true,
  enhanced_enabled: false,
  content_enabled: false,
  installation_id: "11111111-1111-4111-8111-111111111111",
  endpoint: "https://hoardarr.com/api/telemetry/v1",
  connection_status: "registered",
  credential_fingerprint: "1234567890abcdef",
  last_successful_upload: "2026-08-24T10:00:00Z",
  last_attempted_upload: "2026-08-24T10:00:00Z",
  last_error: null,
  schema_version: 1,
  country_code: "US",
  timezone: "America/New_York",
  location_detection_method: "manual",
  queued_records: 1,
  queued_bytes: 256,
  dead_letter_records: 0,
  by_status: { queued: { records: 1, bytes: 256 } },
  limitations: "A local administrator can modify collected data.",
};

const pending: FleetPendingDocument = {
  schema_version: 1,
  field_groups: { level_0: "Required anonymous installation heartbeat" },
  items: [{
    id: "record-1",
    message_type: "heartbeat",
    telemetry_level: 0,
    schema_version: 1,
    payload: {
      installation_id: settings.installation_id,
      hoardarr_version: "0.3.11",
      schema_version: 1,
      platform_family: "linux",
      heartbeat_at: "2026-08-24T10:00:00Z",
    },
    status: "queued",
    attempt_count: 0,
    last_error: null,
    created_at: "2026-08-24T10:00:00Z",
  }],
};

describe("FleetTelemetryPanel", () => {
  beforeEach(() => {
    vi.spyOn(api, "fleetTelemetrySettings").mockResolvedValue(settings);
    vi.spyOn(api, "fleetPendingPayloads").mockResolvedValue(pending);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("explains the required heartbeat without claiming all communication can stop", async () => {
    render(<FleetTelemetryPanel />);
    expect(await screen.findByText("Anonymous installation heartbeat is required")).toBeInTheDocument();
    expect(screen.getByLabelText(/Anonymous installation heartbeat/)).toBeChecked();
    expect(screen.getByLabelText(/Anonymous installation heartbeat/)).toBeDisabled();
    expect(screen.getByLabelText(/Enhanced diagnostics/)).not.toBeChecked();
    expect(screen.getByLabelText(/Content diagnostics/)).toBeDisabled();
  });

  it("shows the exact queued JSON and its telemetry level", async () => {
    render(<FleetTelemetryPanel />);
    await userEvent.click(await screen.findByRole("button", { name: "View exactly what is sent" }));
    expect(screen.getByText(/Required anonymous installation heartbeat/)).toBeInTheDocument();
    expect(screen.getAllByText(/11111111-1111-4111-8111-111111111111/)).toHaveLength(2);
    expect(screen.getByText(/"telemetry_level": 0/)).toBeInTheDocument();
  });

  it("enforces layered diagnostics and saves canonical location choices", async () => {
    const save = vi.spyOn(api, "saveFleetTelemetrySettings").mockResolvedValue({
      ...settings,
      enhanced_enabled: true,
    });
    render(<FleetTelemetryPanel />);
    await userEvent.click(await screen.findByLabelText(/Enhanced diagnostics/));
    expect(screen.getByLabelText(/Content diagnostics/)).toBeEnabled();
    await userEvent.clear(screen.getByLabelText("Country / Region"));
    await userEvent.type(screen.getByLabelText("Country / Region"), "ca");
    await userEvent.click(screen.getByRole("button", { name: "Save privacy settings" }));
    await waitFor(() => expect(save).toHaveBeenCalledWith(expect.objectContaining({
      hardware_enabled: true,
      enhanced_enabled: true,
      content_enabled: false,
      country_code: "CA",
      timezone: "America/New_York",
    })));
  });

  it("has no automated accessibility violations", async () => {
    const { container } = render(<FleetTelemetryPanel />);
    await screen.findByText("Telemetry & Privacy");
    expect((await axe.run(container)).violations).toEqual([]);
  });
});
