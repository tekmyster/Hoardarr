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
});
