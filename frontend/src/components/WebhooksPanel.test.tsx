import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { WebhookDeliveryDocument, WebhookEndpointDocument } from "../types";
import { WebhooksPanel } from "./WebhooksPanel";

const endpoint: WebhookEndpointDocument = {
  id: "webhook-1",
  name: "Home Assistant",
  url: "http://127.0.0.1:9900/events",
  event_types: ["alert.opened", "alert.cleared", "test.delivery"],
  allow_localhost: true,
  verify_tls: false,
  enabled: true,
  status: "healthy",
  secret_configured: true,
  secret_fingerprint: "1234567890abcdef",
  last_success_at: "2026-08-24T03:00:00Z",
  last_error: null,
  created_at: "2026-08-24T02:00:00Z",
  updated_at: "2026-08-24T03:00:00Z",
};

const delivery: WebhookDeliveryDocument = {
  id: "delivery-1",
  endpoint_id: endpoint.id,
  event_id: "test:one",
  event_type: "test.delivery",
  status: "delivered",
  attempt_count: 1,
  next_attempt_at: "2026-08-24T03:00:00Z",
  response_status: 204,
  last_error: null,
  delivered_at: "2026-08-24T03:00:00Z",
  created_at: "2026-08-24T03:00:00Z",
  updated_at: "2026-08-24T03:00:00Z",
};

const eventTypes = [
  "alert.acknowledged",
  "alert.cleared",
  "alert.opened",
  "alert.suppressed",
  "alert.unsuppressed",
  "test.delivery",
];

describe("WebhooksPanel", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("shows an honest empty state and durable-delivery explanation", async () => {
    vi.spyOn(api, "webhookEndpoints").mockResolvedValue([]);
    vi.spyOn(api, "webhookEventTypes").mockResolvedValue(eventTypes);
    render(<WebhooksPanel />);
    expect(await screen.findByText("No webhook destination")).toBeInTheDocument();
    expect(screen.getByText(/without blocking storage jobs/i)).toBeInTheDocument();
  });

  it("creates an endpoint and removes the signing secret from the DOM", async () => {
    vi.spyOn(api, "webhookEventTypes").mockResolvedValue(eventTypes);
    vi.spyOn(api, "webhookEndpoints").mockResolvedValueOnce([]).mockResolvedValue([endpoint]);
    vi.spyOn(api, "webhookDeliveries").mockResolvedValue([]);
    const create = vi.spyOn(api, "createWebhookEndpoint").mockResolvedValue(endpoint);
    render(<WebhooksPanel />);
    await userEvent.click(await screen.findByRole("button", { name: "Add webhook" }));
    await userEvent.type(screen.getByLabelText("Name"), "Home Assistant");
    await userEvent.type(screen.getByLabelText("Webhook URL"), endpoint.url);
    const signingSecret = "a-secure-test-signing-secret-123456";
    await userEvent.type(screen.getByLabelText("Signing secret"), signingSecret);
    await userEvent.click(screen.getByRole("button", { name: "Save webhook" }));
    await waitFor(() => expect(create).toHaveBeenCalledWith(expect.objectContaining({
      secret: signingSecret,
      event_types: ["alert.opened", "alert.cleared", "test.delivery"],
    })));
    expect(await screen.findByText("Home Assistant")).toBeInTheDocument();
    expect(screen.queryByDisplayValue(signingSecret)).not.toBeInTheDocument();
  });

  it("queues a real test delivery and displays its durable status", async () => {
    vi.spyOn(api, "webhookEventTypes").mockResolvedValue(eventTypes);
    vi.spyOn(api, "webhookEndpoints").mockResolvedValue([endpoint]);
    vi.spyOn(api, "webhookDeliveries").mockResolvedValue([]);
    const test = vi.spyOn(api, "testWebhookEndpoint").mockResolvedValue({
      ...delivery,
      status: "queued",
      attempt_count: 0,
      response_status: null,
      delivered_at: null,
    });
    render(<WebhooksPanel />);
    await userEvent.click(await screen.findByRole("button", { name: "Send test" }));
    await waitFor(() => expect(test).toHaveBeenCalledWith(endpoint.id));
    expect(screen.getByText(/Latest delivery: queued/)).toBeInTheDocument();
  });

  it("replaces a signing secret without retaining it in the page", async () => {
    vi.spyOn(api, "webhookEventTypes").mockResolvedValue(eventTypes);
    vi.spyOn(api, "webhookEndpoints").mockResolvedValue([endpoint]);
    vi.spyOn(api, "webhookDeliveries").mockResolvedValue([]);
    const rotate = vi.spyOn(api, "rotateWebhookSecret").mockResolvedValue({
      ...endpoint,
      status: "not_tested",
      secret_fingerprint: "replacement-fp",
    });
    render(<WebhooksPanel />);
    await userEvent.click(await screen.findByRole("button", { name: "Replace signing secret" }));
    const replacement = "replacement-signing-secret-123456789";
    await userEvent.type(screen.getByLabelText("Replacement signing secret"), replacement);
    await userEvent.click(screen.getByRole("button", { name: "Replace signing secret" }));
    await waitFor(() => expect(rotate).toHaveBeenCalledWith(endpoint.id, replacement));
    expect(screen.queryByDisplayValue(replacement)).not.toBeInTheDocument();
  });

  it("passes automated accessibility checks", async () => {
    vi.spyOn(api, "webhookEventTypes").mockResolvedValue(eventTypes);
    vi.spyOn(api, "webhookEndpoints").mockResolvedValue([endpoint]);
    vi.spyOn(api, "webhookDeliveries").mockResolvedValue([delivery]);
    const { container } = render(<WebhooksPanel />);
    await screen.findByText("Home Assistant");
    const result = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(result.violations).toEqual([]);
  });
});
