import { cleanup, render } from "@testing-library/react";
import axe from "axe-core";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { AppShell } from "./components/AppShell";
import { AuthenticationPage } from "./components/AuthenticationPage";
import { StorageWizardDialog } from "./components/StorageWizardDialog";
import { ControllerRedundancyDetail } from "./components/ControllerRedundancyDetail";
import { api } from "./api/client";
import type { LogicalStorageDocument } from "./types";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const originalShowModal = HTMLDialogElement.prototype.showModal;
const originalClose = HTMLDialogElement.prototype.close;

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function showModal() {
    this.open = true;
  };
  HTMLDialogElement.prototype.close = function close() {
    this.open = false;
  };
});

afterAll(() => {
  HTMLDialogElement.prototype.showModal = originalShowModal;
  HTMLDialogElement.prototype.close = originalClose;
});

async function expectAccessible(container: HTMLElement): Promise<void> {
  const result = await axe.run(container, {
    rules: {
      // Color contrast requires a layout engine and is covered by browser E2E.
      "color-contrast": { enabled: false },
    },
  });
  expect(result.violations).toEqual([]);
}

describe("automated accessibility gate", () => {
  it("checks the existing-server sign-in page", async () => {
    const { container } = render(
      <AuthenticationPage
        setupStatus={{ configured: true, claim_available: false }}
        busy={false}
        error={null}
        demo={false}
        onSubmit={vi.fn()}
      />,
    );
    await expectAccessible(container);
  });

  it("checks every primary navigation destination", async () => {
    for (const page of [
      "Overview",
      "Storage",
      "Storage Access",
      "Networking",
      "Activity",
      "Health",
      "Analytics",
      "Settings",
    ] as const) {
      const view = render(
        <AppShell activePage={page} onNavigate={vi.fn()} demo={false}>
          <section aria-labelledby={`${page}-content`}>
            <h2 id={`${page}-content`}>{page} content</h2>
            <p>No data has been reported.</p>
          </section>
        </AppShell>,
      );
      await expectAccessible(view.container);
      view.unmount();
    }
  });

  it("checks the critical storage wizard dialog and controls", async () => {
    const { container } = render(
      <StorageWizardDialog
        action="add"
        mode="guided"
        busy={false}
        onModeChange={vi.fn()}
        onCancelChanges={vi.fn()}
        onSaveForLater={vi.fn()}
        onClose={vi.fn()}
      >
        <form aria-label="Storage questions">
          <label htmlFor="purpose">What will this storage hold?</label>
          <select id="purpose" defaultValue="media">
            <option value="media">Media</option>
          </select>
        </form>
      </StorageWizardDialog>,
    );
    await expectAccessible(container);
  });

  it("checks the Advanced controller redundancy topology and controls", async () => {
    vi.spyOn(api, "storageRedundancyEvents").mockResolvedValue([]);
    vi.spyOn(api, "metricEntities").mockResolvedValue([]);
    vi.spyOn(api, "currentMetrics").mockResolvedValue({
      captured_at: "2026-08-22T15:00:00Z",
      items: [],
      restricted_capabilities: [],
    });
    const storage: LogicalStorageDocument = {
      id: "11111111-1111-4111-8111-111111111111",
      name: "MediaPool",
      stable_identity: "wwn:naa.600a098000abc",
      filesystem_uuid: "22222222-2222-4222-8222-222222222222",
      mountpoint: "/media",
      presentation_device: "/dev/mapper/naa.600a098000abc",
      topology_state: "fully_redundant",
      capacity_bytes: 8_000_000_000_000,
      paths: ["a", "b"].map((name, index) => ({
        id: `${index + 3}3333333-3333-4333-8333-333333333333`,
        stable_path_identity: `fc:hba-${name}:target-${name}`,
        kernel_path: `/dev/sd${index ? "c" : "b"}`,
        protocol: "fc",
        state: "ready",
        active: true,
        optimized: index === 0,
        controller: null,
        metadata: {},
      })),
    };
    const { container } = render(
      <ControllerRedundancyDetail storage={storage} onAction={vi.fn()} />,
    );
    await expectAccessible(container);
  });
});
