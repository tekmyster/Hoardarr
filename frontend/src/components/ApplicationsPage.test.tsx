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
        state: { activity: { quality: "available", active_writes: 4, downloading: 1, importing: 1, renaming: 1, moving: 1, pending: 3 }, activity_observed_at: "2026-08-23T16:00:00Z" },
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
    expect(screen.getByText(/1 downloading · 1 importing · 1 renaming · 1 moving · 3 pending/)).toBeInTheDocument();
    expect(screen.getByText("Temporarily unavailable")).toBeInTheDocument();
    expect(screen.getByText(/will not assume storage is idle/)).toBeInTheDocument();
  });

  it("shows read-only Plex libraries and preserves honestly unavailable capacity", async () => {
    vi.spyOn(api, "integrations").mockResolvedValue([{
      id: "33333333-3333-4333-8333-333333333333",
      name: "Plex",
      expected_product: "plex",
      discovered_product: "plex",
      product_version: "1.42.0",
      base_url: "http://plex:32400",
      status: "connected",
      capabilities: ["media_libraries"],
      state: { libraries: [
        { id: "movies", name: "Movies", media_type: "movie", paths: ["/data/media/Movies"], item_count: 4020, capacity_bytes: null, quality: "available" },
        { id: "tv", name: "TV", media_type: "show", paths: ["/storage/media/TV"], item_count: 1200, capacity_bytes: null, quality: "available", storage_mapping: { quality: "available", confidence: "high", source: "local_path_device_and_namespace", storage_group_name: "Media", storage_group_namespace: "/storage/media", storage_capacity_bytes: 4_000_000_000_000, storage_free_bytes: 1_000_000_000_000 } },
      ] },
      last_checked_at: "2026-08-23T16:00:00Z",
    }]);

    render(<ApplicationsPage />);

    expect(await screen.findByText("4,020 items")).toBeInTheDocument();
    expect(screen.getByText(/Storage Group not reported/)).toBeInTheDocument();
    expect(screen.getByText("Storage Group: Media")).toBeInTheDocument();
    expect(screen.getByText(/4 TB storage capacity/)).toBeInTheDocument();
    expect(screen.getByText(/Confirmed from the local namespace/)).toBeInTheDocument();
    expect(screen.getByText("/data/media/Movies")).toBeInTheDocument();
    expect(screen.getByText(/Read-only observability/)).toBeInTheDocument();
  });

  it("adds media servers without applying ARR folder recommendations", async () => {
    vi.spyOn(api, "integrations").mockResolvedValue([]);
    vi.spyOn(api, "createIntegration").mockResolvedValue({
      integration: { id: "44444444-4444-4444-8444-444444444444", name: "Jellyfin", expected_product: "jellyfin", discovered_product: null, product_version: null, base_url: "http://jellyfin:8096", status: "pending", capabilities: [], state: {}, last_checked_at: null },
      operation: { id: "op-media", kind: "media.discover", status: "queued" },
    });
    const recommendations = vi.fn();
    const user = userEvent.setup();
    render(<ApplicationsPage onRecommendations={recommendations} />);
    await screen.findByText("No applications connected");
    await user.click(screen.getAllByRole("button", { name: "Add application" }).at(-1)!);
    await user.selectOptions(screen.getByLabelText("Application"), "jellyfin");
    expect(screen.queryByRole("checkbox", { name: /Prepare torrent folders/ })).not.toBeInTheDocument();
    expect(screen.getByText(/will read library names, paths, and item counts/)).toBeInTheDocument();
    await user.type(screen.getByLabelText("Address"), "http://jellyfin:8096");
    await user.type(screen.getByLabelText("API key"), "secret-key");
    await user.click(screen.getAllByRole("button", { name: "Add application" }).at(-1)!);
    await waitFor(() => expect(api.createIntegration).toHaveBeenCalled());
    expect(recommendations).not.toHaveBeenCalled();
  });
});
