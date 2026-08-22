import { cleanup, render } from "@testing-library/react";
import axe from "axe-core";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { AppShell } from "./components/AppShell";
import { AuthenticationPage } from "./components/AuthenticationPage";
import { StorageWizardDialog } from "./components/StorageWizardDialog";

afterEach(cleanup);

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
});
