import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { ApplicationsPage } from "./ApplicationsPage";

describe("ApplicationsPage", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("preselects media, torrent, and Usenet paths and lets the user change them", async () => {
    vi.spyOn(api, "integrations").mockResolvedValue([]);
    vi.spyOn(api, "createIntegration").mockResolvedValue({
      integration: {
        id: "11111111-1111-4111-8111-111111111111",
        name: "Sonarr",
        expected_product: "sonarr",
        discovered_product: null,
        product_version: null,
        base_url: "http://sonarr:8989",
        status: "pending",
        capabilities: [],
        state: {},
        last_checked_at: null,
      },
      operation: { id: "op-1", kind: "servarr.discover", status: "queued" },
    });
    const recommendations = vi.fn();
    const user = userEvent.setup();
    render(<ApplicationsPage onRecommendations={recommendations} />);
    await screen.findByText("No applications connected");
    await user.click(screen.getAllByRole("button", { name: "Add application" }).at(-1)!);

    expect(screen.getByRole("checkbox", { name: /Use the recommended media folder/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /Prepare torrent folders/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /Prepare Usenet folders/ })).toBeChecked();
    await user.click(screen.getByRole("checkbox", { name: /Prepare Usenet folders/ }));
    await user.type(screen.getByLabelText("Address"), "http://sonarr:8989");
    await user.type(screen.getByLabelText("API key"), "secret-key");
    await user.click(screen.getAllByRole("button", { name: "Add application" }).at(-1)!);

    await waitFor(() => expect(recommendations).toHaveBeenCalledWith({
      product: "sonarr",
      media: true,
      torrents: true,
      usenet: false,
    }));
  });

  it("shows real write-sensitive ARR activity and unavailable state honestly", async () => {
    vi.spyOn(api, "integrations").mockResolvedValue([
      {
        id: "11111111-1111-4111-8111-111111111111",
        name: "Sonarr",
        expected_product: "sonarr",
        discovered_product: "sonarr",
        product_version: "4.0.0",
        base_url: "http://sonarr:8989",
        status: "connected",
        capabilities: ["activity"],
        state: { activity: { quality: "available", active_writes: 2, downloading: 1, importing: 1, pending: 3 }, activity_observed_at: "2026-08-23T16:00:00Z" },
        last_checked_at: "2026-08-23T16:00:00Z",
      },
      {
        id: "22222222-2222-4222-8222-222222222222",
        name: "Radarr",
        expected_product: "radarr",
        discovered_product: "radarr",
        product_version: "5.0.0",
        base_url: "http://radarr:7878",
        status: "connected",
        capabilities: ["activity"],
        state: { activity: { quality: "temporarily_unavailable" } },
        last_checked_at: null,
      },
    ]);

    render(<ApplicationsPage />);

    expect(await screen.findByText("Storage active")).toBeInTheDocument();
    expect(screen.getByText(/1 downloading · 1 importing · 3 pending/)).toBeInTheDocument();
    expect(screen.getByText("Temporarily unavailable")).toBeInTheDocument();
    expect(screen.getByText(/will not assume storage is idle/)).toBeInTheDocument();
  });
});
