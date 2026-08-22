import { useEffect, useRef, type PropsWithChildren } from "react";
import type { WizardMode } from "../types";
import type { StorageAction } from "./StoragePage";

const ACTION_TITLES: Record<StorageAction, string> = {
  add: "Add storage",
  move: "Move data",
  change: "Change storage",
};

export function StorageWizardDialog({
  action,
  mode,
  busy,
  onModeChange,
  onCancelChanges,
  onSaveForLater,
  onClose,
  closeSavesDraft = true,
  firstRun = false,
  children,
}: PropsWithChildren<{
  action: StorageAction;
  mode: WizardMode;
  busy: boolean;
  onModeChange: (mode: WizardMode) => void;
  onCancelChanges: () => void;
  onSaveForLater: () => void;
  onClose: () => void;
  closeSavesDraft?: boolean;
  firstRun?: boolean;
}>) {
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (!dialog.open) dialog.showModal();
    return () => {
      if (dialog.open) dialog.close();
    };
  }, []);

  return <dialog
    ref={dialogRef}
    className="storage-wizard-dialog"
    aria-labelledby="storage-wizard-title"
    onCancel={(event) => {
      event.preventDefault();
      if (!busy) onClose();
    }}
  >
    <div className="storage-wizard-dialog-shell">
      <header className="storage-wizard-dialog-header">
        <div>
          <div className="eyebrow">{firstRun ? "First-time setup" : mode === "advanced" ? "Advanced storage settings" : "Guided storage change"}</div>
          <h2 id="storage-wizard-title">{firstRun ? "Set up Hoardarr" : ACTION_TITLES[action]}</h2>
          <p>{firstRun ? "Configure this server, connect it to the network, then discover storage." : mode === "advanced" ? "Review explicit device, filesystem, resiliency, cache, sharing, and controller settings." : "Answer a few questions and Hoardarr will recommend safe settings before showing the exact plan."}</p>
        </div>
        <div className="storage-wizard-dialog-actions">
          <div className="mode-control" aria-label="Storage change detail level">
            <button type="button" className={mode === "guided" ? "active" : ""} aria-pressed={mode === "guided"} onClick={() => onModeChange("guided")} disabled={busy}>Guided</button>
            <button type="button" className={mode === "advanced" ? "active" : ""} aria-pressed={mode === "advanced"} onClick={() => onModeChange("advanced")} disabled={busy}>Advanced settings</button>
          </div>
          <button type="button" className="button button-quiet" onClick={onCancelChanges} disabled={busy}>Cancel changes</button>
          {closeSavesDraft && <button type="button" className="button button-secondary" onClick={onSaveForLater} disabled={busy}>Save for later</button>}
          <button type="button" className="dialog-close" aria-label={closeSavesDraft ? "Minimize storage change" : "Close storage change"} title={closeSavesDraft ? "Minimize and save this draft for later" : "Close storage change"} onClick={onClose} disabled={busy}>×</button>
        </div>
      </header>
      <div className="storage-wizard-dialog-body">{children}</div>
    </div>
  </dialog>;
}
