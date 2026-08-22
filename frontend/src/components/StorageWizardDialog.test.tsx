import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { StorageWizardDialog } from "./StorageWizardDialog";

beforeEach(() => {
  HTMLDialogElement.prototype.showModal = vi.fn(function showModal(this: HTMLDialogElement) {
    this.setAttribute("open", "");
  });
  HTMLDialogElement.prototype.close = vi.fn(function close(this: HTMLDialogElement) {
    this.removeAttribute("open");
  });
});

afterEach(cleanup);

describe("StorageWizardDialog", () => {
  it("keeps discard, persistent save, and minimize as separate actions", async () => {
    const user = userEvent.setup();
    const onCancelChanges = vi.fn();
    const onSaveForLater = vi.fn();
    const onClose = vi.fn();
    render(<StorageWizardDialog action="add" mode="guided" busy={false} onModeChange={vi.fn()} onCancelChanges={onCancelChanges} onSaveForLater={onSaveForLater} onClose={onClose}><p>Wizard content</p></StorageWizardDialog>);

    await user.click(screen.getByRole("button", { name: "Cancel changes" }));
    await user.click(screen.getByRole("button", { name: "Save for later" }));
    await user.click(screen.getByRole("button", { name: "Minimize storage change" }));

    expect(onCancelChanges).toHaveBeenCalledOnce();
    expect(onSaveForLater).toHaveBeenCalledOnce();
    expect(onClose).toHaveBeenCalledOnce();
  });
});
